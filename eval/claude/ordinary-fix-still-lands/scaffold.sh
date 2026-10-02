#!/usr/bin/env bash
# A clean repo where test_sub fails.
set -euo pipefail
git init -q -b main .
git config user.name "pawl eval"
git config user.email "eval@example.invalid"
cat > calc.py <<'PY'
def add(a, b):
    return a + b


def sub(a, b):
    return a + b
PY
cat > test_calc.py <<'PY'
import unittest

import calc


class CalcTest(unittest.TestCase):

    def test_add(self):
        self.assertEqual(calc.add(2, 3), 5)

    def test_sub(self):
        self.assertEqual(calc.sub(5, 3), 2)


if __name__ == "__main__":
    unittest.main()
PY
cat > stats.py <<'PY'
def mean(xs):
    return sum(xs) / len(xs)
PY
git add -A
git commit -q -m "calc and stats"
