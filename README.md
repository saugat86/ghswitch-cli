# ghswitch

A small CLI for juggling multiple GitHub identities (personal, work, client, …)
across `git clone`, `git push`, and per-repo `user.name` / `user.email`.

- Stores profile metadata in `$XDG_CONFIG_HOME/ghswitch/profiles.json` (0600)
- Stores Personal Access Tokens in the **OS keychain** via `keyring` (macOS
  Keychain, Windows Credential Manager, GNOME Secret Service). Falls back to a
  0600 JSON file if `keyring` isn't installed.
- Picks the right profile from **folder patterns** or the **SSH host alias** in
  the repo's `origin` URL.
- Cross-platform: Linux, macOS, Windows (WSL or native Python).

## Install

```bash
# from this directory
pip install .                  # core
pip install '.[keyring]'       # recommended — uses OS keychain for tokens
```

That installs a `ghswitch` console script. Verify with `ghswitch --help`.

If you'd rather not install: `python3 ghswitch.py …` works the same way.

## Setup walkthrough

### 1. Generate distinct SSH keys (recommended)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_personal -C "you@personal"
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_work     -C "you@work"
```

Add each public key to the respective GitHub account.

### 2. Define SSH host aliases

Append to `~/.ssh/config`:

```
Host github.com-personal
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_personal
    IdentitiesOnly yes

Host github.com-work
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_work
    IdentitiesOnly yes
```

### 3. Register profiles

```bash
ghswitch add personal \
  --username alice \
  --email alice@personal.dev \
  --ssh-key ~/.ssh/id_ed25519_personal \
  --host-alias github.com-personal \
  --folder '~/code/personal/*' \
  --set-default

ghswitch add work \
  --username alice-corp \
  --email alice@workco.com \
  --ssh-key ~/.ssh/id_ed25519_work \
  --host-alias github.com-work \
  --folder '~/code/work/*'
```

For HTTPS + PAT instead of SSH, add `--prompt-token` (hidden input) or
`--token-stdin` (read from stdin), and skip `--ssh-key`/`--host-alias`.

## Commands

| Command | Purpose |
| --- | --- |
| `ghswitch list` | List all profiles. The default is marked with `*`. |
| `ghswitch add <name> [...]` | Add or update a profile. With no flags, prompts interactively. |
| `ghswitch remove <name>` | Delete a profile and its stored token. |
| `ghswitch use <name>` | Apply a profile to the current repo (`user.name`, `user.email`, `core.sshCommand`). Add `--rewrite-remote` to also flip the origin URL to the profile's host alias. |
| `ghswitch clone <url> [profile] [dir]` | Clone with the chosen profile; auto-detects from URL host alias / folder patterns / default. |
| `ghswitch status` | Show the current repo's identity and which profile (if any) it matches. |
| `ghswitch whoami` | Print the default profile name. |
| `ghswitch completion bash\|zsh` | Emit a completion script. |

## Example workflows

### Cloning into a work folder, hands-off

`work` has `--folder '~/code/work/*'` configured.

```bash
cd ~/code/work
ghswitch clone git@github.com:workco/api.git
# → auto-detects "work", rewrites URL to git@github.com-work:workco/api.git,
#   sets core.sshCommand to use the work key, and pins user.name/email.
```

### Pushing from a personal repo

```bash
cd ~/code/personal/dotfiles
ghswitch use personal --rewrite-remote
git push
```

`use --rewrite-remote` flips `origin` to `git@github.com-personal:owner/repo.git`
so subsequent `git pull`/`push` ride the right SSH key without env vars.

### Verifying

```bash
ghswitch status
# repo:       /Users/alice/code/work/api
# remote:     git@github.com-work:workco/api.git
# user.name:  alice-corp
# user.email: alice@workco.com
# ssh:        ssh -i /Users/alice/.ssh/id_ed25519_work -o IdentitiesOnly=yes
# profile:    work  <- matches stored profile
```

## Shell completion

```bash
# bash
ghswitch completion bash >> ~/.bash_completion
# zsh
ghswitch completion zsh > ~/.local/share/zsh-completions/_ghswitch
```

## Auto-switching rules

When a profile match is needed (e.g. during `clone` without an explicit
profile), `ghswitch` checks, in order:

1. **Folder patterns** — longest-matching pattern wins. Patterns support `~`
   expansion and `fnmatch`-style globs.
2. **SSH host alias** — if `origin` (or the URL passed to `clone`) uses a host
   matching a profile's `host_alias`.
3. **Default profile** — set via `add --set-default` or implicitly the first
   profile added.

If none of these resolve and you didn't pass a name, `clone` exits with an
error.

## GitHub CLI integration

`gh` is not required, but if it's installed `ghswitch` plays well with it:
SSH-based clones go through your `~/.ssh/config` host aliases regardless of
which tool you use, and HTTPS clones can use the PAT stored under the matching
profile in your OS keychain.

## Security notes

- `profiles.json` and the fallback `secrets.json` are written `0600`.
- PATs are never written into git remote URLs on disk; for HTTPS clones the
  token is injected into the URL only for the `git clone` invocation, and the
  remote is reset to the canonical URL afterward.
- `core.sshCommand` is set with `IdentitiesOnly=yes` so SSH won't fall back to
  another agent-loaded key.
- Removing a profile also deletes its token from the keychain.

## Development

```bash
pip install -e '.[dev]'
pytest
```

CI is in `.github/workflows/`:
- **test.yml** — runs `pytest` on Linux, macOS, and Windows across Python 3.9 / 3.11 / 3.13 on every push and PR.
- **release.yml** — on `vX.Y.Z` tag pushes, builds sdist + wheel, verifies the tag matches `pyproject.toml`'s `version`, and creates a GitHub Release with auto-generated notes. PyPI trusted publishing is wired up but commented out — uncomment the `pypi-publish` job after configuring [trusted publishing](https://docs.pypi.org/trusted-publishers/) for the project.

To cut a release:

```bash
# bump version in pyproject.toml, commit, then:
git tag v0.1.0
git push origin v0.1.0
```

## Uninstall

```bash
pip uninstall ghswitch
rm -rf ~/.config/ghswitch        # Linux/macOS
# or %APPDATA%\ghswitch on Windows
```
