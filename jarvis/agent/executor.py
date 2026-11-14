"""Executor: every tool call goes policy -> (run | pending | block) -> activity log."""
import json

from ..activity import Activity
from ..policy.engine import decide
from ..policy.paths import PolicyError
from ..policy.pending import Pending, PendingStore
from ..tools import REGISTRY, ToolResult, files


class UnknownTool(Exception):
    pass


class Executor:
    def __init__(self, cfg: dict, activity: Activity, pending: PendingStore) -> None:
        self.cfg = cfg
        self.activity = activity
        self.pending = pending
        self.paused = False

    def call(self, name: str, args: dict, *, tainted: bool, request_id: str) -> tuple[str, Pending | None]:
        tool = REGISTRY.get(name)
        if tool is None:
            raise UnknownTool(name)
        d = decide(tool, args or {}, tainted=tainted, paused=self.paused, cfg=self.cfg)
        log = dict(request_id=request_id, tool=name, args=d.args, tier=d.tier, tainted=tainted)
        if d.verdict == "block":
            self.activity.log(**log, decision="block", result=d.reason)
            return f"Blocked: {d.reason}.", None
        if d.verdict == "confirm":
            p = self.pending.add(name, d.args, tool.summary(d.args), tainted, request_id)
            self.activity.log(**log, decision=f"pending {p.id}")
            return f"Waiting for Naman to confirm ({p.id}): {p.summary}. Do not call this tool again.", p
        return self._run(tool, d.args, log), None

    def _run(self, tool, args: dict, log: dict) -> str:
        try:
            res = tool.run(args, self.cfg)
        except Exception as e:  # tool failures become text the model and Naman can see
            res = ToolResult(f"Error: {e}")
        self.activity.log(**log, decision="run", result=res.text, undo=res.undo)
        return res.text

    def confirm(self, pid: str) -> str:
        p = self.pending.pop(pid)
        if p is None:
            return f"There's no pending action {pid.upper()} (it may have expired)."
        if self.paused:
            return "Jarvis is paused; say 'resume' first."
        tool = REGISTRY[p.tool]
        try:  # re-check scope; arguments stay exactly as approved
            args = tool.check(p.args, self.cfg) if tool.check else p.args
            tier = tool.tier(args, self.cfg) if callable(tool.tier) else tool.tier
        except PolicyError as e:
            return f"{p.id} can't run any more: {e}."
        if json.dumps(args, sort_keys=True) != p.args_json:
            return f"{p.id} no longer matches what you approved (files changed). Ask me again."
        log = dict(request_id=p.request_id, tool=p.tool, args=args, tier=tier, tainted=p.tainted)
        return self._run(tool, args, log)

    def cancel(self, pid: str) -> str:
        p = self.pending.pop(pid)
        if p is None:
            return f"There's no pending action {pid.upper()}."
        self.activity.log(request_id=p.request_id, tool=p.tool, args=p.args, tier=None, tainted=p.tainted,
                          decision=f"cancelled {p.id}", result=p.summary)
        return f"Cancelled {p.id}."

    def undo_last(self) -> str:
        row = self.activity.last_undoable()
        if row is None:
            return "Nothing to undo."
        msg = files.undo(json.loads(row["undo"]))
        self.activity.mark_undone(row["id"])
        return msg
