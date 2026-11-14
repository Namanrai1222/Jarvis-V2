"""File tools, limited to the allowed folders. Changes are journaled so they can be undone; nothing is ever deleted."""
import fnmatch
import glob
import os
import shutil
import time
from pathlib import Path

from ..policy.paths import PolicyError, display, resolve
from . import STOP, ToolResult, register

READ_LIMIT = 20_000


def _paths(args: dict, cfg: dict, key: str = "paths") -> list[str]:
    raw = args.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    if not raw:
        raise PolicyError("no files given")
    out = []
    for p in raw:
        if any(ch in str(p) for ch in "*?["):  # models often pass globs; expand, then check each match
            matches = sorted(glob.glob(os.path.expanduser(str(p))))
            if not matches:
                raise PolicyError(f"no files match {p}")
            out += [str(resolve(m, cfg, must_exist=True)) for m in matches]
        else:
            out.append(str(resolve(p, cfg, must_exist=True)))
    return list(dict.fromkeys(out))


def _names(paths: list[str], n: int = 5) -> str:
    names = [Path(p).name for p in paths]
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


# --- read (tier 0) ---

def _check_find(args: dict, cfg: dict) -> dict:
    return {"root": str(resolve(args.get("root", "~/Documents"), cfg, must_exist=True)),
            "pattern": str(args.get("pattern") or "*"),
            "modified_within_days": args.get("modified_within_days")}


def _find(a: dict, cfg: dict) -> ToolResult:
    since = time.time() - float(a["modified_within_days"]) * 86400 if a["modified_within_days"] else None
    hits = []
    for dirpath, dirnames, filenames in os.walk(a["root"]):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not fnmatch.fnmatch(name.lower(), a["pattern"].lower()):
                continue
            p = os.path.join(dirpath, name)
            try:
                if since and os.path.getmtime(p) < since:
                    continue
                resolve(p, cfg)
            except (OSError, PolicyError):
                continue
            hits.append(p)
            if len(hits) >= 200:
                break
    if not hits:
        return ToolResult("No matching files.")
    return ToolResult(f"{len(hits)} file(s):\n" + "\n".join(hits))


register(
    name="find_files",
    description="List files under a folder matching a glob pattern like '*.pdf', optionally modified in the last N days.",
    params={"root": {"type": "string", "description": "folder, e.g. ~/Downloads"},
            "pattern": {"type": "string"}, "modified_within_days": {"type": "number"}},
    required=["root"], tier=0, check=_check_find, run=_find, untrusted_output=True,
    summary=lambda a: f"Find {a['pattern']} in {display(a['root'])}",
)


def _read(a: dict, cfg: dict) -> ToolResult:
    p = Path(a["path"])
    if p.is_dir():
        return ToolResult("That's a folder; use find_files.")
    if p.suffix.lower() in {".pdf", ".docx", ".doc", ".pages"}:
        return ToolResult("Reading PDF and Word files isn't supported yet.")
    text = p.read_text(errors="replace")
    return ToolResult(text[:READ_LIMIT] + ("\n[truncated]" if len(text) > READ_LIMIT else ""))


register(
    name="read_file", description="Read a text file inside the allowed folders.",
    params={"path": {"type": "string"}}, required=["path"], tier=0,
    check=lambda a, c: {"path": str(resolve(a.get("path", ""), c, must_exist=True))},
    run=_read, untrusted_output=True, summary=lambda a: f"Read {display(a['path'])}",
)


# --- change (tier 2, undoable) ---

def _check_transfer(args: dict, cfg: dict) -> dict:
    return {"paths": _paths(args, cfg), "dest_dir": str(resolve(args.get("dest_dir", ""), cfg))}


def _transfer(a: dict, copy: bool) -> ToolResult:
    dest = Path(a["dest_dir"])
    dest.mkdir(parents=True, exist_ok=True)
    ops, skipped = [], []
    for p in a["paths"]:
        if STOP.is_set():
            break
        src, target = Path(p), dest / Path(p).name
        if target.exists():
            skipped.append(src.name)  # never overwrite
            continue
        if copy:
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, target)
            ops.append({"op": "remove_copy", "path": str(target)})
        else:
            shutil.move(src, target)
            ops.append({"op": "move", "from": str(src), "to": str(target)})
    verb = "Copied" if copy else "Moved"
    text = f"{verb} {len(ops)} item(s) to {display(dest)}."
    if skipped:
        text += f" Skipped {len(skipped)} that already exist there: {_names(skipped)}."
    if STOP.is_set():
        text += " Stopped early by the kill switch."
    return ToolResult(text, ops)


register(
    name="move_files", description="Move files or folders into a destination folder (never overwrites).",
    params={"paths": {"type": "array", "items": {"type": "string"}}, "dest_dir": {"type": "string"}},
    required=["paths", "dest_dir"], tier=2, check=_check_transfer, run=lambda a, c: _transfer(a, copy=False),
    summary=lambda a: f"Move {len(a['paths'])} item(s) to {display(a['dest_dir'])}: {_names(a['paths'])}",
)

register(
    name="copy_files", description="Copy files or folders into a destination folder (never overwrites).",
    params={"paths": {"type": "array", "items": {"type": "string"}}, "dest_dir": {"type": "string"}},
    required=["paths", "dest_dir"], tier=2, check=_check_transfer, run=lambda a, c: _transfer(a, copy=True),
    summary=lambda a: f"Copy {len(a['paths'])} item(s) to {display(a['dest_dir'])}: {_names(a['paths'])}",
)


def _check_rename(args: dict, cfg: dict) -> dict:
    src = resolve(args.get("path", ""), cfg, must_exist=True)
    new = str(args.get("new_name", "")).strip()
    if not new or "/" in new or new in {".", ".."}:
        raise PolicyError("new name must be a plain file name")
    target = resolve(str(src.parent / new), cfg)
    if target.exists():
        raise PolicyError(f"{new} already exists")
    return {"path": str(src), "new_name": new}


def _rename(a: dict, cfg: dict) -> ToolResult:
    src = Path(a["path"])
    target = src.parent / a["new_name"]
    src.rename(target)
    return ToolResult(f"Renamed {src.name} to {target.name}.", [{"op": "move", "from": str(src), "to": str(target)}])


register(
    name="rename_file", description="Rename a file or folder in place.",
    params={"path": {"type": "string"}, "new_name": {"type": "string"}}, required=["path", "new_name"],
    tier=2, check=_check_rename, run=_rename,
    summary=lambda a: f"Rename {display(a['path'])} to {a['new_name']}",
)


def _mkdir(a: dict, cfg: dict) -> ToolResult:
    p = Path(a["path"])
    if p.exists():
        return ToolResult(f"{display(p)} already exists.")
    p.mkdir(parents=True)
    return ToolResult(f"Created {display(p)}.", [{"op": "rmdir", "path": str(p)}])


register(
    name="create_folder", description="Create a new folder.",
    params={"path": {"type": "string"}}, required=["path"], tier=2,
    check=lambda a, c: {"path": str(resolve(a.get("path", ""), c))}, run=_mkdir,
    summary=lambda a: f"Create folder {display(a['path'])}",
)


def _trash_one(path: str) -> str:
    from Foundation import NSURL, NSFileManager

    ok, url, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        NSURL.fileURLWithPath_(path), None, None)
    if not ok:
        raise OSError(str(err))
    return url.path()


def _trash(a: dict, cfg: dict) -> ToolResult:
    ops = []
    for p in a["paths"]:
        if STOP.is_set():
            break
        ops.append({"op": "move", "from": p, "to": _trash_one(p)})
    return ToolResult(f"Moved {len(ops)} item(s) to the Trash.", ops)


register(
    name="trash", description="Move files to the macOS Trash (restorable). Permanent delete is not possible.",
    params={"paths": {"type": "array", "items": {"type": "string"}}}, required=["paths"], tier=2,
    check=lambda a, c: {"paths": _paths(a, c)}, run=_trash,
    summary=lambda a: f"Move {len(a['paths'])} item(s) to the Trash: {_names(a['paths'])}",
)


def undo(ops: list[dict]) -> str:
    """Reverse a journal, newest first. Removing a copy goes to the Trash, never a hard delete."""
    done = 0
    for op in reversed(ops):
        if op["op"] == "move" and Path(op["to"]).exists() and not Path(op["from"]).exists():
            Path(op["from"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(op["to"], op["from"])
            done += 1
        elif op["op"] == "remove_copy" and Path(op["path"]).exists():
            _trash_one(op["path"])
            done += 1
        elif op["op"] == "rmdir" and Path(op["path"]).is_dir() and not any(Path(op["path"]).iterdir()):
            Path(op["path"]).rmdir()
            done += 1
    return f"Undid {done} of {len(ops)} change(s)."
