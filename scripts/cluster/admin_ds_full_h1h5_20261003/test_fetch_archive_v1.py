"""Offline safety tests: no SSH, inference, processes, or frozen-file mutation."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('fetch_archive_v1_tested', Path(__file__).with_name('fetch_archive_v1.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'root'
        self.output = self.base / 'export'
        self.snapshot = self.base / 'snapshot'
        self.root.mkdir()
        self.output.mkdir()
        for name in ('code', 'sessions', 'launches', 'prepared', 'incoming', 'exports'):
            (self.root / name).mkdir()
            (self.root / name / 'fixture.txt').write_text(name)
        for name in ('stage_manifest.json', 'latest_session.json', 'dispatch.lock', 'coordinator.lock', 'stage.lock'):
            (self.root / name).write_text('{}')

    def archive(self):
        receipt = m.build_archive(self.root, self.output)
        m.write_new(self.output / 'exit.json', {'success': True, 'worker_finished': True})
        return receipt

    def test_roundtrip_excludes_inputs_preserves_evidence(self):
        receipt = self.archive()
        self.assertEqual(m.validate_download(self.output), receipt)
        expected = m.extract_verified(self.output, self.snapshot, receipt)
        self.assertTrue('code/fixture.txt' in expected)
        self.assertFalse(any(n.startswith(('prepared/', 'incoming/', 'exports/')) for n in expected))
        self.assertEqual((self.snapshot / 'sessions/fixture.txt').read_text(), 'sessions')
        self.assertEqual(m.extract_verified(self.output, self.snapshot, receipt), expected)

    def test_archive_refuses_existing_partial(self):
        (self.output / 'evidence.tar.gz.building').write_bytes(b'partial')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            m.build_archive(self.root, self.output)
        self.assertEqual((self.output / 'evidence.tar.gz.building').read_bytes(), b'partial')

    def test_source_symlink_rejected(self):
        (self.root / 'code/linked').symlink_to(self.root / 'prepared/fixture.txt')
        with self.assertRaisesRegex(RuntimeError, 'Nonregular'):
            m.build_archive(self.root, self.output)

    def test_download_rejects_archive_tamper(self):
        self.archive()
        with (self.output / 'evidence.tar.gz').open('ab') as stream:
            stream.write(b'bad')
        with self.assertRaisesRegex(RuntimeError, 'hash/size'):
            m.validate_download(self.output)

    def test_download_requires_real_success_exit(self):
        self.archive()
        (self.output / 'exit.json').write_text('{"success":false,"worker_finished":true}')
        with self.assertRaisesRegex(RuntimeError, 'successful exit'):
            m.validate_download(self.output)

    def test_extract_existing_file_never_overwritten(self):
        receipt = self.archive()
        (self.snapshot / 'code').mkdir(parents=True)
        (self.snapshot / 'code/fixture.txt').write_text('different')
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            m.extract_verified(self.output, self.snapshot, receipt)
        self.assertEqual((self.snapshot / 'code/fixture.txt').read_text(), 'different')

    def test_extraction_target_symlink_rejected(self):
        receipt = self.archive()
        self.snapshot.mkdir()
        (self.snapshot / 'code').symlink_to(self.root / 'code')
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            m.extract_verified(self.output, self.snapshot, receipt)

    def malicious(self, names, link=False):
        values = []
        with tarfile.open(self.output / 'evidence.tar.gz', 'w:gz') as archive:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 1
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/tmp/target'
                archive.addfile(info, io.BytesIO(b'x'))
                values.append({'path': name, 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()})
        (self.output / 'members.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in values))
        return {'member_count': len(values)}

    def test_traversal_absolute_excluded_and_noncanonical_rejected(self):
        for name in ('../outside', '/etc/passwd', 'code/../outside', 'prepared/a', 'code//a', './code/a'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                m.member_name(name)

    def test_duplicate_inventory_rejected(self):
        receipt = self.malicious(['code/a', 'code/a'])
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            m.extract_verified(self.output, self.snapshot, receipt)

    def test_archive_symlink_rejected(self):
        receipt = self.malicious(['code/a'], link=True)
        with self.assertRaisesRegex(RuntimeError, 'Nonregular'):
            m.extract_verified(self.output, self.snapshot, receipt)

    def test_missing_archive_member_rejected(self):
        receipt = self.malicious(['code/a'])
        with tarfile.open(self.output / 'evidence.tar.gz', 'w:gz'):
            pass
        with self.assertRaisesRegex(RuntimeError, 'Missing archive'):
            m.extract_verified(self.output, self.snapshot, receipt)

    def test_prepared_reuse_exact_copy_not_symlink(self):
        self.snapshot.mkdir()
        source = self.root / 'prepared'
        for name in m.PREPARED:
            (source / name).write_text(name)
        m.write_new(self.snapshot / 'stage_manifest.json', {'files': {'prepared/' + n: m.digest(source / n) for n in m.PREPARED}})
        m.reuse_prepared(source, self.snapshot)
        m.reuse_prepared(source, self.snapshot)
        for name in m.PREPARED:
            self.assertEqual((source / name).read_bytes(), (self.snapshot / 'prepared' / name).read_bytes())
            self.assertFalse((self.snapshot / 'prepared' / name).is_symlink())
            self.assertNotEqual((source / name).stat().st_ino, (self.snapshot / 'prepared' / name).stat().st_ino)

    def test_prepared_mismatch_rejected(self):
        self.snapshot.mkdir()
        m.write_new(self.snapshot / 'stage_manifest.json', {'files': {'prepared/' + n: 'wrong' for n in m.PREPARED}})
        (self.root / 'prepared' / m.PREPARED[0]).write_text('source')
        with self.assertRaisesRegex(RuntimeError, 'frozen prepared mismatch'):
            m.reuse_prepared(self.root / 'prepared', self.snapshot)

    def test_same_live_checks_start_ticks_and_zombie(self):
        identity = {'pid': 7, 'start_ticks': '50', 'state': 'S'}
        self.assertTrue(m.same_live(identity, identity))
        self.assertFalse(m.same_live(identity, {**identity, 'start_ticks': '51'}))
        self.assertFalse(m.same_live(identity, {**identity, 'state': 'Z'}))


if __name__ == '__main__':
    unittest.main()
