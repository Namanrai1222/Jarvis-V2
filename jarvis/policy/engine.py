"""Policy engine: pure code that runs before every tool call. Tiers live here, not in config.

Tier 0 read -> run. Tier 1 low impact -> run, but confirm if the request is tainted or touches > 50 files.
Tier 2 changes data -> confirm. Tier 3 outbound/irreversible -> blocked.
"""
from dataclasses import dataclass

from .paths import PolicyError

BULK_LIMIT = 50


@dataclass
class Decision:
    verdict: str  # "run" | "confirm" | "block"
    tier: int
    args: dict
    reason: str = ""


def decide(tool, args: dict, *, tainted: bool, paused: bool, cfg: dict) -> Decision:
    try:
        args = tool.check(dict(args), cfg) if tool.check else dict(args)
        tier = tool.tier(args, cfg) if callable(tool.tier) else tool.tier
    except PolicyError as e:
        return Decision("block", 3, args, str(e))
    if tier >= 3:
        return Decision("block", tier, args, "that kind of action is blocked; Naman has to do it directly")
    if paused and tier >= 1:
        return Decision("block", tier, args, "Jarvis is paused; say 'resume' first")
    bulk = len(args.get("paths", [])) > BULK_LIMIT
    if tier == 2 or (tier == 1 and (tainted or bulk)):
        return Decision("confirm", tier, args)
    return Decision("run", tier, args)
