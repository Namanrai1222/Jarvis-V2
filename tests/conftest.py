import copy

import pytest

from jarvis import daemon, secrets
from jarvis.notify import presence

from jarvis.config import DEFAULTS


@pytest.fixture
def cfg(tmp_path):
    """Config whose only allowed folder is a temp dir."""
    c = copy.deepcopy(DEFAULTS)
    c["scope"]["allowed_roots"] = [str(tmp_path)]
    c["shortcuts"] = {"Focus Playlist": "tier1", "Clean Desktop": "tier2"}
    return c


@pytest.fixture(autouse=True)
def no_real_side_channels(monkeypatch):
    """Tests never pop Mac notifications, read the Keychain or message a real phone."""
    monkeypatch.setattr(daemon, "mac_notification", lambda text: None)
    monkeypatch.setattr(secrets, "get", lambda name: None)
    monkeypatch.setattr(presence, "present", lambda away_after=600: True)
