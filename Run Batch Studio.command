#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

alert() {
  printf '%s\n' "$1" >&2
  osascript -e "display alert \"Batch Image Studio\" message \"$1\"" >/dev/null 2>&1 || true
}

py=""
candidates=(
  python3.13 python3.12 python3.11 python3.10 python3
  /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10
  /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10
  /opt/homebrew/opt/python@3.13/bin/python3.13
  /opt/homebrew/opt/python@3.12/bin/python3.12
  /opt/homebrew/opt/python@3.11/bin/python3.11
)
for candidate in "${candidates[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
      py="$candidate"
      break
    fi
  fi
done

if [[ -z "$py" ]]; then
  alert "Python 3.10 or newer is required. Install it from python.org, then run this again."
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  "$py" -m venv .venv
fi

if ! .venv/bin/python -c 'import PySide6, openai, PIL, numpy, dotenv' >/dev/null 2>&1; then
  if ! .venv/bin/python -m pip install --upgrade pip; then
    alert "Could not install the program. Check the network connection and run this again."
    exit 1
  fi
  if ! .venv/bin/python -m pip install -r requirements.txt; then
    alert "Could not install the program. Check the network connection and run this again."
    exit 1
  fi
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi

log_dir="$HOME/Library/Logs"
mkdir -p "$log_dir"
nohup .venv/bin/python -m studio >>"$log_dir/Batch Image Studio.log" 2>&1 &
disown || true
