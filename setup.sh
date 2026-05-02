#!/usr/bin/env bash
# setup.sh - first-time setup for Quench (LLM router for Anthropic-protocol clients)
#
# Idempotent. Safe to re-run. Does:
#   1. Verifies bash + checks required tools (curl, jq)
#   2. Installs uv (Astral) if missing
#   3. Pins Python 3.14 via uv if missing
#   4. Runs `uv sync` to install Python deps
#   5. Copies .env.example to .env if .env doesn't exist
#   6. Optionally walks you through filling in API keys
#   7. Makes quench executable
#
# After this finishes, run `./quench start` to launch the proxy.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
warn()  { printf '\033[33m  %s\033[0m\n' "$*"; }
ok()    { printf '\033[32m  %s\033[0m\n' "$*"; }
err()   { printf '\033[31m  %s\033[0m\n' "$*" >&2; }

need_tool() {
  command -v "$1" >/dev/null 2>&1 || {
    err "Required tool '$1' is not installed."
    case "$1" in
      jq)   info "Install: 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu)." ;;
      curl) info "Install via your system package manager." ;;
    esac
    exit 1
  }
}

bold "Quench setup"
echo

# Step 1: required tools
need_tool curl
need_tool jq
ok "Required tools present (curl, jq)."

# Step 2: install uv if missing
if ! command -v uv >/dev/null 2>&1; then
  warn "uv (Astral) not found. Installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installer writes to ~/.local/bin; make sure it's on PATH for this shell
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  err "uv install did not put uv on PATH. Add ~/.local/bin to your PATH and re-run."
  exit 1
fi
ok "uv $(uv --version)"

# Step 3: ensure Python 3.14
if ! uv python find 3.14 >/dev/null 2>&1; then
  warn "Python 3.14 not found via uv. Installing..."
  uv python install 3.14
fi
ok "Python 3.14 available."

# Step 4: install dependencies
bold "Installing Python dependencies..."
uv sync
ok "Dependencies installed."

# Step 5: prepare .env
if [[ -f .env ]]; then
  ok ".env already exists, leaving it alone."
else
  cp .env.example .env
  ok "Created .env from .env.example."
fi

# Step 6: interactive key prompt (optional)
if [[ "${SKIP_KEY_PROMPT:-0}" == "1" ]]; then
  warn "SKIP_KEY_PROMPT=1, skipping interactive setup."
else
  echo
  bold "Set up at least one provider key (you can edit .env later)"
  info "Press Enter to skip any provider you don't want to use right now."
  echo

  prompt_key() {
    local var="$1"
    local hint="$2"
    local current
    current=$(grep -E "^${var}=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
    if [[ -n "$current" && "$current" != "" ]]; then
      info "$var already set (length ${#current}). Skipping."
      return
    fi
    printf "  %s [%s]: " "$var" "$hint"
    read -r value
    if [[ -n "$value" ]]; then
      # Replace the empty value in .env, preserving the rest of the line.
      # Sed -i differs between BSD (macOS) and GNU; use a temp file for portability.
      awk -v var="$var" -v val="$value" 'BEGIN{FS=OFS="="} $1==var {print var "=\"" val "\""; next} {print}' .env > .env.tmp && mv .env.tmp .env
      ok "$var saved."
    fi
  }

  prompt_key NVIDIA_NIM_API_KEY    "https://build.nvidia.com/settings/api-keys (40 req/min free)"
  prompt_key OPENROUTER_API_KEY    "https://openrouter.ai/keys (free models available)"
  prompt_key DEEPSEEK_API_KEY      "https://platform.deepseek.com/api_keys (paid, optional)"
fi

# Step 7: make quench runnable
chmod +x quench 2>/dev/null || true

echo
bold "Setup complete."
echo
info "Next steps:"
info "  ./quench start      # start the proxy and route Claude Code through it"
info "  ./quench status     # check proxy state"
info "  ./quench stop       # back to Anthropic subscription"
info "  ./quench logs       # tail the proxy log"
echo
info "Default chains in .env route through NVIDIA NIM with OpenRouter free-tier"
info "as a fallback. See README.md for the full chain syntax."
