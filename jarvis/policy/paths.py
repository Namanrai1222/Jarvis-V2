"""Folder scope: every path is resolved (realpath) first, then checked against allowed roots and deny rules."""
import fnmatch
import os
from pathlib import Path

from .. import config


class PolicyError(Exception):
    """An action or argument the policy refuses."""


DENY_DIRS = {".ssh", ".gnupg", ".aws", ".config", ".git"}
DENY_NAMES = ["*.keychain*", ".env*", "*id_rsa*", "*id_ed25519*", "*.pem", "*.p12", "*password*export*"]


def _roots(cfg: dict) -> list[Path]:
    return [Path(os.path.expanduser(r)).resolve() for r in cfg["scope"]["allowed_roots"]]


def resolve(path: str, cfg: dict, must_exist: bool = False) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise PolicyError("empty path")
    p = Path(os.path.expanduser(path.strip())).resolve()
    if must_exist and not p.exists():
        raise PolicyError(f"{display(p)} doesn't exist")
    if not any(p == r or p.is_relative_to(r) for r in _roots(cfg)):
        raise PolicyError(f"{display(p)} is outside the folders Jarvis may touch")
    home_lib = (Path.home() / "Library").resolve()
    if p.is_relative_to(home_lib) or p.is_relative_to(config.APP_DIR.resolve()):
        raise PolicyError(f"{display(p)} is a protected folder")
    if DENY_DIRS & set(p.parts):
        raise PolicyError(f"{display(p)} is a protected folder")
    if any(fnmatch.fnmatch(p.name.lower(), pat) for pat in DENY_NAMES):
        raise PolicyError(f"{p.name} looks like a secret; Jarvis won't touch it")
    return p


def display(p: Path | str) -> str:
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s
