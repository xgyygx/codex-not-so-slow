"""Synthetic-only tests for opt-in context snapshot/export; no real chats."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_repair_context as fixture

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPT_DIR))
import snapshot_context as snap
import repair_context as repair_module


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        fixture.WorkflowTests.setUp(self)
        # Use the helper's imported Repair, whose guards are independently tested.
        self.repair = repair_module.Repair(self.home, fixture.TARGET, fixture.CURRENT, self.root)
        extra = [
            {'type': 'response_item', 'timestamp': 't1', 'payload': {'type': 'function_call', 'name': 'read_fixture', 'call_id': 'c1', 'arguments': '{}'}},
            {'type': 'response_item', 'timestamp': 't2', 'payload': {'type': 'function_call_output', 'call_id': 'c1', 'output': [{'type': 'input_text', 'text': '完整证据：42。 Warning: truncated output'}]}},
            {'type': 'event_msg', 'timestamp': 't3', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': 123}, 'model_context_window': 1000}}},
        ]
        with self.path.open('ab') as f:
            for row in extra:
                f.write(repair_module.encode(row) + b'\n')
        self.before = self.path.read_bytes()

    def tearDown(self):
        fixture.WorkflowTests.tearDown(self)

    def test_snapshot_leaves_source_unchanged_and_verifies(self):
        result = snap.create_snapshot(self.repair)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual((self.root / 'before-maintenance.jsonl').read_bytes(), self.before)
        self.assertEqual(result['tool_output_count'], 1)
        self.assertEqual(result['outputs_with_truncation_marker'], 1)
        self.assertIsNone(result['wire_request_bytes'])
        self.assertEqual(result['last_usage']['last_token_usage']['input_tokens'], 123)
        self.assertEqual(snap.verify_snapshot(self.repair)['state'], 'snapshot_verified')
        self.assertFalse((self.root / 'mode.json').exists())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for name in ('before-maintenance.jsonl', 'available-tool-results.jsonl', 'tool-index.json', 'baseline.json'):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)

    def test_byte_index_reads_exact_utf8_record(self):
        snap.create_snapshot(self.repair)
        index = json.loads((self.root / 'tool-index.json').read_bytes())
        export = (self.root / 'available-tool-results.jsonl').read_bytes()
        row = index[0]
        exact = export[row['byte_offset']:row['byte_offset'] + row['bytes']]
        self.assertIn(exact, self.before)
        self.assertEqual(json.loads(exact)['payload']['call_id'], 'c1')
        self.assertEqual(row['name'], 'read_fixture')

    def test_repeat_snapshot_does_not_overwrite(self):
        snap.create_snapshot(self.repair)
        with self.assertRaises(repair_module.Stop):
            snap.create_snapshot(self.repair)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_tampered_export_stops_verify(self):
        snap.create_snapshot(self.repair)
        (self.root / 'available-tool-results.jsonl').write_bytes(b'changed')
        with self.assertRaises(repair_module.Stop):
            snap.verify_snapshot(self.repair)

    def test_tampered_backup_stops_verify(self):
        snap.create_snapshot(self.repair)
        (self.root / 'before-maintenance.jsonl').write_bytes(b'changed')
        with self.assertRaises(repair_module.Stop):
            snap.verify_snapshot(self.repair)

    def test_tampered_baseline_metrics_stop_verify(self):
        snap.create_snapshot(self.repair)
        path = self.root / 'baseline.json'
        baseline = json.loads(path.read_bytes())
        for key, value in [('tool_output_count', 999), ('wire_request_bytes', 1234), ('last_usage', {'timestamp': 'forged'})]:
            altered = dict(baseline)
            altered[key] = value
            path.write_text(json.dumps(altered))
            with self.subTest(field=key), self.assertRaises(repair_module.Stop):
                snap.verify_snapshot(self.repair)
        path.write_text(json.dumps(baseline))
        self.assertEqual(snap.verify_snapshot(self.repair)['state'], 'snapshot_verified')

    def test_partial_appended_record_stops_verify(self):
        snap.create_snapshot(self.repair)
        with self.path.open('ab') as f:
            f.write(b'{"type":')
        with self.assertRaises(repair_module.Stop):
            snap.verify_snapshot(self.repair)

    def test_append_allowed_but_rewrite_rejected(self):
        snap.create_snapshot(self.repair)
        with self.path.open('ab') as f:
            f.write(b'{"type":"later"}\n')
        self.assertGreater(snap.verify_snapshot(self.repair)['appended_bytes'], 0)
        self.path.write_bytes(b'changed')
        with self.assertRaises(repair_module.Stop):
            snap.verify_snapshot(self.repair)

    def test_concurrent_append_stops_before_baseline(self):
        real_atomic = snap.atomic
        def race(path, data, mode=0o600):
            result = real_atomic(path, data, mode)
            if path.name == 'available-tool-results.jsonl':
                with self.path.open('ab') as f:
                    f.write(b'{"type":"later"}\n')
            return result
        with patch.object(snap, 'atomic', side_effect=race), self.assertRaises(repair_module.Stop):
            snap.create_snapshot(self.repair)
        self.assertFalse((self.root / 'baseline.json').exists())
        self.assertTrue(self.path.read_bytes().startswith(self.before))

    def test_symlink_destination_stops(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'before-maintenance.jsonl').symlink_to(self.path)
        with self.assertRaises(repair_module.Stop):
            snap.create_snapshot(self.repair)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_consent_text_is_user_supplied_verbatim(self):
        notice = SCRIPT_DIR.parent / 'references' / 'extreme-consent.txt'
        self.assertEqual(notice.read_text().rstrip('\n'), '**模型后续默认不携带完整的非报错中间过程，可能漏掉其中有用的细节；完整过程要先保存到文件，复盘时再按明确指令读取。** 错误、警告、关键数值和影响结论的证据仍要留在上下文里。')


if __name__ == '__main__':
    unittest.main()
