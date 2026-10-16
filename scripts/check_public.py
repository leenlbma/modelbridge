"""Fail on accidental private files or common secret patterns without echoing them.

This is a narrow publication guard, not a substitute for manual security review.
"""
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = (
    'README.md', 'README_EN.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
    'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md', '.gitignore',
    '.gitattributes', '.editorconfig', 'requirements.in', 'requirements.lock',
    'installer.py', 'package.json', 'package-lock.json',
    '安装 ModelBridge.command', '打开 ModelBridge.command', '卸载 ModelBridge.command',
)
DIRECTORIES = ('bridge', 'desktop', 'web', 'docs', 'scripts', 'tests', 'macos', '.github')
EXCLUDED_PARTS = {'__pycache__', '.DS_Store'}
PRIVATE_PARTS = {'archive', 'evidence', '.test-state', 'runtime', '.desktop-build',
                 'desktop-dist', 'dist', 'node_modules', '.venv'}
PRIVATE_NAMES = {'admin.json', 'profile.json', 'installation.json'}
PATTERNS = {
    'API key': re.compile(r'\bsk-[A-Za-z0-9_-]{24,}'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential URL': re.compile(r'https?://[^\s/]+:[^\s/@]+@'),
    'internal hostname': re.compile(r'\b(?:[A-Za-z0-9-]+\.)*(?:corp|internal)\.[A-Za-z0-9.-]+'),
    'personal path': re.compile(r'/' + r'Users/[^/\s"\']+/'),
}


def source_paths(root=ROOT):
    paths = [root / name for name in TOP_LEVEL]
    for directory in DIRECTORIES:
        paths.extend(p for p in (root / directory).rglob('*')
                     if p.is_file() and not EXCLUDED_PARTS.intersection(p.parts)
                     and p.suffix != '.pyc')
    return sorted(set(paths))


def findings(path, root=ROOT):
    relative = path.relative_to(root)
    issues = []
    if (PRIVATE_PARTS.intersection(relative.parts) or path.name in PRIVATE_NAMES
            or path.name.startswith('.env') or path.suffix in {'.pem', '.key', '.p12', '.pfx', '.log'}):
        return [f'{relative}: private runtime file']
    if path.is_symlink():
        return [f'{relative}: symlinks are not allowed in public source']
    if not path.exists():
        return [f'{relative}: missing required file']
    data = path.read_bytes()
    if path.suffix in {'.png', '.icns'}:
        expected = b'\x89PNG\r\n\x1a\n' if path.suffix == '.png' else b'icns'
        return [] if data.startswith(expected) else [f'{relative}: invalid asset format']
    try:
        content = data.decode('utf8')
    except UnicodeDecodeError:
        return [f'{relative}: unreviewed binary file']
    for number, line in enumerate(content.splitlines(), 1):
        # Exact synthetic fixtures exercise URL rejection and shell quoting.
        if str(relative) == 'tests/test_providers.py':
            line = line.replace('https://' + 'user:pass@host/v1', 'INVALID_CREDENTIAL_URL')
        if str(relative) == 'tests/test_bridge.py':
            line = line.replace('/Users/test user/', '/fixture/')
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                issues.append(f'{relative}:{number}: possible {label}')
    if path.suffix == '.md':
        for target in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', content):
            if '://' not in target and not target.startswith(('#', 'mailto:')):
                destination = target.split('#', 1)[0]
                if destination and not (path.parent / destination).exists():
                    issues.append(f'{relative}: missing local documentation link')
    return issues


def check(paths, root=ROOT):
    issues = [issue for path in paths for issue in findings(path, root)]
    package = json.loads((root / 'package.json').read_text())
    lock = json.loads((root / 'package-lock.json').read_text())
    version = ast.literal_eval((root / 'bridge/__init__.py').read_text().split('=', 1)[1].strip())
    if not version == package['version'] == lock['version'] == lock['packages']['']['version']:
        issues.append('Version metadata differs between Python, package.json and lockfile')
    if package.get('license') != 'MIT' or lock['packages'][''].get('license') != 'MIT':
        issues.append('Package license metadata is missing')
    if issues:
        raise SystemExit('\n'.join(issues))


def main():
    paths = set(source_paths())
    if (ROOT / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
        paths.update(ROOT / name for name in result.stdout.decode().split('\0') if name)
    check(sorted(paths))
    print(f'Public-file checks passed: {len(paths)} files; no findings in configured rules.')


if __name__ == '__main__':
    main()
