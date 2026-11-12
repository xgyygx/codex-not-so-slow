"""Only synthetic fixtures; never reads the user's real Codex data."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zlib
import struct

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'repair_context.py'
spec = importlib.util.spec_from_file_location('repair_context', SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
TARGET = '00000000-0000-4000-8000-000000000001'
CURRENT = '00000000-0000-4000-8000-000000000002'
CHILD = '00000000-0000-4000-8000-000000000003'


def png():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    # Deterministic real RGB PNG with enough bytes for absolute path replacement.
    pixels = b'\x00' + bytes(i % 251 for i in range(3 * 1024))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1024, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels, 0)) + chunk(b'IEND', b'')


def fixture():
    image = {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(png()).decode(), 'detail': 'high'}
    msg = {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '保留这段原文'}, image, copy.deepcopy(image)]}
    records = [
        {'type': 'session_meta', 'payload': {'id': TARGET}},
        {'type': 'response_item', 'payload': copy.deepcopy(msg)},
        {'timestamp': 'synthetic', 'type': 'compacted', 'payload': {
            'message': 'opaque-summary-preserve', 'replacement_history': [msg, {'type': 'reasoning', 'encrypted_content': 'opaque'}]}},
        {'type': 'response_item', 'payload': copy.deepcopy(msg)}]
    return b''.join(r.encode(row) + b'\n' for row in records)


class TransformTests(unittest.TestCase):
    def test_only_latest_checkpoint_image_fields_change(self):
        data = fixture()
        new, report, assets = r.transform(data, TARGET, Path('/private/synthetic/images'))
        self.assertEqual(len(new), len(data))
        self.assertEqual(report['image_count'], 2)
        self.assertEqual(len(assets), 1)
        oldlines, newlines = data.splitlines(True), new.splitlines(True)
        self.assertEqual([len(x) for x in oldlines], [len(x) for x in newlines])
        self.assertEqual(oldlines[:2], newlines[:2])
        self.assertEqual(oldlines[3:], newlines[3:])
        old, changed = json.loads(oldlines[2]), json.loads(newlines[2])
        for item in report['images']:
            mi, ci = item['message_index'], item['content_index']
            self.assertEqual(changed['payload']['replacement_history'][mi]['content'][ci]['type'], 'input_text')
            changed['payload']['replacement_history'][mi]['content'][ci] = old['payload']['replacement_history'][mi]['content'][ci]
        self.assertEqual(changed, old)
        self.assertEqual(report['suffix_media_nodes_preserved'], 2)

    def test_incomplete_identity_and_missing_checkpoint(self):
        for data, ident in [(fixture()[:-1], TARGET), (fixture(), CURRENT),
                            (r.encode({'type': 'session_meta', 'payload': {'id': TARGET}}) + b'\n', TARGET)]:
            with self.subTest(ident=ident, length=len(data)), self.assertRaises(r.Stop):
                r.transform(data, ident, Path('/private/images'))

    def test_unknown_image_schema_and_remote_image(self):
        records = [json.loads(x) for x in fixture().splitlines()]
        for change in [{'type': 'output_image', 'data': 'opaque'}, {'type': 'unknown', 'image_url': 'opaque'}, {'type': 'image', 'source': {}}, {'type': 'input_image', 'image_url': 'https://example.invalid/image.png'}]:
            rows = copy.deepcopy(records)
            rows[2]['payload']['replacement_history'][0]['content'][1] = change
            with self.assertRaises(r.Stop):
                r.transform(b''.join(r.encode(x) + b'\n' for x in rows), TARGET, Path('/private/images'))

    def test_mime_signature_and_base64(self):
        for url in ['data:image/jpeg;base64,' + base64.b64encode(png()).decode(), 'data:image/png;base64,?']:
            with self.assertRaises(r.Stop):
                r.decode_image({'image_url': url})

    def test_no_images_and_oversized_paths(self):
        data = fixture()
        new, _, _ = r.transform(data, TARGET, Path('/private/images'))
        with self.assertRaises(r.Stop):
            r.transform(new, TARGET, Path('/private/images'))
        with self.assertRaises(r.Stop):
            r.transform(data, TARGET, Path('/' + 'x' * 10000))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'codex'
        (self.home / 'sessions').mkdir(parents=True)
        (self.home / 'archived_sessions').mkdir()
        self.path = self.home / 'sessions' / 'rollout.jsonl'
        self.original = fixture()
        self.path.write_bytes(self.original)
        self.db = sqlite3.connect(self.home / 'state_5.sqlite')
        self.db.executescript('CREATE TABLE threads (id TEXT,rollout_path TEXT,archived INTEGER,attached_thread_parent_id TEXT); CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT);')
        for ident in [TARGET, CURRENT, CHILD]:
            self.db.execute('INSERT INTO threads VALUES (?,?,0,NULL)', (ident, str(self.path)))
        self.db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)', (TARGET, CHILD))
        self.db.commit()
        self.root = self.base / 'private-repair'
        self.repair = r.Repair(self.home, TARGET, CURRENT, self.root)
        self.mock_run = patch.object(r.subprocess, 'run', return_value=type('Result', (), {'returncode': 1, 'stdout': '', 'stderr': ''})())
        self.mock_run.start()
        self.mock_sleep = patch.object(r.time, 'sleep')
        self.mock_sleep.start()
        self.mock_platform = patch.object(r.sys, 'platform', 'darwin')
        self.mock_platform.start()

    def tearDown(self):
        self.mock_platform.stop()
        self.mock_sleep.stop()
        self.mock_run.stop()
        self.db.close()
        self.tmp.cleanup()

    def archive(self):
        dest = self.home / 'archived_sessions' / self.path.name
        self.path.rename(dest)
        self.path = dest
        self.db.execute('UPDATE threads SET archived=1,rollout_path=? WHERE id IN (?,?)', (str(dest), TARGET, CHILD))
        self.db.commit()

    def prepare_archive(self):
        self.repair.prepare()
        self.archive()

    def test_inspect_is_read_only(self):
        result = self.repair.inspect()
        self.assertEqual(result['image_count'], 2)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_full_workflow_and_rollback(self):
        self.prepare_archive()
        result = self.repair.apply()
        self.assertEqual(result['state'], 'applied_verified')
        self.assertEqual(self.repair.verify()['state'], 'disk_verified')
        self.assertFalse(self.repair.verify()['archive_states_restored'])
        self.assertEqual(Path(result['backup']).read_bytes(), self.original)
        self.assertEqual(self.repair.rollback()['state'], 'rolled_back_verified')
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_unarchived_and_live_handles_stop(self):
        self.repair.prepare()
        with self.assertRaises(r.Stop):
            self.repair.apply()
        self.archive()
        with patch.object(r.subprocess, 'run', return_value=type('Result', (), {'returncode': 0, 'stdout': '123', 'stderr': ''})()):
            with self.assertRaises(r.Stop):
                self.repair.apply()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_inconclusive_handle_check_stops(self):
        self.prepare_archive()
        with patch.object(r.subprocess, 'run', return_value=type('Result', (), {'returncode': 1, 'stdout': '', 'stderr': 'permission denied'})()):
            with self.assertRaises(r.Stop):
                self.repair.apply()

    def test_asset_tamper_and_missing_asset_stop(self):
        self.prepare_archive()
        asset = next((self.root / 'images').iterdir())
        asset.write_bytes(b'changed')
        with self.assertRaises(r.Stop):
            self.repair.apply()
        asset.unlink()
        with self.assertRaises(OSError):
            self.repair.apply()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_backup_tamper_stops(self):
        self.prepare_archive()
        next((self.root / 'backups').iterdir()).write_bytes(b'changed')
        with self.assertRaises(r.Stop):
            self.repair.apply()

    def test_append_preserved_and_rollback_stops_after_new_turn(self):
        self.prepare_archive()
        suffix = r.encode({'type': 'event_msg', 'payload': {'message': 'new turn'}}) + b'\n'
        with self.path.open('ab') as f:
            f.write(suffix)
        self.repair.apply()
        self.assertTrue(self.path.read_bytes().endswith(suffix))
        with self.path.open('ab') as f:
            f.write(suffix)
        self.assertEqual(self.repair.verify()['appended_bytes'], len(suffix))
        with self.assertRaises(r.Stop):
            self.repair.rollback()

    def test_new_checkpoint_stops(self):
        self.prepare_archive()
        with self.path.open('ab') as f:
            f.write(fixture().splitlines(True)[2].replace(b'synthetic', b'newer'))
        with self.assertRaises(r.Stop):
            self.repair.apply()

    def test_changed_existing_record_stops(self):
        self.prepare_archive()
        self.path.write_bytes(self.original.replace(b'opaque-summary', b'changed-summary'))
        with self.assertRaises(r.Stop):
            self.repair.apply()

    def test_repeat_prepare_apply_stop(self):
        self.prepare_archive()
        with self.assertRaises(r.Stop):
            self.repair.prepare()
        self.repair.apply()
        with self.assertRaises(r.Stop):
            self.repair.apply()

    def test_current_and_ancestor_rejected(self):
        for current in (TARGET, CHILD):
            with self.assertRaises(r.Stop):
                r.Repair(self.home, TARGET, current, self.root).state()

    def test_attached_ancestor_rejected(self):
        self.db.execute('UPDATE threads SET attached_thread_parent_id=? WHERE id=?', (TARGET, CURRENT))
        self.db.commit()
        with self.assertRaises(r.Stop):
            self.repair.state()

    def test_subtree_change_stops(self):
        self.prepare_archive()
        self.db.execute('DELETE FROM thread_spawn_edges')
        self.db.commit()
        with self.assertRaises(r.Stop):
            self.repair.apply()

    def test_journal_precedes_mutation(self):
        self.prepare_archive()
        original_atomic = r.atomic
        def fail_rollout(path, data, mode=0o600):
            if path == self.path:
                self.assertEqual(json.loads((self.root / 'applied.json').read_bytes())['state'], 'pending')
                raise OSError('synthetic interruption')
            return original_atomic(path, data, mode)
        with patch.object(r, 'atomic', side_effect=fail_rollout), self.assertRaises(OSError):
            self.repair.apply()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_malformed_appended_json_stops_verification(self):
        self.prepare_archive()
        self.repair.apply()
        with self.path.open('ab') as f:
            f.write(b'{bad-json}\n')
        with self.assertRaises(ValueError):
            self.repair.verify()

    def test_apply_race_retains_new_messages(self):
        self.prepare_archive()
        real_atomic = r.atomic
        suffix = b'{"type":"event_msg","payload":{"message":"racing append"}}\n'
        def racing_atomic(path, data, mode=0o600):
            if path == self.path:
                with path.open('ab') as stream:
                    stream.write(suffix)
            return real_atomic(path, data, mode)
        with patch.object(r, 'atomic', side_effect=racing_atomic), self.assertRaises(r.Stop):
            self.repair.apply()
        result = json.loads((self.root / 'applied.json').read_bytes())
        self.assertEqual(result['state'], 'pending')
        self.assertEqual(Path(result['recovery_inode']).read_bytes(), self.original + suffix)
        self.assertEqual(Path(result['backup']).read_bytes(), self.original)
        with self.assertRaises(r.Stop):
            self.repair.verify()

    def test_late_old_inode_append_remains_recoverable(self):
        self.prepare_archive()
        original_fd = self.path.open('ab')  # Simulate a hidden late writer; lsof is mocked.
        try:
            result = self.repair.apply()
            original_fd.write(b'{"type":"late-turn"}\n')
            original_fd.flush()
        finally:
            original_fd.close()
        self.assertTrue(Path(result['recovery_inode']).read_bytes().endswith(b'{"type":"late-turn"}\n'))
        with self.assertRaises(r.Stop):
            self.repair.verify()
        with self.assertRaises(r.Stop):
            self.repair.rollback()

    def test_rollback_race_retains_new_messages(self):
        self.prepare_archive()
        self.repair.apply()
        repaired = self.path.read_bytes()
        real_atomic = r.atomic
        suffix = b'{"type":"event_msg","payload":{"message":"rollback append"}}\n'
        def racing_atomic(path, data, mode=0o600):
            if path == self.path:
                with path.open('ab') as stream:
                    stream.write(suffix)
            return real_atomic(path, data, mode)
        with patch.object(r, 'atomic', side_effect=racing_atomic), self.assertRaises(r.Stop):
            self.repair.rollback()
        result = json.loads((self.root / 'applied.json').read_bytes())
        self.assertEqual(Path(result['rollback_recovery_inode']).read_bytes(), repaired + suffix)

    def test_cross_volume_hardlink_failure_does_not_replace(self):
        self.prepare_archive()
        with patch.object(r.os, 'link', side_effect=OSError('Cross-device link')), self.assertRaises(OSError):
            self.repair.apply()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse((self.root / 'applied.json').exists())

    def test_inode_swap_stops_before_replacement(self):
        self.prepare_archive()
        real_link = r.os.link
        def changed_inode(src, dest):
            r.atomic(src, src.read_bytes())
            real_link(src, dest)
        with patch.object(r.os, 'link', side_effect=changed_inode), self.assertRaises(r.Stop):
            self.repair.apply()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_symlink_and_repository_data_paths_stop(self):
        link = self.base / 'linked'
        link.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(r.Stop):
            r.Repair(link, TARGET, CURRENT, self.root)
        (self.base / '.git').mkdir()
        with self.assertRaises(r.Stop):
            r.Repair(self.home, TARGET, CURRENT, self.root)

    def test_compressed_target_stops(self):
        self.db.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(self.path) + '.zst', TARGET))
        self.db.commit()
        with self.assertRaises(r.Stop):
            self.repair.inspect()


if __name__ == '__main__':
    unittest.main()
