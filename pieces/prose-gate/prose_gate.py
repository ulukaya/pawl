#!/usr/bin/env python3
"""prose_gate.py: scores a draft for machine-sounding prose and fails above a threshold.

One file plus a JSON catalog of regex tells. Each tell has a per-1000-word tolerance, a
weight, and a family. The score is the weighted excess over tolerance, plus a penalty when
too many distinct families fire at once. A few tells have tolerance zero: one hit fails the
draft outright. Code fences, URLs, quotes, and italics are blanked before scoring so quoted
material is never charged to the author.

    prose_gate.py --plane chat        draft.md         # exit 0 pass, 1 fail
    prose_gate.py --plane deliverable post.md --explain
    echo "text" | prose_gate.py --plane chat --why
    prose_gate.py --plane chat draft.md --json

Planes: chat (short replies, threshold 3.0, 2 families free) and deliverable (docs and
posts, threshold 6.0, 4 families free). Thresholds live in prose_patterns.json, not here.

Environment:
    PROSE_GATE_PATTERNS   path to the catalog (default: prose_patterns.json beside this file)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

WORD_FLOOR = 250  # short drafts are scored as if they were 250 words so one hit cannot dominate


def catalog_path() -> Path:
    return Path(os.environ.get("PROSE_GATE_PATTERNS", Path(__file__).resolve().parent / "prose_patterns.json"))


def load_catalog() -> Dict[str, Any]:
    path = catalog_path()
    data = json.loads(path.read_text())
    for p in data["patterns"]:
        for key in ("code", "pattern", "threshold_per_1000w", "weight", "plane", "family"):
            if key not in p:
                raise ValueError(f"pattern {p.get('code')!r} is missing {key}")
        p["regex"] = re.compile(p["pattern"])
    return data


def _blank(match: "re.Match[str]") -> str:
    return " " * (match.end() - match.start())


def clean(text: str) -> str:
    """Blanks spans the author did not write in their own voice, preserving offsets."""
    text = re.sub(r"\A\s*---\n.*?\n---(?:\n|$)", _blank, text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", _blank, text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", _blank, text)
    text = re.sub(r"`[^`\n]+`", _blank, text)
    text = re.sub(r'"[^"\n]+"', _blank, text)
    text = re.sub(r"(?<!\w)\*[^*\n]+\*(?!\w)", _blank, text)
    text = re.sub(r"(?<!\w)_[^_\n]+_(?!\w)", _blank, text)
    text = re.sub(r"(?m)^\s*>.*$", _blank, text)
    text = re.sub(r"<!--.*?-->", _blank, text, flags=re.DOTALL)
    return text


def score(text: str, plane: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    body = clean(text)
    words = len(body.split())
    kw = max(words, WORD_FLOOR) / 1000.0
    details: List[Dict[str, Any]] = []
    families = set()
    hard_fail = None
    for p in catalog["patterns"]:
        if p["plane"] not in (plane, "both"):
            continue
        matches = [(m.start(), m.group(0)) for m in p["regex"].finditer(body)]
        if not matches:
            continue
        hits = len(matches)
        density = hits / kw
        tol = float(p["threshold_per_1000w"])
        excess = max(0.0, density - tol) / max(tol, 0.5)
        contrib = float(p["weight"]) * excess
        if density > tol:
            families.add(p["family"])
        if tol == 0 and hard_fail is None:
            hard_fail = p["code"]
        details.append({"code": p["code"], "hits": hits, "per_1000w": round(density, 2),
                        "tolerance": tol, "weight": p["weight"], "contrib": round(contrib, 3),
                        "matches": matches})
    plane_cfg = catalog["planes"][plane]
    family_term = 0.5 * max(0, len(families) - int(plane_cfg["family_free"]))
    total = sum(d["contrib"] for d in details) + family_term
    details.sort(key=lambda d: d["contrib"], reverse=True)
    return {
        "plane": plane,
        "words": words,
        "score": round(total, 3),
        "threshold": float(plane_cfg["fail_above"]),
        "families_over": sorted(families),
        "family_term": family_term,
        "hard_fail": hard_fail,
        "is_fail": bool(hard_fail) or total > float(plane_cfg["fail_above"]),
        "details": details,
    }


def _read(paths: List[str]) -> Tuple[str, str]:
    chunks = []
    for p in paths:
        chunks.append(sys.stdin.read() if p == "-" else Path(p).read_text())
    return "\n".join(chunks), (paths[0] if len(paths) == 1 else "-")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", default=["-"])
    ap.add_argument("--plane", choices=["chat", "deliverable"], required=True)
    ap.add_argument("--explain", action="store_true", help="per-pattern table on stdout")
    ap.add_argument("--why", action="store_true", help="one stderr line per hit with the matched span")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    catalog = load_catalog()
    text, _ = _read(args.paths)
    result = score(text, args.plane, catalog)

    if args.why:
        for d in result["details"]:
            for offset, span in d["matches"]:
                sys.stderr.write(f"[why] {d['code']} @{offset}: {span!r}\n")
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.explain:
        print(f"words={result['words']} plane={result['plane']} threshold={result['threshold']}")
        print(f"{'code':<28}{'hits':>5}{'/1000w':>8}{'tol':>6}{'w':>3}{'contrib':>9}")
        for d in result["details"]:
            print(f"{d['code']:<28}{d['hits']:>5}{d['per_1000w']:>8}{d['tolerance']:>6}{d['weight']:>3}{d['contrib']:>9}")
        print(f"families over tolerance: {len(result['families_over'])} (+{result['family_term']})")
        print(f"score={result['score']}  hard_fail={result['hard_fail']}  fail={result['is_fail']}")
    else:
        verdict = "FAIL" if result["is_fail"] else "PASS"
        why = f" hard:{result['hard_fail']}" if result["hard_fail"] else ""
        print(f"{verdict} score={result['score']} threshold={result['threshold']} words={result['words']}{why}")
    return 1 if result["is_fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
