"""Unit tests for ghswitch internals."""
import json
import os
import sys
from pathlib import Path

import pytest

import ghswitch
from ghswitch import (
    Config,
    Profile,
    config_path,
    detect_profile,
    load_config,
    parse_repo_url,
    rewrite_url_with_alias,
    rewrite_url_with_token,
    save_config,
)


# ---------- url parsing ----------

@pytest.mark.parametrize("url,expected", [
    ("git@github.com:owner/repo.git", {"host": "github.com", "owner": "owner", "repo": "repo"}),
    ("git@github.com-work:org/proj.git", {"host": "github.com-work", "owner": "org", "repo": "proj"}),
    ("https://github.com/owner/repo.git", {"host": "github.com", "owner": "owner", "repo": "repo"}),
    ("https://github.com/owner/repo", {"host": "github.com", "owner": "owner", "repo": "repo"}),
    ("ssh://git@github.com/owner/repo.git", {"host": "github.com", "owner": "owner", "repo": "repo"}),
])
def test_parse_repo_url_valid(url, expected):
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize("url", [
    "",
    "not-a-url",
    "ftp://github.com/x/y",
    "https://example.com/foo",  # missing owner/repo segment
])
def test_parse_repo_url_invalid(url):
    assert parse_repo_url(url) is None


# ---------- url rewrites ----------

def test_rewrite_url_with_alias_ssh():
    out = rewrite_url_with_alias("git@github.com:owner/repo.git", "github.com-work")
    assert out == "git@github.com-work:owner/repo.git"


def test_rewrite_url_with_alias_passthrough_on_unparsable():
    assert rewrite_url_with_alias("not-a-url", "anything") == "not-a-url"


def test_rewrite_url_with_token_https():
    out = rewrite_url_with_token("https://github.com/owner/repo.git", "alice", "ghp_xxx")
    assert out == "https://alice:ghp_xxx@github.com/owner/repo.git"


def test_rewrite_url_with_token_passthrough_on_ssh():
    src = "git@github.com:owner/repo.git"
    assert rewrite_url_with_token(src, "alice", "ghp_xxx") == src


# ---------- config persistence ----------

def test_config_roundtrip(isolated_config):
    cfg = Config()
    cfg.profiles["work"] = Profile(
        name="work",
        username="alice-work",
        email="alice@workco.com",
        ssh_key="~/.ssh/id_work",
        host_alias="github.com-work",
        folder_patterns=["~/work/*"],
        has_token=False,
    )
    cfg.default = "work"
    save_config(cfg)

    p = config_path()
    assert p.exists()
    # 0600 on POSIX
    if sys.platform != "win32":
        mode = p.stat().st_mode & 0o777
        assert mode == 0o600

    loaded = load_config()
    assert loaded.default == "work"
    assert loaded.profiles["work"].username == "alice-work"
    assert loaded.profiles["work"].folder_patterns == ["~/work/*"]


def test_load_config_missing_returns_empty(isolated_config):
    cfg = load_config()
    assert cfg.profiles == {}
    assert cfg.default is None


# ---------- secret fallback ----------

def test_secret_fallback_roundtrip(isolated_config, no_keyring):
    ghswitch.store_token("work", "ghp_secret")
    assert ghswitch.get_token("work") == "ghp_secret"

    # File mode 0600 on POSIX
    path = ghswitch.fallback_secrets_path()
    assert path.exists()
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777) == 0o600

    ghswitch.delete_token("work")
    assert ghswitch.get_token("work") is None


def test_secret_fallback_multiple_profiles(isolated_config, no_keyring):
    ghswitch.store_token("a", "tok-a")
    ghswitch.store_token("b", "tok-b")
    assert ghswitch.get_token("a") == "tok-a"
    assert ghswitch.get_token("b") == "tok-b"
    ghswitch.delete_token("a")
    assert ghswitch.get_token("a") is None
    assert ghswitch.get_token("b") == "tok-b"


# ---------- profile detection ----------

def _make_cfg():
    cfg = Config()
    cfg.profiles["personal"] = Profile(
        name="personal",
        username="alice",
        email="alice@personal.dev",
        host_alias="github.com-personal",
        folder_patterns=[],
    )
    cfg.profiles["work"] = Profile(
        name="work",
        username="alice-work",
        email="alice@workco.com",
        host_alias="github.com-work",
        folder_patterns=["/tmp/work-area/*"],
    )
    return cfg


def test_detect_by_host_alias():
    cfg = _make_cfg()
    p = detect_profile(cfg, cwd=Path("/tmp"), url="git@github.com-work:org/proj.git")
    assert p is not None and p.name == "work"


def test_detect_by_folder_pattern(tmp_path, monkeypatch):
    cfg = Config()
    cfg.profiles["work"] = Profile(
        name="work", username="u", email="e",
        folder_patterns=[str(tmp_path / "work" / "*")],
    )
    target = tmp_path / "work" / "repo"
    target.mkdir(parents=True)
    p = detect_profile(cfg, cwd=target, url=None)
    assert p is not None and p.name == "work"


def test_detect_no_match_returns_none(tmp_path):
    cfg = _make_cfg()
    p = detect_profile(cfg, cwd=tmp_path, url="git@github.com:foo/bar.git")
    assert p is None


def test_detect_folder_wins_over_host(tmp_path):
    """Folder pattern is checked before host alias; the more-specific signal wins."""
    cfg = Config()
    cfg.profiles["work"] = Profile(
        name="work", username="w", email="w@x",
        host_alias="github.com-work",
    )
    cfg.profiles["personal"] = Profile(
        name="personal", username="p", email="p@x",
        folder_patterns=[str(tmp_path / "*")],
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    p = detect_profile(cfg, cwd=repo, url="git@github.com-work:org/proj.git")
    assert p is not None and p.name == "personal"
