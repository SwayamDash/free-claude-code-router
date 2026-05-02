<div align="center">

# 🧊 Quench

**An Anthropic-API proxy with auto-failover.**
Per-slot pipe-separated fallback chains across NVIDIA NIM, OpenRouter, DeepSeek, Ollama, LM Studio, llama.cpp. Drop-in for Claude Code, Cursor, and any Anthropic SDK client. Configure once, and Quench cools failing providers and routes around them.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776ab.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![CI](https://github.com/SwayamDash/quench/actions/workflows/tests.yml/badge.svg)](https://github.com/SwayamDash/quench/actions/workflows/tests.yml)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-f5a623.svg)](https://github.com/astral-sh/ruff)
[![Type checking: ty](https://img.shields.io/badge/type%20checking-ty-ffcc00.svg)](https://pypi.org/project/ty/)

[Quick Start](#quick-start) · [Providers](#supported-providers) · [API Keys](#api-key-setup) · [Fallback Chains](#fallback-chains-and-the-quench-registry) · [Configuration](#configuration) · [Compare](#how-quench-compares) · [Contributing](#contributing)

![Quench routing demo](docs/demo.gif)

</div>

---

## What and why

Quench is a local proxy that speaks the Anthropic Messages API. It sits between your client (Claude Code, Cursor, any Anthropic SDK consumer) and one or more upstream LLM providers. Configure a pipe-separated chain per Claude model slot; when one provider fails, Quench *quenches* it for a TTL and routes to the next entry, transparently.

You hit Claude rate limits mid-session. Subscription credit runs out. A provider has a flaky weekend. Existing options: switch SDKs and break your workflow, run an OpenAI-format proxy and convert every tool, or hand-roll retry logic per provider per error code. Quench's answer: keep your client unmodified, configure once, let the proxy handle failover.

```dotenv
MODEL_SONNET="nvidia_nim/qwen/qwen3-coder-480b|nvidia_nim/openai/gpt-oss-120b|open_router/google/gemma-4-31b-it:free"
```

On 401 (auth/quota), 429 (rate limit), or 5xx (overloaded), the active entry is held cold for a TTL (1 hour, 60 seconds, 30 seconds respectively) and the next entry takes over. Even mid-stream: a `message_start` SSE buffer ensures clients see one clean stream when failover happens before any content has been emitted.

This repo started as a fork of [Alishahryar1/free-claude-code](https://github.com/Alishahryar1/free-claude-code), rebuilt around the chain-fallback feature and rebranded as Quench.

## 🚀 Quick Start

```bash
git clone https://github.com/SwayamDash/quench.git
cd quench
./setup.sh        # installs uv, syncs deps, prepares .env, prompts for keys
./quench start    # starts the proxy, points Claude Code and VSCode at it
```

That's the whole install. `./quench stop` reverts to the official Anthropic API. `./quench status` shows current state. `./quench logs` tails the proxy log.

### Prerequisites

- [Claude Code](https://github.com/anthropics/claude-code) installed.
- At least one provider key, or one local provider running.
- `bash`, `curl`, `jq` (auto-installed by `./setup.sh` where missing).

### Manual install (skip if you ran `./setup.sh`)

```bash
# install uv (Astral)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Quench requires Python 3.14
uv python install 3.14

# install deps
uv sync

# copy env template, fill in keys
cp .env.example .env

# start the proxy
./quench start
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.14
uv sync
copy .env.example .env
.\quench start
```

## 📝 Quick Example

The minimum config is a single line in `.env` mapping a Claude model slot to an upstream model:

```dotenv
# single-provider mapping
MODEL_SONNET="nvidia_nim/qwen/qwen3-coder-480b-a35b-instruct"

# chain: try first; on auth/rate-limit/overload, try second; then third
MODEL_OPUS="nvidia_nim/qwen/qwen3-coder-480b|nvidia_nim/openai/gpt-oss-120b|open_router/anthropic/claude-3.5-sonnet:beta"

# fully local, no API keys
MODEL_HAIKU="ollama/llama3.2|lmstudio/local-model"
```

Format: `provider_id/model_path[|next_provider_id/model_path|...]`. Provider IDs are `nvidia_nim`, `open_router`, `deepseek`, `ollama`, `lmstudio`, `llamacpp`. The model path is whatever string the upstream uses for the model name.

Once `.env` is configured, `./quench start` brings the proxy up on `127.0.0.1:8082` and points Claude Code at it. Use Claude Code normally; routing happens behind the scenes.

## 🔌 Supported Providers

Quench supports six provider backends, grouped by what you need to use them:

**Free-tier (with key):**

| Provider | Free quota | Signup |
|---|---|---|
| **NVIDIA NIM** | 40 requests / minute | [build.nvidia.com](https://build.nvidia.com/settings/api-keys) |
| **OpenRouter** | Free models on `:free` suffix; small monthly credits | [openrouter.ai/keys](https://openrouter.ai/keys) |

**Paid (with key):**

| Provider | Pricing | Signup |
|---|---|---|
| **DeepSeek** | Pay-as-you-go, ~$0.14/M tokens | [platform.deepseek.com](https://platform.deepseek.com/api_keys) |

**Local (no key required):**

| Provider | Setup | Default URL |
|---|---|---|
| **Ollama** | `curl -fsSL https://ollama.com/install.sh \| sh && ollama serve` | `http://localhost:11434` |
| **LM Studio** | [Download](https://lmstudio.ai) → Local Server tab → Start | `http://localhost:1234/v1` |
| **llama.cpp** | Build / install `llama-server`, run `llama-server -m model.gguf` | `http://localhost:8080/v1` |

## 🔑 API Key Setup

Step-by-step for each provider. Skip the ones you don't plan to use.

### NVIDIA NIM (recommended free tier)

1. Sign up at [build.nvidia.com](https://build.nvidia.com) (free, requires NVIDIA developer account).
2. Visit the API keys page: [build.nvidia.com/settings/api-keys](https://build.nvidia.com/settings/api-keys).
3. Click **Generate API Key**. Copy the key.
4. In `.env`, set:
   ```dotenv
   NVIDIA_NIM_API_KEY="nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   ```
5. Free tier: 40 requests per minute. Quench will hold a 60-second cooldown if you hit the rate limit and route to the next chain entry.

Recommended models on NVIDIA NIM: `qwen/qwen3-coder-480b-a35b-instruct` (coding), `openai/gpt-oss-120b` (general).

### OpenRouter (most provider variety)

1. Sign up at [openrouter.ai](https://openrouter.ai) (Google or GitHub OAuth).
2. Get your key at [openrouter.ai/keys](https://openrouter.ai/keys). Click **Create Key**.
3. In `.env`, set:
   ```dotenv
   OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx"
   ```
4. Browse free models with the `:free` suffix at [openrouter.ai/models?max_price=0](https://openrouter.ai/models?max_price=0). Examples: `google/gemma-4-31b-it:free`, `mistralai/mistral-7b-instruct:free`.
5. Free models are rate-limited; chain them with NVIDIA NIM for resilience.

### DeepSeek (cheap paid)

1. Sign up at [platform.deepseek.com](https://platform.deepseek.com).
2. Get a key at [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys).
3. Add credit (minimum $1).
4. In `.env`, set:
   ```dotenv
   DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   ```
5. Pricing is roughly an order of magnitude below GPT-4-class APIs. Useful as a paid backstop.

### Ollama (fully local, free)

1. Install: `curl -fsSL https://ollama.com/install.sh | sh` (macOS/Linux) or `winget install Ollama.Ollama` (Windows).
2. Pull a model: `ollama pull llama3.2` (or whatever fits your hardware).
3. Start the server: `ollama serve` (or just `ollama run <model>` for one-shot).
4. No API key. Set in `.env`:
   ```dotenv
   OLLAMA_BASE_URL="http://localhost:11434"
   ```
5. Reference in chains as `ollama/<model_name>`. Works offline.

### LM Studio (local GUI)

1. Download from [lmstudio.ai](https://lmstudio.ai).
2. Browse and download a model in the GUI.
3. Click the **Local Server** tab. Click **Start Server**.
4. No API key. Set in `.env`:
   ```dotenv
   LM_STUDIO_BASE_URL="http://localhost:1234/v1"
   ```
5. Reference in chains as `lmstudio/<model_name>`.

### llama.cpp (lightweight local)

1. Install or build `llama.cpp`. See [github.com/ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp).
2. Run with the OpenAI-compatible server: `llama-server -m path/to/model.gguf --port 8080`.
3. No API key. Set in `.env`:
   ```dotenv
   LLAMACPP_BASE_URL="http://localhost:8080/v1"
   ```
4. Reference in chains as `llamacpp/<model_name>`.

![Get API keys in 5 minutes](docs/api-keys.gif)

## 🔄 Fallback Chains and the Quench Registry

This is the headline feature. Three layers of failover, all transparent to the client:

**Inter-turn failover (per request).** The quench registry persists in-process across requests. Once a provider has been quenched on turn N, turn N+1 skips it and starts from the next chain entry. Restoration is automatic when the TTL expires.

**Pre-stream failover.** Before opening the upstream HTTP stream, Quench runs a preflight check. If the provider returns a retryable error (auth, rate limit, overload), the chain advances and the client sees no failure.

**In-stream-but-pre-content failover.** Anthropic SSE streams begin with a `message_start` event. Most providers emit `message_start` *before* the upstream HTTP call completes, so an early 401 or 429 arrives after the first SSE chunk has hit the wire. Quench buffers `message_start` and only flushes it once the upstream commits to a real response. If the first provider dies after `message_start` but before any content, the buffer is dropped silently and the next chain entry takes over from a clean state.

**TTL defaults**, tunable per call:

| Error class | TTL | Why |
|---|---|---|
| Auth (401/403) | 1 hour | Quota or credit exhaustion. Long backoff. |
| Rate limit (429) | 60 seconds | Most rate-limit windows reset within a minute. |
| Overloaded (5xx, 529) | 30 seconds | Server-side recovery is usually fast. |
| Other API error | 15 seconds | Fallback default. |

The registry is in-memory only. Restart Quench and all entries clear. Single-entry chains skip quench entirely so legitimate client retries aren't refused.

True mid-stream failures (the upstream dies *after* content has been streamed to the client) cannot be recovered without buffering full responses. Quench gracefully ends the SSE in that case so the client doesn't hang.

## ⚙️ Configuration

`.env` covers everything. Highlights:

| Variable | Default | Purpose |
|---|---|---|
| `MODEL_OPUS`, `MODEL_SONNET`, `MODEL_HAIKU`, `MODEL` | empty / `nvidia_nim/z-ai/glm4.7` | Per-Claude-model chain. Empty means inherit `MODEL`. |
| `ENABLE_OPUS_THINKING`, `ENABLE_SONNET_THINKING`, `ENABLE_HAIKU_THINKING`, `ENABLE_MODEL_THINKING` | true | Pass thinking blocks to the upstream when supported. |
| `ANTHROPIC_AUTH_TOKEN` | empty | If set, Quench requires this bearer token from clients. **Set this whenever the proxy is reachable from anywhere other than loopback.** |
| `QUENCH_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` exposes Quench on the LAN. |
| `QUENCH_PORT` | `8082` | Listen port. |
| `QUENCH_LOG` | `/tmp/quench.log` | Log file path. |
| `QUENCH_START_TIMEOUT` | `10` | Seconds to wait for uvicorn to bind on `quench start`. |
| `LOG_RAW_API_PAYLOADS`, `LOG_RAW_SSE_EVENTS`, `LOG_API_ERROR_TRACEBACKS` | false | Verbose diagnostics. **Off by default** to avoid leaking request content into logs. |
| `WEB_FETCH_ALLOW_PRIVATE_NETWORKS` | false | If true, web-search/web-fetch tools may reach RFC1918 ranges. |

See `.env.example` for the full list with annotations.

## ⚖️ How Quench Compares

| | Free-tier helpers | Fallback chains | Anthropic-native SSE | Drop-in for Claude Code |
|---|---|---|---|---|
| **Quench** | Yes, with chain auto-fallback | Yes, per-slot, TTL-quenched, mid-stream-safe | Yes | Yes |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | BYO keys | Request-level routing, no per-slot chains | OpenAI-format-first, Anthropic via adapter | Indirect |
| [Alishahryar1/free-claude-code](https://github.com/Alishahryar1/free-claude-code) (upstream) | Yes | Single value per slot | Yes | Yes |
| [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router) | BYO keys | Intent-based routing, no quench TTL | Translates to OpenAI chat | Yes |
| [fuergaosi233/claude-code-proxy](https://github.com/fuergaosi233/claude-code-proxy) | BYO keys | No | Chat-only, no Anthropic SSE | Yes |

**Pick Quench when:** you're hitting free-tier rate limits and want a configure-once-then-forget setup that survives provider hiccups.

**Pick LiteLLM when:** you have many paid keys and want OpenAI-format unification across the entire stack, with cost tracking and gateway features.

**Pick claude-code-router when:** you want intent-based routing (different model per task category: reasoning vs. background vs. web search).

## 🏗 Architecture

```
+----------------------+        +-------------------+        +--------------------+
|  Claude Code / SDK   |        |   Quench proxy    |        |  Upstream provider |
|  (Anthropic format)  | ---->  |   :8082 loopback  | ---->  |  (NIM / OR / etc.) |
+----------------------+        +---------+---------+        +--------------------+
                                          |
                                          v
                                   +--------------+
                                   |   Quench     |
                                   |   registry   |  (TTL cooldowns)
                                   +--------------+
```

Quench inspects each `/v1/messages` request, resolves the model chain, walks chain entries skipping quenched ones, and forwards to the first healthy upstream. Errors during preflight or before the first content chunk trigger transparent failover. After the first chunk reaches the client, errors gracefully end the stream.

The chain pump (`api/services.py::ClaudeProxyService._chain_pump`) is the load-bearing function; the quench registry (`core/quench.py`) is a thread-safe TTL map. State is process-local and never persisted.

## 💬 Discord / Telegram Bots

Quench inherits the optional Discord and Telegram remote-coding bots from upstream. They let you drive a Claude CLI subprocess from chat, with tree-based threading and session persistence.

Set `MESSAGING_PLATFORM` in `.env` to `discord`, `telegram`, or `none`. See `.env.example` for the full set of `*_BOT_TOKEN` and channel-allowlist variables. The bot stack is opt-in; default is `discord` but disabled until a token is provided.

## 🛠 Development

Quench targets Python 3.14 and uses `uv` for everything.

```bash
# install dev deps
uv sync

# format + lint + type check + test (in this order)
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest -v
```

CI enforces all four checks on every push and PR (see `.github/workflows/tests.yml`). A second workflow (`fresh-clone.yml`) validates that a brand-new clone runs `setup.sh` cleanly and that `quench` parses without syntax errors.

Smoke tests live under `smoke/` and are opt-in via `QUENCH_LIVE_SMOKE=1`. They touch real providers, real bot APIs, or real local model servers.

## 🗺 Roadmap

- v0.2: VSCode extension with live failover view and per-request provider attribution.
- v0.2: Persistent quench registry (SQLite) so cooldowns survive restarts.
- v0.2: `quench-skills` companion repo with curated prompt-engineering and automation skills.
- Future: Routing telemetry to learn fastest healthy provider per task type.

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## 🤝 Contributing

Quench is MIT-licensed and accepts contributions of all sizes. New provider adapters, new fallback strategies, bug fixes, docs, examples — all welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev workflow and [SECURITY.md](SECURITY.md) for the threat model and disclosure policy.

Want to add a provider? Check `providers/` for the existing adapter pattern. The `BaseProvider` ABC is small and well-commented.

## 🙏 Acknowledgments

Quench is forked from [Alishahryar1/free-claude-code](https://github.com/Alishahryar1/free-claude-code), which built the original Anthropic-to-OpenAI translation layer, the Discord/Telegram bot stack, and the per-Claude-model routing primitives. Upstream development paused, so Quench picks up day-to-day fixes and the chain-fallback feature in this fork.

Comparison table differentiates Quench from [musistudio/claude-code-router](https://github.com/musistudio/claude-code-router) and [fuergaosi233/claude-code-proxy](https://github.com/fuergaosi233/claude-code-proxy), which solve adjacent problems with different trade-offs.

## 📄 License

MIT. Copyright (c) 2026 Ali Khokhar (original work) and Swayam Dash (Quench fork). See [LICENSE](LICENSE).
