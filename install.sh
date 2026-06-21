#!/usr/bin/env bash
# TopDock installer — Linux & macOS

set -e

BOLD="\033[1m"
CYAN="\033[36m"
MAGENTA="\033[35m"
GREEN="\033[32m"
RED="\033[31m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${MAGENTA}${BOLD}"
echo "  ⚡ TopDock Installer"
echo -e "${RESET}"

# ── check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ Python 3 not found. Install it first: https://python.org${RESET}"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "${RED}✗ Python 3.10+ required (found $PY_VER)${RESET}"
    exit 1
fi
echo -e "${GREEN}✔ Python $PY_VER found${RESET}"

# ── check Docker ──────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo -e "${RED}✗ Docker not found. Install from: https://docs.docker.com/get-docker/${RESET}"
    exit 1
fi
echo -e "${GREEN}✔ Docker found${RESET}"

# ── install ───────────────────────────────────────────────────────────────────
if command -v pipx &>/dev/null; then
    echo -e "${CYAN}→ Installing via pipx…${RESET}"
    pipx install "$SCRIPT_DIR" --force
    echo -e "${GREEN}${BOLD}✔ Done! Run: topdock${RESET}"
    exit 0
fi

echo -e "${CYAN}→ pipx not found. Installing pipx first…${RESET}"
if python3 -m pip install --user pipx &>/dev/null; then
    python3 -m pipx ensurepath &>/dev/null || true
    export PATH="$PATH:$HOME/.local/bin"
    if command -v pipx &>/dev/null; then
        echo -e "${GREEN}✔ pipx installed${RESET}"
        pipx install "$SCRIPT_DIR" --force
        echo -e "${GREEN}${BOLD}✔ Done! Run: topdock${RESET}"
        echo -e "${CYAN}  Restart your shell if the command isn't found.${RESET}"
        exit 0
    fi
fi

# ── last resort: pip ──────────────────────────────────────────────────────────
echo -e "${CYAN}→ Falling back to pip…${RESET}"
python3 -m pip install --user "$SCRIPT_DIR"
echo -e "${GREEN}${BOLD}✔ Done! Run: topdock${RESET}"
echo -e "${CYAN}  Make sure ~/.local/bin is in your PATH.${RESET}"
