#!/usr/bin/env bash
set -euo pipefail

DIR="${ONIONMIND_DIR:-/var/lib/qwen}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir) DIR="$2"; shift 2 ;;
    *) echo "usage: $0 [--install-dir DIR]" >&2; exit 2 ;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
DIR=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$DIR")
HOME_REAL=$(python3 -c 'from pathlib import Path; print(Path.home().resolve())')
case "$DIR" in
  /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/lib|"$HOME_REAL")
    echo "Refusing broad install directory: $DIR" >&2
    exit 1
    ;;
esac
mkdir -p "$DIR"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || {
    echo "sudo is required to update Onionmind Agent" >&2
    exit 1
  }
  SUDO=sudo
fi
NODE_MAJOR=$(node --version 2>/dev/null | sed -n 's/^v\([0-9][0-9]*\).*/\1/p')
if [ "${NODE_MAJOR:-0}" -lt 22 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    node_setup=$(mktemp) || { echo "Could not create a Node.js setup temp file." >&2; exit 1; }
    curl -fsSL https://deb.nodesource.com/setup_22.x -o "$node_setup"
    $SUDO -E bash "$node_setup"
    rm -f "$node_setup"
    $SUDO apt-get install -y nodejs
  elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -S --needed --noconfirm nodejs npm
  else
    echo "Onionmind Agent requires Node.js 22 or newer." >&2
    exit 1
  fi
  NODE_MAJOR=$(node --version 2>/dev/null | sed -n 's/^v\([0-9][0-9]*\).*/\1/p')
fi
[ "${NODE_MAJOR:-0}" -ge 22 ] || { echo "Onionmind Agent requires Node.js 22 or newer." >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required to update Onionmind Agent." >&2; exit 1; }
$SUDO npm install --global @qwen-code/qwen-code@0.22.0 --no-fund --no-audit
command -v qwen >/dev/null 2>&1 || { echo "Onionmind Agent runtime is unavailable after update." >&2; exit 1; }
# qwen --version is the readiness probe shared with the desktop Adapter.
QWEN_VERSION=$(qwen --version 2>/dev/null || true)
case "$QWEN_VERSION" in
  0.22.0*) ;;
  *) echo "Onionmind Agent runtime $QWEN_VERSION was installed; expected 0.22.0." >&2; exit 1 ;;
esac

model='inferno'
if [ -f "$DIR/onionmind.py" ]; then
  model=$(sed -n 's/^MODEL[[:space:]]*=[[:space:]]*"\([^"]*\)"/\1/p' "$DIR/onionmind.py" | head -1)
  model=${model:-inferno}
fi

STAGE=$(mktemp -d)
PREPARED=""
cleanup() {
  rm -rf -- "$STAGE"
  if [ -n "$PREPARED" ] && [ -d "$PREPARED" ]; then
    rm -rf -- "$PREPARED"
  fi
}
trap cleanup EXIT

revision=$(curl -fsSL 'https://api.github.com/repos/Codemaster64/onionmind/commits/main' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
[[ "$revision" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Could not resolve a fixed update revision." >&2; exit 1; }
base="https://raw.githubusercontent.com/Codemaster64/onionmind/$revision"
FILES=(
  onionmind.py
  onionmind_desktop_core.py
  onionmind_desktop.py
)
for name in "${FILES[@]}"; do
  curl -fsSL "$base/$name" -o "$STAGE/$name"
done

python3 - "$STAGE/onionmind.py" "$model" <<'PY'
from pathlib import Path
import json
import re
import sys

path = Path(sys.argv[1])
model = sys.argv[2]
text = path.read_text(encoding="utf-8")
text, count = re.subn(
    r'^MODEL\s*=.*$',
    lambda _match: f"MODEL  = {json.dumps(model, ensure_ascii=False)}",
    text,
    count=1,
    flags=re.MULTILINE,
)
if count != 1:
    raise SystemExit("MODEL assignment is missing from downloaded onionmind.py")
path.write_text(text, encoding="utf-8", newline="\n")
PY

python3 -m py_compile "$STAGE/onionmind.py"
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  python3 -m py_compile \
    "$STAGE/onionmind_desktop_core.py" \
    "$STAGE/onionmind_desktop.py"
else
  echo "Python 3.10+ is required for the native workbench; the classic UI will remain active." >&2
fi
chmod 755 "$STAGE/onionmind.py"
chmod 644 \
  "$STAGE/onionmind_desktop_core.py" \
  "$STAGE/onionmind_desktop.py"

# Prepare every file on the destination filesystem before replacing anything.
# If an unlikely rename fails mid-set, restore every path already replaced.
PREPARED=$(mktemp -d "$DIR/.onionmind-update.XXXXXX")
BACKUP="$STAGE/backup"
mkdir -p "$BACKUP"
for name in "${FILES[@]}"; do
  cp -p -- "$STAGE/$name" "$PREPARED/$name"
  if [ -f "$DIR/$name" ]; then
    cp -p -- "$DIR/$name" "$BACKUP/$name"
  fi
done

replaced=()
rollback() {
  local name
  for name in "${replaced[@]}"; do
    if [ -f "$BACKUP/$name" ]; then
      cp -p -- "$BACKUP/$name" "$DIR/$name"
    else
      rm -f -- "$DIR/$name"
    fi
  done
}
for name in "${FILES[@]}"; do
  if ! mv -f -- "$PREPARED/$name" "$DIR/$name"; then
    rollback
    echo "Update failed while replacing $name; previous files were restored." >&2
    exit 1
  fi
  replaced+=("$name")
done

# Existing installs may still have a chat-sized context. Re-register the same
# manifests with enough room for the Agent prompt and file tools. Model blobs
# are reused; this changes configuration without downloading weights.
AGENT_CONTEXT='PARAMETER num_ctx 32768'
set_agent_context() {
  local definition="$1" model_name="$2" temporary
  [ -f "$definition" ] || return 0
  temporary=$(mktemp "$DIR/.onionmind-modelfile.XXXXXX") || return 1
  awk -v replacement="$AGENT_CONTEXT" '
    BEGIN { replaced = 0 }
    /^PARAMETER[[:space:]]+num_ctx[[:space:]]+[0-9]+[[:space:]]*$/ && !replaced {
      print replacement
      replaced = 1
      next
    }
    { print }
    END { if (!replaced) print replacement }
  ' "$definition" > "$temporary"
  chmod --reference="$definition" "$temporary" 2>/dev/null || chmod 644 "$temporary"
  mv -f -- "$temporary" "$definition"
  if command -v ollama >/dev/null 2>&1; then
    ollama create "$model_name" -f "$definition"
  fi
}
set_agent_context "$DIR/Modelfile" "$model"
model_base=${model%%:*}
set_agent_context "$DIR/Modelfile.vision" "$model_base-vision"

if python3 -m venv "$DIR/desktop-env"; then
  desktop_python="$DIR/desktop-env/bin/python"
  desktop_marker="$DIR/desktop-env/.onionmind-desktop-ready"
  rm -f -- "$desktop_marker"
  if ! "$desktop_python" -m pip install --quiet --disable-pip-version-check \
      requests PySocks PySide6-Essentials==6.11.1; then
    echo "Native desktop dependency download failed; checking the existing environment." >&2
  fi
  if "$desktop_python" -c 'import requests, socks, PySide6.QtWidgets'; then
    : > "$desktop_marker"
  else
    echo "Native desktop dependencies could not be imported; the classic UI remains available." >&2
  fi
else
  echo "Could not create the native desktop environment; install python3-venv and rerun." >&2
fi

if [ -w /usr/local/bin ] || [ -n "$SUDO" ]; then
  DIR_LITERAL=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$DIR")
  MODEL_LITERAL=$(python3 -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$model")

  $SUDO tee /usr/local/bin/onionmind >/dev/null <<DESKTOP
#!/bin/sh
DIR=$DIR_LITERAL
systemctl is-active --quiet tor 2>/dev/null || sudo -n systemctl start tor 2>/dev/null \
  || echo "[tor] not running - chat search will refuse until: sudo systemctl start tor"
cd "\$HOME"
PYTHON="\$DIR/desktop-env/bin/python"
[ -x "\$PYTHON" ] && [ -f "\$DIR/desktop-env/.onionmind-desktop-ready" ] || PYTHON=python3
if [ "\$#" -eq 0 ]; then
  exec "\$PYTHON" "\$DIR/onionmind.py" --ui
else
  exec "\$PYTHON" "\$DIR/onionmind.py" "\$@"
fi
DESKTOP
  $SUDO chmod 755 /usr/local/bin/onionmind

  $SUDO tee /usr/local/bin/onionmind-code >/dev/null <<AGENT
#!/bin/sh
DIR=$DIR_LITERAL
MODEL=$MODEL_LITERAL
export ONIONMIND_PY="\$DIR/onionmind.py"
export ONIONMIND_PYTHON=python3
if [ "\$#" -eq 0 ]; then
  echo 'usage: onionmind-code "task for the coding agent"' >&2
  exit 2
fi
NODE_VERSION=\$(node --version 2>/dev/null) || {
  echo 'Onionmind Agent requires Node.js 22 or newer. Re-run Onionmind Update.' >&2
  exit 1
}
NODE_VERSION=\${NODE_VERSION#v}
NODE_MAJOR=\${NODE_VERSION%%.*}
case "\$NODE_MAJOR" in
  *[!0-9]*|'') echo "Could not read the installed Node.js version: \$NODE_VERSION" >&2; exit 1 ;;
esac
if [ "\$NODE_MAJOR" -lt 22 ]; then
  echo "Onionmind Agent requires Node.js 22 or newer; found \$NODE_VERSION." >&2
  exit 1
fi
command -v qwen >/dev/null 2>&1 || {
  echo 'Onionmind Agent runtime is missing. Re-run Onionmind Update.' >&2
  exit 1
}
# qwen --version confirms the pinned Adapter before every launch.
QWEN_VERSION=\$(qwen --version 2>/dev/null || true)
case "\$QWEN_VERSION" in
  0.22.0*) ;;
  *) echo "Onionmind Agent runtime is out of date (\$QWEN_VERSION). Re-run Onionmind Update." >&2; exit 1 ;;
esac
STATE_ROOT="\$DIR/agent"
RUNTIME_ROOT="\$STATE_ROOT/runtime"
mkdir -p "\$RUNTIME_ROOT"
cat > "\$STATE_ROOT/settings.json" <<'QWENSETTINGS'
{
  "model": {
    "generationConfig": {
      "contextWindowSize": 32768,
      "samplingParams": { "max_tokens": 2048 },
      "reasoning": false,
      "extra_body": { "reasoning_effort": "none" }
    }
  }
}
QWENSETTINGS
export QWEN_HOME="\$STATE_ROOT"
export QWEN_RUNTIME_DIR="\$RUNTIME_ROOT"
export QWEN_USAGE_STATISTICS_ENABLED=false
export QWEN_CODE_SKIP_UPDATE_CHECK_ONCE=1
export OPENAI_API_KEY=onionmind-local
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1
export OPENAI_MODEL="\$MODEL"
export NO_PROXY=127.0.0.1,::1
export no_proxy=127.0.0.1,::1
unset ALL_PROXY HTTPS_PROXY HTTP_PROXY all_proxy https_proxy http_proxy
EXCLUDED_TOOLS='run_shell_command,web_fetch,web_search,image_gen,save_memory,agent,skill,ask_user_question,cron_create,cron_list,cron_delete,loop_wakeup,create_sub_session,list_agents,task_create,task_update,task_stop,team_create,team_delete,send_message,monitor,tool_search,read_mcp_resource,enter_worktree,exit_worktree,workflow,computer_use__bring_to_front,computer_use__check_for_update,computer_use__check_permissions,computer_use__click,computer_use__double_click,computer_use__drag,computer_use__end_session,computer_use__get_accessibility_tree,computer_use__get_agent_cursor_state,computer_use__get_config,computer_use__get_cursor_position,computer_use__get_recording_state,computer_use__get_screen_size,computer_use__get_window_state,computer_use__hotkey,computer_use__kill_app,computer_use__launch_app,computer_use__list_apps,computer_use__list_windows,computer_use__move_cursor,computer_use__page,computer_use__press_key,computer_use__replay_trajectory,computer_use__right_click,computer_use__scroll,computer_use__set_agent_cursor_enabled,computer_use__set_agent_cursor_motion,computer_use__set_agent_cursor_style,computer_use__set_config,computer_use__set_value,computer_use__start_recording,computer_use__start_session,computer_use__stop_recording,computer_use__type_text,computer_use__zoom,get_goal,notebook_edit,record_artifact,todo_write,update_goal,zoom_image'
SYSTEM_PROMPT='You are Onionmind Agent, a local coding agent. Work only in the current project. Complete the user task by inspecting and editing files with the available file tools. Never use shell, web, network, cloud, persistence, or subagents. Make minimal accurate changes. Do not merely describe an edit: call a file-edit tool. Stop when the task is complete.'
echo 'Onionmind Agent can edit files in this project; shell and web tools are disabled.' >&2
exec qwen --prompt "\$*" --system-prompt "\$SYSTEM_PROMPT" --output-format text \
  --approval-mode auto-edit --auth-type openai --model "\$MODEL" \
  --openai-api-key onionmind-local --openai-base-url http://127.0.0.1:11434/v1 \
  --telemetry=false --chat-recording=false --safe-mode \
  --exclude-tools "\$EXCLUDED_TOOLS" --max-wall-time 30m \
  --max-tool-calls 200 --channel desktop
AGENT
  $SUDO chmod 755 /usr/local/bin/onionmind-code

  $SUDO tee /usr/local/bin/onionmind-update >/dev/null <<UPDATE
#!/bin/sh
DIR=$DIR_LITERAL
tmp=\$(mktemp) || exit 1
trap 'rm -f "\$tmp"' EXIT
curl -fsSL https://raw.githubusercontent.com/Codemaster64/onionmind/main/update-onionmind.sh -o "\$tmp" || exit 1
bash "\$tmp" --install-dir "\$DIR"
status=\$?
rm -f "\$tmp"
trap - EXIT
exit "\$status"
UPDATE
  $SUDO chmod 755 /usr/local/bin/onionmind-update
fi

echo "Updated Onionmind native workbench and coding agent ($model). Model weights were not changed."
