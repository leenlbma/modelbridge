"""Publication guard regressions. All secret-like inputs here are synthetic."""
from pathlib import Path
import tempfile
import unittest

from scripts.check_public import findings, source_paths


class PublicationTests(unittest.TestCase):
    def test_secret_detection_never_echoes_the_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'example.txt'
            fake = 'sk-' + '0' * 48
            path.write_text(fake)
            result = findings(path, root)
            self.assertTrue(result)
            self.assertNotIn(fake, '\n'.join(result))

    def test_private_state_and_unreviewed_binaries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, content in [('profile.json', b'{}'), ('sample.bin', b'\xff\xfe')]:
                path = root / name
                path.write_bytes(content)
                self.assertTrue(findings(path, root))

    def test_source_bundle_includes_license_but_no_local_evidence_or_state(self):
        paths = source_paths()
        self.assertTrue(any(path.name == 'LICENSE' for path in paths))
        self.assertTrue(any(path.name == 'ci.yml' for path in paths))
        self.assertFalse(any({'evidence', 'archive', '.test-state'}.intersection(path.parts) for path in paths))
