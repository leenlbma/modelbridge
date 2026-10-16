#!/bin/zsh
set -eu
cd -- "${0:A:h}"
trap 'print "安装未完成。请保留上方错误信息，按回车关闭。"; read -r bridge_reply' ZERR
if [[ "$(uname -s)" != "Darwin" ]]; then
  print "此测试包仅支持 macOS。"
  exit 1
fi
print "ModelBridge · 安装到当前用户，不需要管理员权限"
print "首次安装需要网络，将从官方渠道准备 uv、Python 与锁定版本的依赖。"
bridge_uv="$(command -v uv || true)"
if [[ -z "$bridge_uv" && -x "$HOME/.local/bin/uv" ]]; then bridge_uv="$HOME/.local/bin/uv"; fi
if [[ -z "$bridge_uv" ]]; then
  bridge_tools="$HOME/Library/Application Support/ModelBridge/tools"
  if [[ ! -x "$bridge_tools/uv" ]]; then
    bridge_download="$(mktemp -d)"
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$bridge_download/install-uv.sh"
    UV_INSTALL_DIR="$bridge_tools" UV_NO_MODIFY_PATH=1 sh "$bridge_download/install-uv.sh"
    rm -f -- "$bridge_download/install-uv.sh"
    rmdir -- "$bridge_download"
  fi
  bridge_uv="$bridge_tools/uv"
fi
"$bridge_uv" run --no-project --python 3.12 installer.py install --uv "$bridge_uv"
print "控制台已打开。之后可双击“打开 ModelBridge.command”。"
