import os
import sys
from pathlib import Path

import pytest

# Make the top-level ghswitch.py importable without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Redirect ghswitch's config dir to a tmp path on every platform."""
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Ensure HOME is also redirected so ~ expansions in tests don't leak.
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_keyring(monkeypatch):
    """Force the file-based fallback for secret storage."""
    import ghswitch
    monkeypatch.setattr(ghswitch, "_HAS_KEYRING", False)
    yield
