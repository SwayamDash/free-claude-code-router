# Changelog

All user-visible changes land here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-05-02

First public release as **Quench** (forked from `Alishahryar1/free-claude-code` and rebranded). Headline: per-slot fallback chains with transparent quench-aware failover, plus a one-command install (`./setup.sh && ./quench start`).

### Added

- One-command setup script (`./setup.sh`) that installs `uv`, pins Python 3.14, syncs dependencies, prepares `.env`, and walks through provider keys interactively.
- Cross-platform `quench` proxy manager script with `start`, `stop`, `restart`, `status`, and `logs` subcommands. Detects VSCode settings path on macOS, Linux, and Windows (WSL/MSYS). Defaults to loopback binding for safety.
- Per-slot fallback chains for `MODEL_OPUS`, `MODEL_SONNET`, `MODEL_HAIKU`, and `MODEL`. Pipe-separated entries are walked left-to-right; failed entries are quenched in an in-memory TTL registry and the next entry is tried transparently. Cooldowns: 1h for auth/quota (401/403), 60s for rate limits (429), 30s for overload (5xx, 529).
- Quench-aware routing logic in `api/services.py` with bounded `asyncio.Queue` for backpressure and `message_start` chunk buffering to allow fallback after the leading SSE event.
- New `raise_on_upstream_error` flag on `BaseProvider.stream_response`. Default preserves existing in-stream SSE error behavior; chain pump opts in for non-final entries.
- `MAX_CHAIN_LENGTH` cap (8 entries) on chain config to bound per-request walk cost.
- 17 new tests across `tests/api/`, `tests/core/`, `tests/config/`, and `tests/providers/` covering chain fallback, message_start buffering, chain exhaustion, quench TTL, concurrent quench writes, and chain-length validation.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, GitHub issue templates, and PR template.
- `.github/workflows/fresh-clone.yml` CI job validates that a brand-new clone runs `setup.sh` cleanly and that `quench` parses without syntax errors.

### Changed

- `api/routes.py` route handler for `/v1/messages` is now async (awaits the coroutine).
- `.gitignore` now blocks `.env.*` (e.g. `.env.bak`) to prevent accidental secret leaks via backup files.
- `quench start` now polls `http://$HOST:$PORT/` for readiness (up to 10s, 100ms ticks) instead of a blind `sleep 2`. Override via `QUENCH_START_TIMEOUT`. Removes a startup race that occasionally caused the proxy to be reported as failed when it was still binding.

### Security

- `CHAIN_PROVIDER_INIT_FAIL` log now records only the exception type by default. Full exception text (which can echo API keys via Pydantic validation errors) is gated behind `LOG_API_ERROR_TRACEBACKS=true`.
- The three chain log paths (`CHAIN_PREFLIGHT_FALLBACK`, `CHAIN_FALLBACK`, `CHAIN_MIDSTREAM_ERROR`) follow the same convention.
- `quench` defaults to `--host 127.0.0.1` instead of `0.0.0.0`. Override via `QUENCH_HOST` if you need LAN access; in that case, set `ANTHROPIC_AUTH_TOKEN`.
- gitleaks scan of full git history (524 commits) is clean as of v0.1.0; only false positives in upstream `.env.example` placeholders.

### Repo identity

- This repo is now an actively maintained fork of [Alishahryar1/free-claude-code](https://github.com/Alishahryar1/free-claude-code), differentiated from [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router).

[Unreleased]: https://github.com/SwayamDash/quench/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SwayamDash/quench/releases/tag/v0.1.0
