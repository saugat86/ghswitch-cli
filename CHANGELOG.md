## [v1.0.0] - 2026-05-18

**Full Changelog**: https://github.com/saugat86/ghswitch-cli/compare/v0.1.1...v1.0.0

## [v0.1.1] - 2026-05-18

**Full Changelog**: https://github.com/saugat86/ghswitch-cli/compare/v0.1.0...v0.1.1

# Changelog

## [0.1.1] - 2026-05-18

### Fixed
- Fix `EOFError` crash on Windows when stdin is not a real TTY (e.g. CI environments)

### Changed
- Published on PyPI as `ghswitch-cli` (install via `pip install ghswitch-cli`)
- Release pipeline now supports `workflow_dispatch` — trigger releases directly from GitHub Actions UI without needing a local git tag

## [0.1.0] - 2026-05-18

### Added
- Initial release
- Manage multiple GitHub identities (personal, work, client) across git operations
- Store profile metadata in `$XDG_CONFIG_HOME/ghswitch/profiles.json`
- Store Personal Access Tokens in OS keychain via `keyring` (macOS Keychain, Windows Credential Manager, GNOME Secret Service) with fallback to a `0600` JSON file
- Auto-detect the right profile from folder patterns or SSH host alias in `origin` URL
- Commands: `add`, `list`, `remove`, `use`, `clone`, `status`, `whoami`, `completion`
- Shell completion for bash and zsh
- CI matrix: Linux, macOS, Windows × Python 3.9, 3.11, 3.13
