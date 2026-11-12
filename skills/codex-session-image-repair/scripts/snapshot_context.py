#!/usr/bin/env python3
"""Back up a compatible idle chat and index available post-checkpoint tool results.

Read-only toward Codex. This helper does NOT compact, trim, message, enable a mode,
or change app settings. The caller must verify idle state with first-party tools.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

from repair_context import Repair, Stop, require, no_symlinks, snapshot, sha, encode, atomic, save, checkpoint


def summarize(data, thread):
    offset, ordinal, line, last = checkpoint(data, thread)
    history = last['payload']['replacement_history']
    later = []
    outputs = bytearray()
    index = []
    calls = {}
    token = None
    for raw in data.splitlines(keepends=True):
        row = json.loads(raw)
        payload = row.get('payload', {})
        if row.get('type') == 'event_msg' and payload.get('type') == 'token_count':
            token = {'timestamp': row.get('timestamp'),
                     'last_token_usage': (payload.get('info') or {}).get('last_token_usage'),
                     'model_context_window': (payload.get('info') or {}).get('model_context_window')}
    for raw in data[offset + len(line):].splitlines(keepends=True):
        row = json.loads(raw)
        if row.get('type') != 'response_item':
            continue
        payload = row['payload']
        later.append(payload)
        kind = payload.get('type')
        if kind in ('function_call', 'custom_tool_call'):
            calls[payload.get('call_id')] = payload.get('name')
        if kind in ('function_call_output', 'custom_tool_call_output'):
            entry = {'timestamp': row.get('timestamp'), 'call_id': payload.get('call_id'),
                     'name': calls.get(payload.get('call_id')),
                     'byte_offset': len(outputs), 'bytes': len(raw),
                     'truncation_marker_present': any(x in raw.lower() for x in
                         (b'truncated', '省略'.encode('utf-8')))}
            outputs.extend(raw)  # Exact available bytes, not a claimed full process log.
            index.append(entry)
    stats = {'checkpoint_timestamp': last.get('timestamp'), 'checkpoint_ordinal': ordinal,
             'checkpoint_json_bytes': len(encode(history)), 'suffix_json_bytes': len(encode(later)),
             'reconstructed_history_json_bytes': len(encode(history + later)),
             'last_usage': token, 'tool_output_count': len(index),
             'outputs_with_truncation_marker': sum(x['truncation_marker_present'] for x in index),
             'wire_request_bytes': None,
             'note': 'Local reconstructed history size is not measured network request size; exports contain only results actually present in the rollout.'}
    return stats, bytes(outputs), index


def create_snapshot(repair):
    path, states = repair.state()
    data, identity = snapshot(path)
    stats, results, index = summarize(data, repair.thread)
    root = repair.root
    no_symlinks(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0,
            'Snapshot directory must be owned by current user with mode 0700')
    paths = {name: root / name for name in ('before-maintenance.jsonl', 'available-tool-results.jsonl',
                                          'tool-index.json', 'baseline.json')}
    lock = root / '.snapshot.lock'
    no_symlinks(lock)
    with lock.open('a') as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for dest in paths.values():
            no_symlinks(dest)
            require(not dest.exists(), 'Existing snapshot artifact; choose a fresh directory')
        atomic(paths['before-maintenance.jsonl'], data)
        require(sha(paths['before-maintenance.jsonl'].read_bytes()) == sha(data), 'Backup verification failed')
        atomic(paths['available-tool-results.jsonl'], results)
        save(paths['tool-index.json'], index)
        require(repair.state() == (path, states) and snapshot(path) == (data, identity),
                'Source changed; snapshot retained, recheck idle state before maintenance')
        stats.update(thread_id=repair.thread, source=str(path), archive_states=states,
                     rollout_bytes=len(data), backup_sha256=sha(data),
                     tool_results_sha256=sha(results), tool_index_sha256=sha(paths['tool-index.json'].read_bytes()))
        save(paths['baseline.json'], stats)
    return stats


def verify_snapshot(repair):
    root = repair.root
    for name in ('baseline.json', 'before-maintenance.jsonl', 'available-tool-results.jsonl', 'tool-index.json'):
        no_symlinks(root / name)
    baseline = json.loads((root / 'baseline.json').read_bytes())
    require(baseline['thread_id'] == repair.thread, 'Snapshot identity mismatch')
    data = (root / 'before-maintenance.jsonl').read_bytes()
    require(sha(data) == baseline['backup_sha256'], 'Backup hash mismatch')
    results = (root / 'available-tool-results.jsonl').read_bytes()
    index_bytes = (root / 'tool-index.json').read_bytes()
    require(sha(results) == baseline['tool_results_sha256'] and
            sha(index_bytes) == baseline['tool_index_sha256'], 'Tool export hash mismatch')
    expected_stats, expected_results, expected_index = summarize(data, repair.thread)
    require(results == expected_results and json.loads(index_bytes) == expected_index,
            'Tool exports do not match backup')
    require(all(baseline.get(key) == value for key, value in expected_stats.items()) and
            baseline.get('rollout_bytes') == len(data), 'Snapshot baseline metrics do not match backup')
    path, states = repair.state()
    current = path.read_bytes()
    require(current.startswith(data), 'Live history prefix changed; preserve new content and investigate')
    checkpoint(current, repair.thread)
    require(path.read_bytes() == current, 'Live history changed during verification; retry when idle')
    return {'state': 'snapshot_verified', 'appended_bytes': len(current) - len(data),
            'archive_states_unchanged': states == baseline['archive_states'],
            'note': 'This verifies stored bytes, not live model behavior or speed.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['snapshot', 'verify'])
    parser.add_argument('--thread', required=True)
    parser.add_argument('--current-thread', required=True)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    repair = Repair(args.codex_home, args.thread, args.current_thread, args.data_dir)
    result = create_snapshot(repair) if args.mode == 'snapshot' else verify_snapshot(repair)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(2)
