"""Build the full macOS app, with a verified uv bootstrap and backend source.

First run downloads managed Python and locked dependencies; no user data ships.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import tarfile
import urllib.request
from check_public import check, source_paths

ROOT = Path(__file__).resolve().parent.parent


def bundle_bootstrap(target):
    pin = json.loads((ROOT / 'desktop/bootstrap.json').read_text())
    cache = ROOT / '.desktop-build'
    cache.mkdir(exist_ok=True)
    archive = cache / pin['archive']
    if not archive.exists():
        with urllib.request.urlopen(pin['url'], timeout=60) as response, archive.open('wb') as dest:
            shutil.copyfileobj(response, dest)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != pin['sha256']:
        raise SystemExit('uv archive checksum mismatch')
    target.mkdir()
    with tarfile.open(archive) as bundle:
        member = bundle.getmember('uv-aarch64-apple-darwin/uv')
        if not member.isfile():
            raise SystemExit('uv archive member is not a regular file')
        binary = bundle.extractfile(member).read()
        (target / 'uv').write_bytes(binary)
        (target / 'uv').chmod(0o755)
    pin['binary_sha256'] = hashlib.sha256(binary).hexdigest()
    (target / 'bootstrap.json').write_text(json.dumps(pin, indent=2) + '\n')
    for name in ('LICENSE-MIT', 'LICENSE-APACHE'):
        license_file = cache / ('uv-' + name)
        if not license_file.exists():
            url = 'https://raw.githubusercontent.com/astral-sh/uv/' + pin['version'] + '/' + name
            license_file.write_bytes(urllib.request.urlopen(url, timeout=60).read())
        shutil.copy2(license_file, target / ('uv-' + name))
    backend = target / 'backend'
    backend.mkdir()
    for name in ('bridge', 'web', 'docs', 'macos'):
        shutil.copytree(ROOT / name, backend / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    for name in ('installer.py', 'requirements.lock', 'README.md', 'README_EN.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, backend / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--electron-zip', type=Path)
    parser.add_argument('--no-zip', action='store_true')
    parser.add_argument('--dmg', action='store_true')
    args = parser.parse_args()
    check(source_paths())
    pin = json.loads((ROOT / 'desktop/runtime.json').read_text())
    package = json.loads((ROOT / 'package.json').read_text())
    cache = Path.home() / 'Library/Caches/electron'
    candidates = list(cache.rglob(pin['archive'])) if cache.exists() else []
    archive = args.electron_zip or (candidates[0] if candidates else ROOT / '.desktop-build' / pin['archive'])
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        url = 'https://github.com/electron/electron/releases/download/v' + pin['version'] + '/' + pin['archive']
        with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as target:
            shutil.copyfileobj(response, target)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != pin['sha256']:
        raise SystemExit('Electron runtime checksum mismatch; nothing was packaged.')
    output = ROOT / 'desktop-dist' / ('ModelBridge-' + package['version'] + '-macOS-' + pin['arch'])
    output.mkdir(parents=True, exist_ok=True)
    app = output / 'ModelBridge.app'
    if app.exists():
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != 'com.modelbridge.desktop':
            raise SystemExit('Refusing to overwrite an unrelated application.')
        shutil.rmtree(app)
    with tempfile.TemporaryDirectory(prefix='modelbridge-electron-build-') as temporary:
        staging = Path(temporary)
        subprocess.run(['ditto', '-x', '-k', str(archive), str(staging)], check=True)
        shutil.move(str(staging / 'Electron.app'), app)
        contents = app / 'Contents'
        resources = contents / 'Resources'
        for name in ('LICENSE', 'LICENSES.chromium.html'):
            if (staging / name).exists():
                shutil.copy2(staging / name, resources / name)
        default = resources / 'default_app.asar'
        if default.exists():
            default.unlink()
        info_path = contents / 'Info.plist'
        info = plistlib.loads(info_path.read_bytes())
        info.update(CFBundleName='ModelBridge', CFBundleDisplayName='ModelBridge', CFBundleIdentifier='com.modelbridge.desktop',
                    CFBundleExecutable='ModelBridge', CFBundleVersion=package['version'], CFBundleShortVersionString=package['version'],
                    CFBundleIconFile='ModelBridge.icns', NSHumanReadableCopyright='ModelBridge desktop preview', LSMinimumSystemVersion='14.0')
        info_path.write_bytes(plistlib.dumps(info))
        (contents / 'MacOS/Electron').rename(contents / 'MacOS/ModelBridge')
        for helper in (contents / 'Frameworks').glob('Electron Helper*.app'):
            plist = helper / 'Contents/Info.plist'
            value = plistlib.loads(plist.read_bytes())
            executable = value.get('CFBundleExecutable', helper.stem)
            renamed = executable.replace('Electron', 'ModelBridge', 1)
            (helper / 'Contents/MacOS' / executable).rename(helper / 'Contents/MacOS' / renamed)
            value['CFBundleExecutable'] = renamed
            value['CFBundleName'] = value.get('CFBundleName', executable).replace('Electron', 'ModelBridge', 1)
            value['CFBundleIdentifier'] = value['CFBundleIdentifier'].replace('com.github.Electron', 'com.modelbridge.desktop')
            plist.write_bytes(plistlib.dumps(value))
            helper.rename(helper.with_name(helper.name.replace('Electron', 'ModelBridge', 1)))
        shutil.copy2(ROOT / 'macos/ModelBridge.icns', resources / 'ModelBridge.icns')
        target = resources / 'app'
        target.mkdir()
        allowlist = ['desktop/main.cjs', 'desktop/backend.cjs', 'desktop/setup.cjs', 'desktop/preload.cjs', 'desktop/trayTemplate.png', 'desktop/trayTemplate@2x.png',
                     'web/index.html', 'web/app.js', 'web/style.css', 'web/icon.svg', 'macos/ModelBridge.icns', 'LICENSE', 'THIRD_PARTY_NOTICES.md']
        for name in allowlist:
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        (target / 'package.json').write_text(json.dumps({key: package[key] for key in ('name', 'productName', 'version', 'description', 'main', 'private', 'license', 'repository')}, indent=2) + '\n')
        bundle_bootstrap(target / 'bootstrap')
    # Local ad-hoc signature for this build. This is not Developer ID signing or notarization.
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    (output / '使用说明.txt').write_text('ModelBridge ' + package['version'] + ' · Electron 完整客户端预览版\n\n将 ModelBridge.app 拖入应用程序目录后打开。\n首次打开先检查本机环境，已有兼容后台与 Python 直接复用，仅缺失时下载。无需终端。\n已有安装会自动连接；停止的服务优先原地恢复，旧版服务按需升级。\n选择服务、填写 Key 并验证，再到“接入 Claude”导出配置。\n关闭窗口可从菜单栏或 Dock 再次打开；退出客户端不会停止模型服务。\n状态与帮助 → 停用后台服务，可取消后台登录启动并保留数据。\n本包支持 macOS 14+ Apple Silicon；只有本机临时签名，尚未完成开发者签名与公证。\n如系统阻止打开，请检查来源后在“系统设置 → 隐私与安全性”允许本次打开。\nClaude Design、Cowork 及其他供应商真实账号兼容性未完成验收。\n', encoding='utf8')
    print('App:', app)
    checksums = []
    if not args.no_zip:
        release = output.parent / (output.name + '.zip')
        if release.exists(): release.unlink()
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(output), str(release)], check=True)
        digest = hashlib.sha256(release.read_bytes()).hexdigest()
        checksums.append(digest + '  ' + release.name)
        print('ZIP:', release)
        print('SHA256:', digest)
    if args.dmg:
        release = output.parent / (output.name + '.dmg')
        with tempfile.TemporaryDirectory(prefix='modelbridge-dmg-') as temporary:
            image_root = Path(temporary)
            subprocess.run(['ditto', str(app), str(image_root / app.name)], check=True)
            (image_root / 'Applications').symlink_to('/Applications')
            shutil.copy2(output / '使用说明.txt', image_root / '使用说明.txt')
            subprocess.run(['hdiutil', 'create', '-volname', 'ModelBridge', '-srcfolder', str(image_root), '-ov', '-format', 'UDZO', str(release)], check=True)
        subprocess.run(['hdiutil', 'verify', str(release)], check=True)
        checksums.append(hashlib.sha256(release.read_bytes()).hexdigest() + '  ' + release.name)
        print('DMG:', release)
    if checksums:
        (output.parent / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n')


if __name__ == '__main__':
    main()
