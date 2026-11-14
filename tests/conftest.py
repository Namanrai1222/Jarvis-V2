import copy

import pytest

from jarvis.config import DEFAULTS


@pytest.fixture
def cfg(tmp_path):
    """Config whose only allowed folder is a temp dir."""
    c = copy.deepcopy(DEFAULTS)
    c["scope"]["allowed_roots"] = [str(tmp_path)]
    c["shortcuts"] = {"Focus Playlist": "tier1", "Clean Desktop": "tier2"}
    return c
