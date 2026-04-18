# Changelog

All notable changes to KODA are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-04-18

### Added
- Claude-Code-style slash menu, smart submit, and thinking indicator in the TUI
- Slash + `@` autocomplete with arrow-key navigation
- `/quit`, `/exit`, and `Ctrl+D` as explicit exit paths
- Model discovery cache (~24h TTL) — first `/model` call ~346ms, subsequent calls cached
- Skills scanned from the local workspace (replacing the GitHub downloader)
- Deepagents backend example under `examples/` (BYOA demo)

### Changed
- TUI replaced: `deepagents-cli` dependency dropped in favor of an in-tree
  Textual app (Phase 2 of the decoupling)
- Agent layer decoupled from `deepagents` (Phase 1)

### Fixed
- Skills not loading on Windows because `virtual_mode=False` resolved the
  agent's `/skills` path to the `C:\` drive root instead of the workspace

## [0.3.0] - Initial public release

- Initial commit of KODA (deepagents-cli based TUI)

[Unreleased]: https://github.com/Badar-e-Alam/KODA/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Badar-e-Alam/KODA/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Badar-e-Alam/KODA/releases/tag/v0.3.0
