import os

import pytest

from jarvis.policy.paths import PolicyError, resolve


def test_inside_root_ok(cfg, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert resolve(str(tmp_path / "a.txt"), cfg) == (tmp_path / "a.txt").resolve()


@pytest.mark.parametrize("p", ["~/.ssh/id_rsa", "/etc/passwd", "~/Library/Keychains", "/"])
def test_outside_roots_denied(cfg, p):
    with pytest.raises(PolicyError):
        resolve(p, cfg)


def test_dotdot_escape_denied(cfg, tmp_path):
    with pytest.raises(PolicyError):
        resolve(str(tmp_path / ".." / ".." / "etc"), cfg)


def test_symlink_escape_denied(cfg, tmp_path):
    os.symlink("/etc", tmp_path / "link")
    with pytest.raises(PolicyError):
        resolve(str(tmp_path / "link" / "hosts"), cfg)


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.pem", "login.keychain-db",
                                  ".git/config", ".ssh/known_hosts"])
def test_secret_names_denied_inside_root(cfg, tmp_path, name):
    with pytest.raises(PolicyError):
        resolve(str(tmp_path / name), cfg)


def test_must_exist(cfg, tmp_path):
    with pytest.raises(PolicyError):
        resolve(str(tmp_path / "missing.txt"), cfg, must_exist=True)
