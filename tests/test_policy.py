import pytest

from jarvis.policy.engine import decide
from jarvis.policy.pending import TTL, PendingStore
from jarvis.tools import REGISTRY, Tool, ToolResult


def _tool(tier):
    return Tool(name=f"t{tier}", description="", params={}, required=[], tier=tier, run=lambda a, c: ToolResult(""))


# every tier x taint x paused combination
@pytest.mark.parametrize("tier,tainted,paused,expected", [
    (0, False, False, "run"), (0, True, False, "run"), (0, False, True, "run"),
    (1, False, False, "run"), (1, True, False, "confirm"), (1, False, True, "block"),
    (2, False, False, "confirm"), (2, True, False, "confirm"), (2, False, True, "block"),
    (3, False, False, "block"), (3, True, False, "block"),
])
def test_tier_matrix(cfg, tier, tainted, paused, expected):
    assert decide(_tool(tier), {}, tainted=tainted, paused=paused, cfg=cfg).verdict == expected


def test_bulk_tier1_needs_confirm(cfg):
    d = decide(_tool(1), {"paths": [f"f{i}" for i in range(51)]}, tainted=False, paused=False, cfg=cfg)
    assert d.verdict == "confirm"


def test_registered_tool_tiers(cfg):
    expected = {"find_files": 0, "read_file": 0, "system_status": 0, "list_reminders": 0, "list_events": 0,
                "open_app": 1, "open_url": 1, "web_search": 1, "create_reminder": 1, "create_note": 1,
                "draft_email": 1, "move_files": 2, "copy_files": 2, "rename_file": 2, "trash": 2,
                "create_folder": 2, "create_event": 2}
    for name, tier in expected.items():
        assert REGISTRY[name].tier == tier, name


def test_no_shell_or_send_tools():
    assert not {"run_shell", "run_powershell", "send_email", "delete_path", "send_message"} & set(REGISTRY)


def test_app_allowlist(cfg):
    assert decide(REGISTRY["open_app"], {"app": "chrome"}, tainted=False, paused=False, cfg=cfg).verdict == "run"
    assert decide(REGISTRY["open_app"], {"app": "com.apple.Terminal"}, tainted=False, paused=False,
                  cfg=cfg).verdict == "block"


@pytest.mark.parametrize("url,ok", [("https://github.com", True), ("youtube.com", True),
                                    ("file:///etc/passwd", False), ("javascript:alert(1)", False),
                                    ("-a Terminal", False)])
def test_url_schemes(cfg, url, ok):
    d = decide(REGISTRY["open_url"], {"url": url}, tainted=False, paused=False, cfg=cfg)
    assert (d.verdict == "run") == ok


def test_shortcut_allowlist_and_tiers(cfg):
    sc = REGISTRY["run_shortcut"]
    assert decide(sc, {"name": "Focus Playlist"}, tainted=False, paused=False, cfg=cfg).verdict == "run"
    assert decide(sc, {"name": "Clean Desktop"}, tainted=False, paused=False, cfg=cfg).verdict == "confirm"
    assert decide(sc, {"name": "Unknown"}, tainted=False, paused=False, cfg=cfg).verdict == "block"


def test_file_tools_out_of_scope_blocked(cfg):
    d = decide(REGISTRY["move_files"], {"paths": ["~/.ssh/id_rsa"], "dest_dir": "~/Desktop"},
               tainted=False, paused=False, cfg=cfg)
    assert d.verdict == "block"


def test_pending_expires_and_is_frozen():
    now = [1000.0]
    store = PendingStore(clock=lambda: now[0])
    args = {"paths": ["a"]}
    p = store.add("move_files", args, "Move a", False, "r1")
    args["paths"].append("evil")  # changing the caller's dict can't change the record
    assert p.args == {"paths": ["a"]}
    now[0] += TTL + 1
    assert store.pop(p.id) is None
