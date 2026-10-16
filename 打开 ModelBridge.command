#!/bin/zsh
set -eu
if [[ -d "/Applications/ModelBridge.app" ]]; then
  open "/Applications/ModelBridge.app"
  exit 0
fi
if [[ -d "$HOME/Applications/ModelBridge.app" ]]; then
  open "$HOME/Applications/ModelBridge.app"
  exit 0
fi
bridge_agent="$HOME/Library/LaunchAgents/com.modelbridge.local.plist"
if [[ ! -f "$bridge_agent" && -f "$HOME/Library/LaunchAgents/com.hairfree.bridge.plist" ]]; then bridge_agent="$HOME/Library/LaunchAgents/com.hairfree.bridge.plist"; fi
if [[ ! -f "$bridge_agent" ]]; then
  print "尚未安装或已经停用。请先运行“安装 ModelBridge.command”。"
  read -r bridge_reply
  exit 1
fi
bridge_release="$(/usr/libexec/PlistBuddy -c 'Print :WorkingDirectory' "$bridge_agent")"
bridge_python="$bridge_release/.venv/bin/python"
cd -- "$bridge_release"
if ! "$bridge_python" installer.py open; then read -r bridge_reply; exit 1; fi
