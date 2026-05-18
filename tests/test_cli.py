"""End-to-end CLI tests that exec ghswitch as a subprocess against a real git repo."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ghswitch.py"


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def run(args, cwd=None, env=None, check=True):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd) if cwd else None,
        env=e,
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.fixture
def cfg_env(tmp_path):
    """Env that points ghswitch at an isolated config dir."""
    env = {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
    }
    if sys.platform == "win32":
        env["APPDATA"] = str(tmp_path / "cfg")
        env["USERPROFILE"] = str(tmp_path)
    return env


@pytest.fixture
def git_repo(tmp_path, cfg_env):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com-work:org/proj.git"],
        cwd=str(repo), check=True,
    )
    return repo


def test_help():
    r = run(["--help"])
    assert r.returncode == 0
    assert "Manage multiple GitHub identities" in r.stdout


def test_list_empty(cfg_env):
    r = run(["list"], env=cfg_env)
    assert r.returncode == 0
    assert "no profiles configured" in r.stdout


def test_add_list_remove(cfg_env):
    r = run([
        "add", "work",
        "--username", "alice-work",
        "--email", "alice@workco.com",
        "--host-alias", "github.com-work",
        "--folder", "~/work/*",
        "--set-default",
    ], env=cfg_env)
    assert r.returncode == 0, r.stderr

    r = run(["list"], env=cfg_env)
    assert "work" in r.stdout
    assert "alice@workco.com" in r.stdout
    assert "* work" in r.stdout  # default marker

    r = run(["whoami"], env=cfg_env)
    assert r.stdout.strip() == "work"

    r = run(["remove", "work"], env=cfg_env)
    assert r.returncode == 0

    r = run(["list"], env=cfg_env)
    assert "no profiles configured" in r.stdout


def test_use_applies_identity(cfg_env, git_repo):
    run([
        "add", "work",
        "--username", "alice-work",
        "--email", "alice@workco.com",
        "--host-alias", "github.com-work",
    ], env=cfg_env)

    r = run(["use", "work"], env=cfg_env, cwd=git_repo)
    assert r.returncode == 0, r.stderr

    name = subprocess.check_output(
        ["git", "config", "--get", "user.name"], cwd=str(git_repo), text=True
    ).strip()
    email = subprocess.check_output(
        ["git", "config", "--get", "user.email"], cwd=str(git_repo), text=True
    ).strip()
    assert name == "alice-work"
    assert email == "alice@workco.com"


def test_status_suggests_profile_from_remote(cfg_env, git_repo):
    run([
        "add", "work",
        "--username", "alice-work",
        "--email", "alice@workco.com",
        "--host-alias", "github.com-work",
    ], env=cfg_env)

    r = run(["status"], env=cfg_env, cwd=git_repo)
    assert r.returncode == 0
    assert "suggested: work" in r.stdout or "matches stored profile" in r.stdout


def test_remove_unknown_fails(cfg_env):
    r = run(["remove", "nope"], env=cfg_env, check=False)
    assert r.returncode != 0
    assert "no such profile" in r.stderr


def test_use_outside_repo_fails(cfg_env, tmp_path):
    run([
        "add", "work",
        "--username", "u", "--email", "u@x",
    ], env=cfg_env)
    notrepo = tmp_path / "notrepo"
    notrepo.mkdir()
    r = run(["use", "work"], env=cfg_env, cwd=notrepo, check=False)
    assert r.returncode != 0
    assert "not inside a git repository" in r.stderr


def test_completion_bash():
    r = run(["completion", "bash"])
    assert r.returncode == 0
    assert "complete -F _ghswitch" in r.stdout
