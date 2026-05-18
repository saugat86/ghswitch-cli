#!/usr/bin/env python3
"""ghswitch — manage multiple GitHub identities for git operations.

Stores per-profile metadata (username, email, ssh_key path, host alias, folder
patterns) in a JSON config file. Personal access tokens are stored in the OS
keychain via the `keyring` package when available; otherwise they fall back to
a 0600-permission file in the config dir.

Subcommands: list, add, remove, use, clone, status, whoami, completion.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

APP_NAME = "ghswitch"
KEYRING_SERVICE = "ghswitch"

try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False


# ---------- paths ----------

def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def config_path() -> Path:
    return config_dir() / "profiles.json"


def fallback_secrets_path() -> Path:
    return config_dir() / "secrets.json"


# ---------- data model ----------

@dataclass
class Profile:
    name: str
    username: str
    email: str
    ssh_key: Optional[str] = None  # path to private key
    host_alias: Optional[str] = None  # SSH host alias e.g. github.com-work
    folder_patterns: list[str] = field(default_factory=list)
    has_token: bool = False  # token kept out of JSON; tracked here for UX

    def to_json(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Profile":
        return cls(
            name=d["name"],
            username=d["username"],
            email=d["email"],
            ssh_key=d.get("ssh_key"),
            host_alias=d.get("host_alias"),
            folder_patterns=d.get("folder_patterns", []),
            has_token=d.get("has_token", False),
        )


@dataclass
class Config:
    profiles: dict[str, Profile] = field(default_factory=dict)
    default: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "default": self.default,
            "profiles": {n: p.to_json() for n, p in self.profiles.items()},
        }

    @classmethod
    def from_json(cls, d: dict) -> "Config":
        profiles = {n: Profile.from_json(p) for n, p in d.get("profiles", {}).items()}
        return cls(profiles=profiles, default=d.get("default"))


def load_config() -> Config:
    p = config_path()
    if not p.exists():
        return Config()
    try:
        return Config.from_json(json.loads(p.read_text()))
    except (OSError, json.JSONDecodeError) as e:
        die(f"failed to read config at {p}: {e}")


def save_config(cfg: Config) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = config_path()
    p.write_text(json.dumps(cfg.to_json(), indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


# ---------- secrets ----------

def store_token(profile: str, token: str) -> None:
    if _HAS_KEYRING:
        keyring.set_password(KEYRING_SERVICE, profile, token)
        return
    path = fallback_secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
    data[profile] = token
    path.write_text(json.dumps(data))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_token(profile: str) -> Optional[str]:
    if _HAS_KEYRING:
        try:
            return keyring.get_password(KEYRING_SERVICE, profile)
        except Exception:  # noqa: BLE001 — keyring backends raise various errors
            pass
    path = fallback_secrets_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get(profile)
    except (OSError, json.JSONDecodeError):
        return None


def delete_token(profile: str) -> None:
    if _HAS_KEYRING:
        try:
            keyring.delete_password(KEYRING_SERVICE, profile)
        except Exception:  # noqa: BLE001
            pass
    path = fallback_secrets_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
        data.pop(profile, None)
        if data:
            path.write_text(json.dumps(data))
        else:
            path.unlink()


# ---------- helpers ----------

def die(msg: str, code: int = 1) -> None:
    print(f"ghswitch: error: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str) -> None:
    print(f"ghswitch: warning: {msg}", file=sys.stderr)


def run_git(args: list[str], cwd: Optional[Path] = None, check: bool = True,
            env: Optional[dict] = None, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        env=env,
        text=True,
        capture_output=capture,
    )


def in_git_repo(cwd: Optional[Path] = None) -> bool:
    try:
        r = run_git(["rev-parse", "--is-inside-work-tree"], cwd=cwd, check=False, capture=True)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except FileNotFoundError:
        die("git is not installed or not on PATH")
        return False  # unreachable


def repo_remote(cwd: Optional[Path] = None) -> Optional[str]:
    r = run_git(["config", "--get", "remote.origin.url"], cwd=cwd, check=False, capture=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def prompt(label: str, default: Optional[str] = None, secret: bool = False) -> str:
    if secret:
        import getpass
        return getpass.getpass(f"{label}: ")
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{label}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val or (default or "")


# ---------- profile detection ----------

GH_URL_RE = re.compile(
    r"^(?:git@|ssh://git@|https://)"
    r"(?P<host>[^:/]+)"
    r"[:/]"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


def parse_repo_url(url: str) -> Optional[dict]:
    m = GH_URL_RE.match(url)
    if not m:
        return None
    return m.groupdict()


def detect_profile(cfg: Config, *, cwd: Optional[Path] = None,
                   url: Optional[str] = None) -> Optional[Profile]:
    """Pick a profile based on (1) folder patterns, (2) host_alias in remote URL."""
    target_dir = (cwd or Path.cwd()).resolve()

    # 1. folder pattern match — most specific (longest pattern) wins
    candidates: list[tuple[int, Profile]] = []
    for prof in cfg.profiles.values():
        for pat in prof.folder_patterns:
            expanded = os.path.expanduser(pat)
            if fnmatch.fnmatch(str(target_dir), expanded) or str(target_dir).startswith(
                expanded.rstrip("*").rstrip("/")
            ):
                candidates.append((len(expanded), prof))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    # 2. host alias match against remote URL
    remote = url or repo_remote(cwd)
    if remote:
        parsed = parse_repo_url(remote)
        if parsed:
            host = parsed["host"]
            for prof in cfg.profiles.values():
                if prof.host_alias and prof.host_alias == host:
                    return prof

    return None


# ---------- url rewriting ----------

def rewrite_url_with_alias(url: str, host_alias: str) -> str:
    """Rewrite a github URL to use a host alias (for SSH config-based key selection)."""
    parsed = parse_repo_url(url)
    if not parsed:
        return url
    return f"git@{host_alias}:{parsed['owner']}/{parsed['repo']}.git"


def rewrite_url_with_token(url: str, username: str, token: str) -> str:
    """Inject username:token into an https URL."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return url
    netloc = f"{username}:{token}@{p.hostname}"
    if p.port:
        netloc += f":{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


# ---------- git config application ----------

def apply_to_repo(repo: Path, profile: Profile, *, set_remote: bool = False) -> None:
    if not in_git_repo(repo):
        die(f"{repo} is not a git repository")
    run_git(["config", "user.name", profile.username], cwd=repo)
    run_git(["config", "user.email", profile.email], cwd=repo)

    if profile.ssh_key:
        key = os.path.expanduser(profile.ssh_key)
        if not os.path.exists(key):
            warn(f"ssh key {key} does not exist")
        ssh_cmd = f"ssh -i {shlex.quote(key)} -o IdentitiesOnly=yes"
        run_git(["config", "core.sshCommand", ssh_cmd], cwd=repo)
    else:
        run_git(["config", "--unset", "core.sshCommand"], cwd=repo, check=False)

    if set_remote and profile.host_alias:
        remote = repo_remote(repo)
        if remote:
            new = rewrite_url_with_alias(remote, profile.host_alias)
            if new != remote:
                run_git(["remote", "set-url", "origin", new], cwd=repo)


def build_clone_env(profile: Profile) -> dict:
    env = os.environ.copy()
    if profile.ssh_key:
        key = os.path.expanduser(profile.ssh_key)
        env["GIT_SSH_COMMAND"] = f"ssh -i {shlex.quote(key)} -o IdentitiesOnly=yes"
    return env


# ---------- commands ----------

def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not cfg.profiles:
        print("(no profiles configured — run `ghswitch add`)")
        return 0
    width = max(len(n) for n in cfg.profiles)
    for name, p in cfg.profiles.items():
        marker = "*" if name == cfg.default else " "
        auth = []
        if p.ssh_key:
            auth.append(f"ssh:{p.ssh_key}")
        if p.has_token:
            auth.append("token")
        print(f"{marker} {name:<{width}}  {p.username} <{p.email}>  [{', '.join(auth) or 'no auth'}]")
        if p.host_alias:
            print(f"    host_alias = {p.host_alias}")
        if p.folder_patterns:
            print(f"    folders    = {', '.join(p.folder_patterns)}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    cfg = load_config()
    name = args.name or prompt("profile name")
    if not name:
        die("profile name required")
    if name in cfg.profiles and not args.force:
        die(f"profile {name!r} already exists (use --force to overwrite)")

    interactive = sys.stdin.isatty()
    username = args.username or (prompt("github username") if interactive else None)
    email = args.email or (prompt("git email") if interactive else None)
    if not username or not email:
        die("--username and --email are required (non-interactive mode)")
    if args.ssh_key is not None:
        ssh_key = args.ssh_key
    elif interactive:
        ssh_key = prompt("ssh key path (blank to skip)", default="") or None
    else:
        ssh_key = None
    if args.host_alias is not None:
        host_alias = args.host_alias
    elif interactive:
        host_alias = prompt("ssh host alias (blank for github.com)", default="") or None
    else:
        host_alias = None
    if args.folder is not None:
        folders_raw = args.folder
    elif interactive:
        folders_raw = prompt(
            "auto-switch folder patterns, comma-separated (optional)", default=""
        )
    else:
        folders_raw = ""
    if isinstance(folders_raw, list):
        folder_patterns = folders_raw
    else:
        folder_patterns = [s.strip() for s in folders_raw.split(",") if s.strip()]

    has_token = False
    if args.token_stdin:
        token = sys.stdin.read().strip()
        if token:
            store_token(name, token)
            has_token = True
    elif args.prompt_token:
        token = prompt("personal access token", secret=True)
        if token:
            store_token(name, token)
            has_token = True

    cfg.profiles[name] = Profile(
        name=name,
        username=username,
        email=email,
        ssh_key=ssh_key,
        host_alias=host_alias,
        folder_patterns=folder_patterns,
        has_token=has_token,
    )
    if cfg.default is None or args.set_default:
        cfg.default = name
    save_config(cfg)
    print(f"added profile {name!r}")
    if not _HAS_KEYRING and has_token:
        warn("keyring not available — token stored in ~/.config/ghswitch/secrets.json (0600)")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.name not in cfg.profiles:
        die(f"no such profile: {args.name}")
    del cfg.profiles[args.name]
    if cfg.default == args.name:
        cfg.default = next(iter(cfg.profiles), None)
    delete_token(args.name)
    save_config(cfg)
    print(f"removed profile {args.name!r}")
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.profile not in cfg.profiles:
        die(f"no such profile: {args.profile}")
    repo = Path.cwd()
    if not in_git_repo(repo):
        die("not inside a git repository")
    apply_to_repo(repo, cfg.profiles[args.profile], set_remote=args.rewrite_remote)
    print(f"profile {args.profile!r} applied to {repo}")
    return 0


def cmd_clone(args: argparse.Namespace) -> int:
    cfg = load_config()
    url = args.url
    profile_name = args.profile

    if profile_name is None:
        detected = detect_profile(cfg, url=url)
        if detected:
            profile_name = detected.name
            print(f"(auto-detected profile: {profile_name})", file=sys.stderr)
        elif cfg.default:
            profile_name = cfg.default
            print(f"(using default profile: {profile_name})", file=sys.stderr)
        else:
            die("could not determine profile — pass one explicitly or set a default")

    profile = cfg.profiles.get(profile_name)
    if profile is None:
        die(f"no such profile: {profile_name}")

    target = args.directory
    final_url = url

    if profile.host_alias and url.startswith(("git@", "ssh://")):
        final_url = rewrite_url_with_alias(url, profile.host_alias)
    elif url.startswith(("http://", "https://")) and profile.has_token:
        token = get_token(profile.name)
        if token:
            final_url = rewrite_url_with_token(url, profile.username, token)
        else:
            warn("profile claims a token but none found in keyring")

    cmd = ["clone", final_url]
    if target:
        cmd.append(target)

    env = build_clone_env(profile)
    try:
        run_git(cmd, env=env)
    except subprocess.CalledProcessError as e:
        die(f"git clone failed (exit {e.returncode})", code=e.returncode)

    # Determine cloned dir to set per-repo identity
    if not target:
        parsed = parse_repo_url(url)
        target = parsed["repo"] if parsed else None
    if target and Path(target).is_dir():
        apply_to_repo(Path(target), profile)
        # If the URL was https, also set credential helper hint via the URL itself —
        # but we don't want to persist token in the remote URL on disk
        if url.startswith(("http://", "https://")) and profile.has_token:
            run_git(["remote", "set-url", "origin", url], cwd=Path(target))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    repo = Path.cwd()
    if not in_git_repo(repo):
        die("not inside a git repository")

    name_r = run_git(["config", "--get", "user.name"], cwd=repo, check=False, capture=True)
    email_r = run_git(["config", "--get", "user.email"], cwd=repo, check=False, capture=True)
    ssh_r = run_git(["config", "--get", "core.sshCommand"], cwd=repo, check=False, capture=True)
    cur_name = name_r.stdout.strip()
    cur_email = email_r.stdout.strip()
    cur_ssh = ssh_r.stdout.strip()
    remote = repo_remote(repo)

    # match against profiles
    matched = None
    for p in cfg.profiles.values():
        if p.username == cur_name and p.email == cur_email:
            matched = p
            break

    print(f"repo:       {repo}")
    print(f"remote:     {remote or '(none)'}")
    print(f"user.name:  {cur_name or '(unset)'}")
    print(f"user.email: {cur_email or '(unset)'}")
    if cur_ssh:
        print(f"ssh:        {cur_ssh}")
    if matched:
        print(f"profile:    {matched.name}  <- matches stored profile")
    else:
        suggested = detect_profile(cfg, cwd=repo)
        if suggested:
            print(f"profile:    (unset, suggested: {suggested.name})")
        else:
            print("profile:    (unmatched)")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    cfg = load_config()
    print(cfg.default or "(no default)")
    return 0


COMPLETION_BASH = r"""# ghswitch bash completion
_ghswitch() {
  local cur prev cmds
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  cmds="list add remove use clone status whoami completion"
  if [[ ${COMP_CWORD} -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "${cmds}" -- "$cur") )
    return 0
  fi
  case "$prev" in
    use|remove|clone)
      local profiles
      profiles=$(ghswitch list 2>/dev/null | awk 'NF>=2 && $1!~"^---" {print $2}' | grep -v '=')
      COMPREPLY=( $(compgen -W "${profiles}" -- "$cur") )
      ;;
  esac
}
complete -F _ghswitch ghswitch
"""


def cmd_completion(args: argparse.Namespace) -> int:
    if args.shell == "bash":
        print(COMPLETION_BASH)
    elif args.shell == "zsh":
        # Zsh can source bash completion via bashcompinit
        print("autoload -U +X bashcompinit && bashcompinit")
        print(COMPLETION_BASH)
    else:
        die(f"unsupported shell: {args.shell}")
    return 0


# ---------- arg parser ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ghswitch", description="Manage multiple GitHub identities.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list configured profiles").set_defaults(func=cmd_list)

    a = sub.add_parser("add", help="add or update a profile")
    a.add_argument("name", nargs="?")
    a.add_argument("--username")
    a.add_argument("--email")
    a.add_argument("--ssh-key")
    a.add_argument("--host-alias", help="SSH host alias to use, e.g. github.com-work")
    a.add_argument("--folder", action="append",
                   help="folder pattern for auto-switching (repeatable)")
    a.add_argument("--prompt-token", action="store_true", help="prompt for a PAT (hidden input)")
    a.add_argument("--token-stdin", action="store_true", help="read PAT from stdin")
    a.add_argument("--set-default", action="store_true")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("remove", help="remove a profile")
    r.add_argument("name")
    r.set_defaults(func=cmd_remove)

    u = sub.add_parser("use", help="apply a profile to the current repo")
    u.add_argument("profile")
    u.add_argument("--rewrite-remote", action="store_true",
                   help="rewrite origin URL to use the profile's host_alias")
    u.set_defaults(func=cmd_use)

    c = sub.add_parser("clone", help="git clone using a profile")
    c.add_argument("url")
    c.add_argument("profile", nargs="?")
    c.add_argument("directory", nargs="?")
    c.set_defaults(func=cmd_clone)

    sub.add_parser("status", help="show active profile in current repo").set_defaults(func=cmd_status)
    sub.add_parser("whoami", help="show default profile").set_defaults(func=cmd_whoami)

    cm = sub.add_parser("completion", help="emit shell completion script")
    cm.add_argument("shell", choices=["bash", "zsh"])
    cm.set_defaults(func=cmd_completion)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if shutil.which("git") is None:
        die("git is not installed or not on PATH")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
