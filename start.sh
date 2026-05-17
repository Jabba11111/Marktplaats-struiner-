#!/usr/bin/env bash
# Treasure Scanner — Linux/macOS one-click installer / launcher.
# First run: creates .venv, installs deps, downloads Chromium, copies .env.
# Subsequent runs: just start the scanner.

set -e
cd "$(dirname "$0")"

echo
echo "============================================"
echo "  Treasure Scanner — setup & start"
echo "============================================"
echo

# 1. Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 not found. Install Python 3.11+ first."
    exit 1
fi
PY_VERSION=$(python3 --version | awk '{print $2}')
echo "Python:           $PY_VERSION"

# Require >= 3.11
PY_MAJ=$(python3 -c 'import sys; print(sys.version_info[0])')
PY_MIN=$(python3 -c 'import sys; print(sys.version_info[1])')
if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 11 ]; }; then
    echo "[ERROR] Python 3.11+ required, found $PY_VERSION."
    exit 1
fi

# 2. Venv
if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtualenv in .venv ..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Virtualenv:       $PWD/.venv"

# 3. Deps
echo
echo "Installing/updating dependencies (first run takes 2-5 minutes) ..."
python -m pip install --quiet --disable-pip-version-check --upgrade pip
python -m pip install --quiet --disable-pip-version-check -e .

# 4. Patchright Chromium
echo "Installing Chromium for stealth browser (skip if already present) ..."
if ! python -m patchright install chromium; then
    echo "[WARN] Chromium install failed. Catawiki + stealth mode will be skipped."
    echo "       Retry later with:  python -m patchright install chromium"
fi

# 5. .env
if [ ! -f .env ]; then
    echo
    echo ".env not found — copying .env.example to .env"
    cp .env.example .env
    echo
    echo "============================================"
    echo "  Edit .env now and set:"
    echo "    TELEGRAM_BOT_TOKEN  (chat @BotFather)"
    echo "    TELEGRAM_CHAT_ID    (chat @userinfobot)"
    echo "============================================"
    echo "Then re-run ./start.sh"
    "${EDITOR:-nano}" .env
    exit 0
fi

if ! grep -E '^TELEGRAM_BOT_TOKEN=.+' .env >/dev/null; then
    echo "[ERROR] TELEGRAM_BOT_TOKEN is empty in .env. Fill it in and re-run."
    exit 1
fi

mkdir -p data

echo
echo "============================================"
echo "  Starting ..."
echo "  Dashboard: http://localhost:8765"
echo "  Stop:      Ctrl+C"
echo "============================================"
echo

exec treasure-scanner
