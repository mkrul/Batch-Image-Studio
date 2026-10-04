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
support="$HOME/Library/Application Support/BatchImageStudio"
mkdir -p "$support"
shopt -s nullglob
for old in "$support"/gui-*.pid; do
  oldpid=$(tr -cd '0-9' <"$old" || true)
  if [[ -z "$oldpid" ]] || ! kill -0 "$oldpid" 2>/dev/null; then
    rm -f "$old"
  fi
done

close_launcher_window() {
  local mytty window_id
  mytty=$(tty 2>/dev/null || true)
  if [[ -z "$mytty" || "$mytty" == "not a tty" ]]; then
    return
  fi
  window_id=$(osascript <<EOF
tell application "Terminal"
  repeat with w in windows
    try
      if (tty of selected tab of w as text) is "$mytty" then
        return id of w as text
      end if
    end try
  end repeat
  return ""
end tell
EOF
)
  window_id=$(printf '%s' "$window_id" | tr -cd '0-9')
  if [[ -z "$window_id" ]]; then
    return
  fi
  nohup osascript -e "delay 0.6" -e "tell application \"Terminal\" to close (first window whose id is $window_id)" >/dev/null 2>&1 &
  disown || true
}

token=$(uuidgen | tr -d '-' | cut -c1-12)
pidfile="$support/gui-$token.pid"
BATCH_STUDIO_PIDFILE="$pidfile" nohup .venv/bin/python -m studio >>"$log_dir/Batch Image Studio.log" 2>&1 &
disown || true

for _ in $(seq 1 60); do
  if [[ -s "$pidfile" ]]; then
    break
  fi
  sleep 0.5
done

if [[ ! -s "$pidfile" ]]; then
  printf '%s\n' "Batch Image Studio did not stay open. The log is $log_dir/Batch Image Studio.log"
  exit 1
fi

gui_pid=$(tr -cd '0-9' <"$pidfile")
printf '%s\n' "Batch Image Studio is running. This window closes when you quit the program."
while [[ -n "$gui_pid" ]] && kill -0 "$gui_pid" 2>/dev/null; do
  sleep 1
done
rm -f "$pidfile"
close_launcher_window
exit 0
