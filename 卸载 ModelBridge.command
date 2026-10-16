#!/bin/zsh
set -eu
bridge_agent="$HOME/Library/LaunchAgents/com.modelbridge.local.plist"
if [[ ! -f "$bridge_agent" && -f "$HOME/Library/LaunchAgents/com.hairfree.bridge.plist" ]]; then bridge_agent="$HOME/Library/LaunchAgents/com.hairfree.bridge.plist"; fi
if [[ ! -f "$bridge_agent" ]]; then print "服务未安装或已经停用。"; exit 0; fi
print "请先在 Claude 切换到其他可用配置。此操作会停止 Bridge，保留配置供恢复。"
read -r "bridge_reply?输入 y 停用，其他输入取消："
if [[ "$bridge_reply" != "y" ]]; then exit 0; fi
bridge_python="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$bridge_agent")"
bridge_release="$(/usr/libexec/PlistBuddy -c 'Print :WorkingDirectory' "$bridge_agent")"
cd -- "$bridge_release"
"$bridge_python" installer.py uninstall
