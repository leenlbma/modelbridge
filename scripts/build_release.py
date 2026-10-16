"""Build an allowlisted source distribution; never include runtime or user data."""
import hashlib
from pathlib import Path
import sys
import zipfile
from check_public import check, source_paths

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bridge import __version__

paths = source_paths()
check(paths)
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
name = "ModelBridge-" + __version__ + "-macOS"
archive = dist / (name + ".zip")
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
    for p in sorted(paths):
        info = zipfile.ZipInfo(name + "/" + str(p.relative_to(ROOT)))
        info.create_system = 3
        info.external_attr = ((0o100755 if p.suffix == ".command" else 0o100644) << 16)
        info.compress_type = zipfile.ZIP_DEFLATED
        bundle.writestr(info, p.read_bytes())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(dist / "SHA256SUMS.txt").write_text(digest + "  " + archive.name + "\n")
print(str(archive))
print("SHA256 " + digest)
print("Files:", len(paths))
