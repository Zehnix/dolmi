#!/bin/zsh
# Dolmi - one-click start from source (double-click in Finder). First run installs everything (several minutes).
cd "${0:A:h}"
echo "[1/3] Checking Python and Xcode tools..."
PY=$(command -v python3.12 || command -v python3)
[[ -n "$PY" ]] || { echo "Python not found. Install it: brew install python@3.12"; read; exit 1; }
xcode-select -p >/dev/null 2>&1 || { echo "Xcode Command Line Tools missing. Run: xcode-select --install"; read; exit 1; }

if [[ ! -x venv/bin/python ]]; then
  echo "[2/3] Creating virtual environment..."
  "$PY" -m venv venv || { echo "Could not create venv"; read; exit 1; }
fi

if [[ ! -f venv/.installed-mac-v1 ]]; then
  echo "[2/3] Installing packages - first run only, this can take a few minutes."
  venv/bin/python -m pip install --upgrade pip
  venv/bin/python -m pip install --progress-bar on -r requirements.txt || {
    echo; echo "Install FAILED. Copy the error above and send it to Claude."; read; exit 1; }
  touch venv/.installed-mac-v1
fi

if [[ ! -x audio/dolmi-audio || audio/dolmi-audio.swift -nt audio/dolmi-audio ]]; then
  echo "[2/3] Building the system audio helper..."
  swiftc -O audio/dolmi-audio.swift -o audio/dolmi-audio || { echo "Could not build the audio helper"; read; exit 1; }
fi

echo "[3/3] Starting Dolmi..."
venv/bin/python ../src/main.py &!   # the app itself is the shared code at the repo root
