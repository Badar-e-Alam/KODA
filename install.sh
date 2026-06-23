#!/usr/bin/env sh
# KODA one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Badar-e-Alam/KODA/main/install.sh | sh
#
# Clones (or updates) the KODA repo, installs it into an isolated venv,
# puts `koda` on your PATH, and helps you set OLLAMA_API_KEY so the
# `ollama:` / `kimi:` model specs work out of the box.
#
# Options (env vars):
#   KODA_REPO     git URL to clone           (default: https://github.com/Badar-e-Alam/KODA.git)
#   KODA_HOME     install parent dir        (default: $HOME/.koda)
#   KODA_EXTRAS   pip extra groups          (default: "ollama")
#   SKIP_KEY=1    do not prompt for the key (default: prompt if unset)

set -eu

# ── pretty printing ───────────────────────────────────────────────────
info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m  x\033[0m %s\n' "$*" >&2; exit 1; }

# ── config ─────────────────────────────────────────────────────────────
REPO="${KODA_REPO:-https://github.com/Badar-e-Alam/KODA.git}"
HOME_DIR="${KODA_HOME:-$HOME/.koda}"
CLONE_DIR="$HOME_DIR/repo"
VENV_DIR="$HOME_DIR/venv"
EXTRAS="${KODA_EXTRAS:-ollama}"
SKIP_KEY="${SKIP_KEY:-0}"

# ── preflight ──────────────────────────────────────────────────────────
info "KODA installer"

command -v git >/dev/null  || die "git not found. Install it first: https://git-scm.com/downloads"

# Pick a Python >=3.11
PY=""
for c in python3 python python3.11 python3.12 python3.13; do
    command -v "$c" >/dev/null 2>&1 || continue
    ver=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || continue
    major=${ver%%.*}; minor=${ver#*.}
    if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; }; then
        PY="$c"; break
    fi
done
[ -n "$PY" ] || die "Python 3.11+ not found. Install it: https://www.python.org/downloads/"
ok "using $($PY --version) ($PY)"

# ── clone / update ─────────────────────────────────────────────────────
mkdir -p "$HOME_DIR"
if [ -d "$CLONE_DIR/.git" ]; then
    info "updating existing clone at $CLONE_DIR"
    git -C "$CLONE_DIR" pull --ff-only
else
    info "cloning $REPO -> $CLONE_DIR"
    git clone --depth 1 "$REPO" "$CLONE_DIR"
fi

# ── venv + install ─────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    info "creating venv at $VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"
info "upgrading pip + installing koda[$EXTRAS]"
python -m pip install --upgrade pip >/dev/null
pip install -e "$CLONE_DIR[$EXTRAS]"

# ── put `koda` on PATH ─────────────────────────────────────────────────
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/koda" "$BIN_DIR/koda"
ok "linked koda -> $BIN_DIR/koda"

on_path=0
case ":$PATH:" in *":$BIN_DIR:"*) on_path=1;; esac

# ── Ollama key ─────────────────────────────────────────────────────────
# KODA reads OLLAMA_API_KEY for Ollama Cloud; OLLAMA_HOST for a local/custom
# daemon. We only touch the cloud key here — local Ollama needs no key.
key="${OLLAMA_API_KEY:-}"
if [ -z "$key" ] && [ -f "$HOME/.env-merged" ] 2>/dev/null; then :; fi  # placeholder

# Detect a running local Ollama — if present, the key is optional.
local_ollama=0
if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 2 http://localhost:11434 >/dev/null 2>&1; then
        local_ollama=1
        ok "local Ollama detected at http://localhost:11434 (key optional)"
    fi
fi

need_key=1
if [ -n "$key" ]; then
    ok "OLLAMA_API_KEY already set in this environment"
    need_key=0
elif [ "$local_ollama" = 1 ]; then
    need_key=0
fi

if [ "$need_key" = 1 ] && [ "$SKIP_KEY" = 0 ]; then
    printf '\033[1;34m==>\033[0m Ollama Cloud API key\n'
    printf '  Get one at https://ollama.com.\n'
    printf '  Paste it now (Enter to skip): '
    read -r typed
    if [ -n "$typed" ]; then
        key="$typed"
        # persist into the right shell rc
        rc=""
        case "$SHELL" in
            *zsh)  rc="$HOME/.zshrc" ;;
            *bash) rc="$HOME/.bashrc" ;;
            *)     rc="$HOME/.profile" ;;
        esac
        {
            printf '\n# KODA — Ollama Cloud\n'
            printf 'export OLLAMA_API_KEY=%s\n' "$key"
        } >> "$rc"
        ok "saved OLLAMA_API_KEY to $rc (start a new shell or: source $rc)"
    else
        warn "skipped — set it later with: export OLLAMA_API_KEY=<your-key>"
    fi
fi

# ── done ───────────────────────────────────────────────────────────────
printf '\n\033[1;32m✓ KODA installed.\033[0m\n\n'
if [ "$on_path" = 0 ]; then
    warn "$BIN_DIR is not on your PATH. Add it once:"
    rc=""
    case "$SHELL" in
        *zsh)  rc="$HOME/.zshrc" ;;
        *bash) rc="$HOME/.bashrc" ;;
        *)     rc="$HOME/.profile" ;;
    esac
    printf '  echo "export PATH=%s:$PATH" >> %s\n' "$BIN_DIR" "$rc"
    printf '  source %s\n\n' "$rc"
fi
printf 'Run it:\n  koda --agent coding_agent --model ollama:glm-5.1\n\n'
printf 'Update later:\n  git -C %s pull && %s/bin/python -m pip install -e %s[%s]\n' \
    "$CLONE_DIR" "$VENV_DIR" "$CLONE_DIR" "$EXTRAS"