# Security policy

## Reporting a vulnerability

If you find a security issue, please do **not** open a public GitHub issue. Instead, email the maintainer privately:

**swayam.dash@unscript.ai**

Or use GitHub's private vulnerability reporting:
[https://github.com/SwayamDash/quench/security/advisories/new](https://github.com/SwayamDash/quench/security/advisories/new)

We aim to respond within 48 hours and patch within 7 days for high-severity issues.

## What counts as a security issue

- Anything that lets an unauthenticated request reach upstream providers using your configured API keys.
- Anything that leaks API keys, OAuth tokens, or upstream response bodies into logs by default.
- Anything that lets a request exploit the proxy to scan internal networks (SSRF), read arbitrary files (path traversal), or execute arbitrary code.
- Authentication bypasses on the proxy auth token.

## Configuration that affects security

- `ANTHROPIC_AUTH_TOKEN` in `.env`: when set, the proxy requires this bearer token on every request. **Always set this if you bind the proxy to anything other than `127.0.0.1`.**
- `--host` flag (default `127.0.0.1` in `quench`): controls which network interfaces the proxy binds to. `0.0.0.0` exposes it on every interface; only do this on a network you trust and with `ANTHROPIC_AUTH_TOKEN` set. Override the default via `QUENCH_HOST`.
- `LOG_RAW_API_PAYLOADS`, `LOG_RAW_SSE_EVENTS`, `LOG_API_ERROR_TRACEBACKS`, `LOG_RAW_MESSAGING_CONTENT`, `LOG_RAW_CLI_DIAGNOSTICS`, `LOG_MESSAGING_ERROR_DETAILS`: opt-in flags. Off by default. Turning any of them on may write request payloads, exception text, or messaging content to logs. Useful for debugging, dangerous for production.
- `WEB_FETCH_ALLOW_PRIVATE_NETWORKS`: off by default. Setting it on lets the local web-search/web-fetch tools reach private RFC1918 ranges. Only enable if you trust every model that can issue a tool call.
- The chain-fallback quench registry stores model identifiers in memory only. State is never persisted to disk.

## Threat model

Quench is a **local proxy** designed to run alongside Anthropic-protocol clients (Claude Code, Cursor with the Anthropic adapter, custom apps using the Anthropic SDK) on a developer machine. It is not hardened for multi-tenant production use. Operators are expected to:

- Keep `.env` outside source control (already gitignored).
- Bind to loopback unless they explicitly need LAN access.
- Set `ANTHROPIC_AUTH_TOKEN` whenever the proxy is reachable from anywhere other than the loopback interface.
- Treat any provider key in `.env` as a credential that grants quota burn or paid usage to whoever can reach the proxy.

## What we will not patch as a "vulnerability"

- Misconfiguration the operator chose intentionally (e.g. `--host 0.0.0.0` with no auth token on a hostile network). We document the safe defaults; we don't ship overrides that prevent operator choice.
- Issues in upstream providers (NVIDIA NIM, OpenRouter, etc.). Report those to the provider directly.
- Issues in Claude Code, the Anthropic SDK, or the VSCode extension. Report those to Anthropic.
