"""User-level macOS installer. Uses immutable releases and retains rollback data."""
import argparse
import fcntl
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

from bridge import __version__
from bridge.config import Store, atomic_json

LABEL = "com.modelbridge.local"
LEGACY_LABEL = "com.hairfree.bridge"
SOURCE = Path(__file__).resolve().parent
SERVICE_NAME = "ModelBridge 模型连接服务"
SERVICE_BUNDLE_ID = "com.modelbridge.background"
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
OVERRIDE_ROOT = None
OVERRIDE_LABEL = None


def root_dir():
    if OVERRIDE_ROOT is not None:
        return OVERRIDE_ROOT
    parent = Path.home() / "Library/Application Support"
    current, legacy = parent / "ModelBridge", parent / "HairFree Bridge"
    return legacy if not (current / "installation.json").exists() and (legacy / "installation.json").exists() else current


def service_label():
    if OVERRIDE_LABEL is not None:
        return OVERRIDE_LABEL
    return LEGACY_LABEL if root_dir().name == "HairFree Bridge" else LABEL


def launch_agent():
    return Path.home() / "Library/LaunchAgents" / (service_label() + ".plist")


def health(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def free_port():
    for port in range(18766, 18800):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise RuntimeError("没有可用的本机端口（18766–18799），请关闭冲突程序后重试。")


def service_arguments(release, data, port, install_id):
    return [str(release / ".venv/bin/python"), "-m", "bridge.server", "--data-dir", str(data), "--port", str(port), "--install-id", install_id]


def service_app(release):
    return release / (SERVICE_NAME + ".app")


def create_service_app(release, data, port, install_id, destination=None):
    """Give launchd a product identity instead of a bare Python executable.

    The shell exec preserves launchd's PID/signal handling. Opening the bundle
    in Finder opens the console; only launchd's --background flag starts a server.
    Each release owns its launcher so rollback cannot use a newer runtime.
    """
    app = destination or service_app(release)
    contents = app / "Contents"
    executable = contents / "MacOS" / SERVICE_NAME
    executable.parent.mkdir(parents=True, exist_ok=True)
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    shutil.copy2(SOURCE / "macos/ModelBridge.icns", resources / "ModelBridge.icns")
    info = {"CFBundleIdentifier": SERVICE_BUNDLE_ID, "CFBundleName": SERVICE_NAME,
            "CFBundleDisplayName": SERVICE_NAME, "CFBundleExecutable": SERVICE_NAME,
            "CFBundlePackageType": "APPL", "CFBundleVersion": __version__,
            "CFBundleShortVersionString": __version__, "CFBundleIconFile": "ModelBridge.icns",
            "LSUIElement": True, "LSMinimumSystemVersion": "14.0"}
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    background = shlex.join(service_arguments(release, data, port, install_id))
    dashboard = shlex.join([str(release / ".venv/bin/python"), str(release / "installer.py"), "open"])
    executable.write_text("#!/bin/sh\nset -eu\ncd " + shlex.quote(str(release)) +
                          '\nif [ "${1-}" = "--background" ]; then\n  exec ' + background +
                          "\nfi\nexec " + dashboard + "\n")
    executable.chmod(0o755)
    subprocess.run([LSREGISTER, "-f", str(app)], check=True, capture_output=True, timeout=15)
    return app


def plist_value(release, data, port, install_id, app=None):
    app = app or service_app(release)
    return {"Label": service_label(), "AssociatedBundleIdentifiers": [SERVICE_BUNDLE_ID],
            "ProgramArguments": [str(app / "Contents/MacOS" / SERVICE_NAME), "--background"],
            "WorkingDirectory": str(release), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 10, "ProcessType": "Background",
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
            "StandardOutPath": str(data / "service.log"), "StandardErrorPath": str(data / "service.log")}


def replace_bytes(path, content):
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    os.replace(temporary, path)


def stop_service(config=None):
    target = "gui/" + str(os.getuid()) + "/" + service_label()
    subprocess.run(["launchctl", "bootout", target], capture_output=True, timeout=10)
    # bootout can return before the job finishes exiting. Do not bootstrap the
    # replacement or remove its plist until launchd confirms the old job is gone.
    for _ in range(50):
        result = subprocess.run(["launchctl", "print", target], capture_output=True, timeout=5)
        if result.returncode == 113:
            current = health(config["port"]) if config else None
            if not current or current.get("install_id") != config["install_id"]:
                return
        elif result.returncode != 0:
            raise RuntimeError("无法确认后台服务是否停止，已保留启动配置。请稍后重试。")
        time.sleep(0.1)
    raise RuntimeError("后台服务仍在运行，已保留启动配置。请稍后重试。")


def install(uv, open_console=True):
    if sys.platform != "darwin":
        raise RuntimeError("这个安装包仅支持 macOS。")
    root = root_dir()
    manifest = root / "installation.json"
    agent = launch_agent()
    previous = json.loads(manifest.read_text()) if manifest.exists() else None
    if agent.exists() and not previous:
        raise RuntimeError("检测到同名但未由本安装器记录的服务。为避免覆盖，请先检查 LaunchAgents。")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    data = root / "data"
    Store(data)
    port = previous["port"] if previous else free_port()
    existing = health(port)
    if existing and (not previous or existing.get("install_id") != previous["install_id"]):
        raise RuntimeError("上次使用的端口已由其他服务占用，未修改现有安装。")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    release = root / "releases" / (__version__ + "-" + stamp)
    release.mkdir(parents=True)
    for folder in ("bridge", "web", "docs", "macos"):
        shutil.copytree(SOURCE / folder, release / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for filename in ("requirements.lock", "README.md", "installer.py"):
        shutil.copy2(SOURCE / filename, release / filename)
    for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if (SOURCE / filename).exists():
            shutil.copy2(SOURCE / filename, release / filename)
    print("[1/3] 正在准备独立运行环境…", flush=True)
    subprocess.run([uv, "venv", "--python", sys.executable, str(release / ".venv")], check=True)
    dependencies = [uv, "pip", "sync", "--python", str(release / ".venv/bin/python"), str(release / "requirements.lock")]
    try:
        subprocess.run(dependencies + ["--offline"], check=True, capture_output=True)
        print("[dependencies-cached] 已复用本机依赖缓存。", flush=True)
    except subprocess.CalledProcessError:
        print("[dependencies-download] 缓存不完整，正在下载缺少的服务组件…", flush=True)
        subprocess.run(dependencies, check=True)
    create_service_app(release, data, port, stamp)
    print("[2/3] 正在启动本机后台服务…", flush=True)
    domain = "gui/" + str(os.getuid())
    old_plist = agent.read_bytes() if agent.exists() else None
    if previous:
        atomic_json(root / "previous-installation.json", previous)
        if (data / "profile.json").exists():
            replace_bytes(root / "previous-profile.json", (data / "profile.json").read_bytes())
    if old_plist:
        replace_bytes(root / "previous-launchagent.plist", old_plist)
    log = data / "service.log"
    log.touch(mode=0o600, exist_ok=True)
    log.chmod(0o600)
    agent.parent.mkdir(parents=True, exist_ok=True)
    current = {"version": __version__, "port": port, "install_id": stamp, "release": str(release), "installed_at": datetime.now().astimezone().isoformat()}
    # Check before entering rollback: a refused upgrade must never stop the old job.
    if previous:
        assert_idle(previous, data)
    try:
        if old_plist:
            stop_service(previous)
        replace_bytes(agent, plistlib.dumps(plist_value(release, data, port, stamp)))
        subprocess.run(["launchctl", "bootstrap", domain, str(agent)], check=True)
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            value = health(port)
            if value and value.get("install_id") == stamp:
                break
            time.sleep(1)
        else:
            raise RuntimeError("后台服务未在 55 秒内启动。已尝试恢复上一次服务，请检查 data/service.log。")
        atomic_json(manifest, current)
    except BaseException:
        stop_service(current)
        if old_plist:
            replace_bytes(agent, old_plist)
            subprocess.run(["launchctl", "bootstrap", domain, str(agent)], capture_output=True)
        elif agent.exists():
            agent.unlink()
        raise
    print("[3/3] 安装完成。后台服务会在登录后自动启动。", flush=True)
    if open_console:
        open_dashboard()


def assert_idle(config, data):
    current = health(config["port"])
    if not current:
        return
    if current.get("install_id") != config["install_id"]:
        raise RuntimeError("端口已被其他服务占用，未修改现有安装。")
    token = json.loads((data / "admin.json").read_text())["token"]
    request = urllib.request.Request(f"http://127.0.0.1:{config['port']}/api/status", headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        value = json.load(response)
    if value.get("requests", {}).get("active", 0) or value.get("verifying", False):
        raise RuntimeError("仍有模型请求或验证正在进行，请完成后再重试。")


def start_existing():
    """Restore an installed release without creating a venv or downloading files."""
    root = root_dir()
    config = json.loads((root / "installation.json").read_text())
    release = Path(config["release"]).resolve()
    if root.resolve() not in release.parents:
        raise RuntimeError("安装目录不正确，未启动服务。")
    current = health(config["port"])
    if current:
        if current.get("install_id") != config["install_id"]:
            raise RuntimeError("端口已由其他服务占用，未修改现有安装。")
        return
    # Detect missing interpreters/dependencies before registering the job.
    subprocess.run([str(release / ".venv/bin/python"), "-I", "-c",
                    "import fastapi,uvicorn,httpx,importlib.util; assert importlib.util.find_spec('litellm')"],
                   check=True, capture_output=True, timeout=15)
    agent = launch_agent()
    saved = agent if agent.exists() else root / "disabled-launchagent.plist"
    original = saved.read_bytes()
    value = plistlib.loads(original)
    expected = service_app(release) / "Contents/MacOS" / SERVICE_NAME
    arguments = value.get("ProgramArguments", [])
    matching_arguments = isinstance(arguments, list) and len(arguments) == 2 and isinstance(arguments[0], str) and Path(arguments[0]).is_absolute() and Path(arguments[0]).resolve() == expected.resolve() and arguments[1] == "--background"
    working = value.get("WorkingDirectory", "")
    if value.get("Label") != service_label() or not isinstance(working, str) or Path(working).resolve() != release or not matching_arguments:
        raise RuntimeError("原启动项与安装记录不一致，未覆盖。请修复后台服务。")
    domain = "gui/" + str(os.getuid())
    target = domain + "/" + service_label()
    loaded = subprocess.run(["launchctl", "print", target], capture_output=True, timeout=5)
    if loaded.returncode not in (0, 113):
        raise RuntimeError("无法确认后台状态，请稍后重试。")
    if not agent.exists():
        agent.parent.mkdir(parents=True, exist_ok=True)
        replace_bytes(agent, original)
    if loaded.returncode == 113:
        subprocess.run(["launchctl", "bootstrap", domain, str(agent)], check=True, capture_output=True, timeout=10)
    else:
        subprocess.run(["launchctl", "kickstart", target], check=True, capture_output=True, timeout=10)
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        current = health(config["port"])
        if current and current.get("install_id") == config["install_id"]:
            print("原服务已启动，未下载 Python 或服务依赖。", flush=True)
            return
        time.sleep(0.5)
    raise RuntimeError("原服务未能启动，请检查后台项权限或修复组件。")


def open_dashboard():
    root = root_dir()
    manifest = root / "installation.json"
    if not manifest.exists():
        raise RuntimeError("尚未安装，请先双击“安装 ModelBridge.command”。")
    config = json.loads(manifest.read_text())
    current = health(config["port"])
    if not current or current.get("install_id") != config["install_id"]:
        agent = launch_agent()
        if not agent.exists():
            raise RuntimeError("服务已停用。请重新运行安装入口恢复。")
        subprocess.run(["launchctl", "kickstart", "gui/" + str(os.getuid()) + "/" + service_label()], capture_output=True)
        for _ in range(50):
            current = health(config["port"])
            if current and current.get("install_id") == config["install_id"]:
                break
            time.sleep(1)
        else:
            raise RuntimeError("本地服务启动失败，请重新运行安装入口修复。")
    token = Store(root / "data").admin
    result = subprocess.run(["open", f"http://127.0.0.1:{config['port']}/#token={token}"], capture_output=True)
    if result.returncode:
        raise RuntimeError("浏览器未能打开，请重新运行“打开 ModelBridge.command”。")


def uninstall():
    root = root_dir()
    if not (root / "installation.json").exists():
        print("未找到 ModelBridge 的安装记录。")
        return
    config = json.loads((root / "installation.json").read_text())
    stop_service(config)
    agent = launch_agent()
    if agent.exists():
        agent.replace(root / "disabled-launchagent.plist")
    print("已停止服务并取消登录启动。配置与运行文件保留，重新安装可恢复。")
    print("Claude 的对话和配置未修改。确认不再需要后，可在访达删除：", root)


def repair_service_name():
    """Rename an existing installation without upgrading its server or data."""
    root, agent = root_dir(), launch_agent()
    config = json.loads((root / "installation.json").read_text())
    release, data = Path(config["release"]), root / "data"
    old_bytes = agent.read_bytes()
    old = plistlib.loads(old_bytes)
    expected = service_arguments(release, data, config["port"], config["install_id"])
    if old.get("Label") != service_label() or old.get("ProgramArguments") != expected:
        raise RuntimeError("启动项已修改或已有应用名称，未覆盖。")
    before = health(config["port"])
    if not before or before.get("install_id") != config["install_id"]:
        raise RuntimeError("无法确认现有服务，未修改启动项。")
    token = json.loads((data / "admin.json").read_text())["token"]
    request = urllib.request.Request(f"http://127.0.0.1:{config['port']}/api/status", headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        if json.load(response)["requests"]["active"]:
            raise RuntimeError("Claude 正在生成回复，请等待完成后再修改后台项名称。")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    backup = root / "service-name-backups" / stamp
    backup.mkdir(parents=True, mode=0o700)
    replace_bytes(backup / agent.name, old_bytes)
    app = create_service_app(release, data, config["port"], config["install_id"],
                             root / "launchers" / stamp / (SERVICE_NAME + ".app"))
    updated = dict(old)
    updated["ProgramArguments"] = [str(app / "Contents/MacOS" / SERVICE_NAME), "--background"]
    updated["AssociatedBundleIdentifiers"] = [SERVICE_BUNDLE_ID]
    domain = "gui/" + str(os.getuid())
    stop_service(config)
    try:
        replace_bytes(agent, plistlib.dumps(updated))
        subprocess.run(["launchctl", "bootstrap", domain, str(agent)], check=True)
        for _ in range(55):
            if health(config["port"]) == before:
                break
            time.sleep(1)
        else:
            raise RuntimeError("命名后的服务未正常启动。")
    except BaseException:
        stop_service(config)
        replace_bytes(agent, old_bytes)
        subprocess.run(["launchctl", "bootstrap", domain, str(agent)], check=True)
        raise
    print("后台项已更新为：", SERVICE_NAME)
    print("原启动配置备份：", backup)
    print("服务版本、端口和模型配置保持不变。")


def main():
    global OVERRIDE_ROOT, OVERRIDE_LABEL
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "start", "open", "uninstall", "repair-name"])
    parser.add_argument("--uv")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--label")
    args = parser.parse_args()
    try:
        if bool(args.root) != bool(args.label):
            raise RuntimeError("独立安装必须同时指定目录与后台标识。")
        if args.root:
            import re
            if not args.root.is_absolute() or not re.fullmatch(r"com\.modelbridge\.[a-zA-Z0-9.-]+|com\.hairfree\.bridge", args.label):
                raise RuntimeError("安装目录或后台标识不正确。")
            OVERRIDE_ROOT, OVERRIDE_LABEL = args.root, args.label
        root_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (root_dir() / ".installer.lock").open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一项安装操作正在进行，请稍后重试。")
        if args.action == "install":
            if not args.uv:
                raise RuntimeError("缺少 uv 路径。请使用 .command 安装入口。")
            install(args.uv, not args.no_open)
        elif args.action == "open":
            open_dashboard()
        elif args.action == "start":
            start_existing()
        elif args.action == "repair-name":
            repair_service_name()
        else:
            uninstall()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("操作未完成：", str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
