"""call_shape.py: which calls the oscillation ring records, and in what form.

The host adds intent fields (toolSummary, toolAction) that the model rewords on
every call, and a view_file of a growing log moves its EndLine on every read.
Hashing either made each poll of one background task look like a new call, so
they are dropped before hashing. A view_file that has a StartLine loses its
EndLine: reading one file again from the same line is a repeat, paging forward
moves StartLine and stays new. A view_file with only an EndLine keeps it.

manage_task is exempt except a status check on one task (Action=status with a
TaskId); list, kill and send_input stay exempt. Standard library only.
"""

from __future__ import annotations

from typing import Any

INTENT_FIELDS = frozenset({"toolSummary", "toolAction"})
SCHEDULE_TOOL = "schedule"
TASK_TOOL = "manage_task"
VIEW_TOOL = "view_file"

# Calls that are legitimately repeated: waiting, messaging, asking.
EXEMPT_TOOLS = frozenset({
    TASK_TOOL,
    SCHEDULE_TOOL,
    "send_message",
    "ask_question",
})


def normalize(tool: str, args: Any) -> Any:
  """The args as hashed: intent fields dropped, a log read's EndLine too."""
  if not isinstance(args, dict):
    return args
  out = {k: v for k, v in args.items() if k not in INTENT_FIELDS}
  if tool == VIEW_TOOL and "StartLine" in out:
    out.pop("EndLine", None)
  return out


def is_task_status(tool: str, args: Any) -> bool:
  """True for manage_task Action=status naming a TaskId."""
  if tool != TASK_TOOL or not isinstance(args, dict):
    return False
  action = str(args.get("Action") or "").lower()
  return action == "status" and bool(args.get("TaskId"))


def is_exempt(tool: str, args: Any) -> bool:
  if is_task_status(tool, args):
    return False
  return tool in EXEMPT_TOOLS


def is_poll(tool: str, args: Any) -> bool:
  """True for a task status check or a read of a .log file."""
  if is_task_status(tool, args):
    return True
  if tool != VIEW_TOOL or not isinstance(args, dict):
    return False
  path = str(args.get("AbsolutePath") or args.get("path") or "")
  return path.endswith(".log")
