# Contributing

Thanks for thinking about contributing. This project moves fastest when changes come with tests and a clear "why".

## Development setup

```bash
git clone https://github.com/SwayamDash/quench.git
cd quench
./setup.sh
```

`setup.sh` installs `uv`, pins Python 3.14, runs `uv sync`, and prepares `.env`.

## Workflow

1. Open an issue first for non-trivial changes so we can align on direction.
2. Create a feature branch off `main`: `git checkout -b feat/your-thing`.
3. Add tests for new behavior (we aim for full coverage; see `tests/`).
4. Run the full CI gauntlet locally before pushing:
   ```bash
   uv run ruff format
   uv run ruff check
   uv run ty check
   uv run pytest tests/ --ignore=tests/integration
   ```
5. Open a PR. Reference the issue. Describe what changed and why.

## Style

- Python: idiomatic, typed. No `# type: ignore` or `# ty: ignore`. Fix the underlying type problem instead.
- Comments and commit messages: write naturally, like you'd talk to a colleague. No filler. No em dashes.
- Architecture: see [AGENTS.md](AGENTS.md). Keep `config/` free of runtime imports. Provider-specific config lives on the provider, not on `BaseProvider`.
- Tests: prefer real integration over heavy mocking where feasible. The chain-fallback tests use a real `MessagesRequest` flow with a mock provider rather than mocking pydantic models.

## What we accept

- Bug fixes with a regression test.
- New providers that follow the existing `BaseProvider` / `AnthropicMessagesTransport` / `OpenAIChatTransport` patterns.
- New chain features (other quench triggers, smarter retry strategies).
- Documentation improvements.
- Performance work backed by a benchmark.

## What we usually don't accept

- Pure refactors without a clear correctness or maintainability win.
- Style-only changes that fight the existing `ruff` formatter config.
- Breaking changes to public env-var names without a migration path.

## Releasing

Versioned tags + GitHub Releases ship from `main` after CI is green. CHANGELOG entries are required for any user-visible change.

## Questions

Open a GitHub Discussion or Issue. There's no Slack or Discord yet.
