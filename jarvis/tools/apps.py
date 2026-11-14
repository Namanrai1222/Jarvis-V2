"""Apps, URLs, web search, system status."""
import re
import subprocess
from urllib.parse import quote_plus, urlparse

from ..policy.paths import PolicyError
from . import ToolResult, register

ALLOWED_SCHEMES = {"http", "https", "vscode", "x-apple-reminderkit"}


def _check_app(args: dict, cfg: dict) -> dict:
    app = str(args.get("app", "")).strip()
    bundle = cfg["apps"].get(app.lower(), app)
    if bundle not in cfg["apps"].values():
        raise PolicyError(f"{app} isn't in the allowed apps")
    return {"app": bundle}


register(
    name="open_app", description="Open an allowed Mac app by name (e.g. 'chrome', 'vs code') or bundle ID.",
    params={"app": {"type": "string"}}, required=["app"], tier=1, check=_check_app,
    run=lambda a, c: (subprocess.run(["open", "-b", a["app"]], check=True), ToolResult(f"Opened {a['app']}."))[1],
    summary=lambda a: f"Open {a['app']}",
)

register(
    name="quit_app", description="Quit an allowed Mac app.",
    params={"app": {"type": "string"}}, required=["app"], tier=1, check=_check_app,
    run=lambda a, c: (subprocess.run(["osascript", "-l", "JavaScript", "-e", "function run(a){Application(a[0]).quit()}",
                                      a["app"]], check=True, timeout=15), ToolResult(f"Quit {a['app']}."))[1],
    summary=lambda a: f"Quit {a['app']}",
)


def _check_url(args: dict, cfg: dict) -> dict:
    url = str(args.get("url", "")).strip()
    if "://" not in url and re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", url, re.I):
        url = "https://" + url
    if urlparse(url).scheme.lower() not in ALLOWED_SCHEMES:
        raise PolicyError("only http(s) links can be opened")
    return {"url": url}


register(
    name="open_url", description="Open a website in the default browser.",
    params={"url": {"type": "string"}}, required=["url"], tier=1, check=_check_url,
    run=lambda a, c: (subprocess.run(["open", a["url"]], check=True), ToolResult(f"Opened {a['url']}."))[1],
    summary=lambda a: f"Open {a['url']}",
)

register(
    name="web_search", description="Search the web in the default browser.",
    params={"query": {"type": "string"}}, required=["query"], tier=1,
    run=lambda a, c: (subprocess.run(["open", "https://www.google.com/search?q=" + quote_plus(a["query"])], check=True),
                      ToolResult(f"Searched for {a['query']}."))[1],
    summary=lambda a: f"Search the web for '{a['query']}'",
)


def _status(a: dict, c: dict) -> ToolResult:
    batt = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)%;\s*([\w ]+);", batt)
    df = subprocess.run(["df", "-h", "/System/Volumes/Data"], capture_output=True, text=True).stdout.splitlines()
    free = df[1].split()[3] if len(df) > 1 else "?"
    parts = [f"Battery {m.group(1)}% ({m.group(2)})" if m else "Battery unknown", f"{free} disk free"]
    return ToolResult(". ".join(parts) + ".")


register(name="system_status", description="Battery level and free disk space.", params={}, required=[], tier=0,
         run=_status, summary=lambda a: "Check system status")
