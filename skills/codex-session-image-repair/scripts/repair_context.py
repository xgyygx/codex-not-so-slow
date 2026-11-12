#!/usr/bin/env python3
"""Conservative macOS Codex checkpoint image externalizer; standard library only.

No app control or SQLite writes. Mutations require an already archived, released
thread. Runtime data belongs in a private durable directory, never in this Skill.
"""
import argparse
import base64
from contextlib import closing
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid


class Stop(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def no_symlinks(path):
    require(path.is_absolute(), 'Use absolute paths')
    require(not any(p.is_symlink() for p in [path, *path.parents]), 'Symlink path rejected')


def atomic(path, data, mode=0o600):
    no_symlinks(path)
    fd, tmp = tempfile.mkstemp(prefix='.context-repair-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(path, value):
    atomic(path, encode(value) + b'\n')


def inode(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def snapshot(path):
    with path.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        data = stream.read()
    identity = (stat.st_dev, stat.st_ino)
    require(inode(path) == identity, 'Rollout inode changed during read')
    return data, identity


def checkpoint(data, thread):
    require(data.endswith(b'\n'), 'Incomplete JSONL; wait for a completed write')
    offset, found = 0, None
    for ordinal, line in enumerate(data.splitlines(keepends=True)):
        record = json.loads(line)
        require(isinstance(record, dict), 'Unsupported JSONL record')
        if ordinal == 0:
            require(record.get('type') == 'session_meta' and
                    record.get('payload', {}).get('id') == thread, 'Session identity mismatch')
        if record.get('type') == 'compacted':
            found = (offset, ordinal, line, record)
        offset += len(line)
    require(found is not None, 'No compaction checkpoint; diagnosis only')
    hist = found[3].get('payload', {}).get('replacement_history')
    require(isinstance(hist, list), 'Unsupported replacement_history schema')
    return found


def decode_image(item):
    url = item.get('image_url')
    require(isinstance(url, str), 'Unsupported image field')
    match = re.fullmatch(r'data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=]+)', url)
    require(match is not None, 'Only supported inline base64 images are eligible')
    raw = base64.b64decode(match[2], validate=True)
    ext = None
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        ext = 'png'
    elif raw.startswith(b'\xff\xd8\xff'):
        ext = 'jpg'
    elif raw[:6] in (b'GIF87a', b'GIF89a'):
        ext = 'gif'
    elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        ext = 'webp'
    require(ext is not None and ext == {'jpeg': 'jpg'}.get(match[1], match[1]),
            'Unsupported or mismatched image signature')
    return raw, ext


def media_nodes(value):
    if isinstance(value, dict):
        kind = value.get('type', '')
        image_field = any(key in value for key in ('image_url', 'image_data'))
        image_mime = any(isinstance(value.get(key), str) and value[key].startswith('image/')
                         for key in ('media_type', 'mime_type'))
        image_url = any(isinstance(v, str) and v.startswith('data:image/') for v in value.values())
        return int((isinstance(kind, str) and 'image' in kind) or image_field or image_mime or image_url) + sum(media_nodes(v) for v in value.values())
    if isinstance(value, list):
        return sum(media_nodes(v) for v in value)
    return 0


def transform(data, thread, image_dir):
    """Pure transformation: return bytes, metrics, and assets. No filesystem IO."""
    offset, ordinal, line, original = checkpoint(data, thread)
    changed = copy.deepcopy(original)
    history = changed['payload']['replacement_history']
    manifest, assets = [], {}
    for mi, message in enumerate(history):
        require(isinstance(message, dict), 'Unsupported history item')
        content = message.get('content', [])
        require(isinstance(content, list), 'Unsupported content schema')
        for ci, item in enumerate(content):
            require(isinstance(item, dict), 'Unsupported content item')
            if item.get('type') != 'input_image':
                continue
            require(message.get('type') == 'message' and message.get('role') == 'user',
                    'Only user message images supported')
            raw, ext = decode_image(item)
            digest = sha(raw)
            name = digest + '.' + ext
            assets[name] = raw
            content[ci] = {'type': 'input_text', 'text':
                '[Historical image stored locally: ' + str(image_dir / name) +
                '. Open only when visual details are needed. Original transcript retained.]'}
            manifest.append({'message_index': mi, 'content_index': ci, 'sha256': digest,
                             'file': name, 'bytes': len(raw)})
    require(manifest, 'No eligible checkpoint images; no repair needed')
    require(media_nodes(history) == 0, 'Unsupported additional image schema; diagnosis only')
    restored = copy.deepcopy(changed)
    for item in manifest:
        mi, ci = item['message_index'], item['content_index']
        restored['payload']['replacement_history'][mi]['content'][ci] = original['payload']['replacement_history'][mi]['content'][ci]
    require(restored == original, 'Non-image field changed')
    compact = encode(changed)
    require(len(compact) < len(line), 'Path replacements do not fit the original byte span')
    new_line = compact + b' ' * (len(line) - len(compact) - 1) + b'\n'
    require(json.loads(new_line) == changed, 'JSON roundtrip failed')
    result = data[:offset] + new_line + data[offset + len(line):]
    require(len(result) == len(data), 'Byte offsets changed')
    metrics = dict(offset=offset, ordinal=ordinal, span_bytes=len(line),
                   checkpoint_sha256=sha(line), image_count=len(manifest), images=manifest,
                   history_bytes_before=len(encode(original['payload']['replacement_history'])),
                   history_bytes_after=len(encode(history)),
                   sha256_before=sha(data), sha256_after=sha(result),
                   suffix_media_nodes_preserved=sum(media_nodes(json.loads(x))
                       for x in data[offset + len(line):].splitlines()))
    return result, metrics, assets


class Repair:
    def __init__(self, home, thread, current, root):
        for value in (thread, current):
            require(str(uuid.UUID(value)) == value, 'Expected canonical thread UUID')
        self.home, self.thread, self.current, self.root = home, thread, current, root
        no_symlinks(home)
        no_symlinks(root)
        no_symlinks(home / 'state_5.sqlite')
        require(home.is_dir(), 'Codex home not found')
        # Data must survive Skill updates and must never be committed with code.
        require(not any((p / '.git').exists() or (p / 'SKILL.md').exists()
                        for p in [root, *root.parents]), 'Choose a data directory outside repositories and Skills')
        require(not root.is_relative_to(home / 'sessions') and
                not root.is_relative_to(home / 'archived_sessions'), 'Data directory overlaps session storage')

    def state(self):
        uri = (self.home / 'state_5.sqlite').as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT id,rollout_path,archived FROM threads').fetchall()
            edges = db.execute('SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges').fetchall()
            columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
            if 'attached_thread_parent_id' in columns:
                edges += db.execute('SELECT attached_thread_parent_id,id FROM threads WHERE attached_thread_parent_id IS NOT NULL').fetchall()
        threads = {r['id']: dict(r) for r in rows}
        require(self.thread in threads and self.current in threads, 'Target/current thread missing from state DB')
        subtree = {self.thread}
        while True:
            expanded = subtree | {child for parent, child in edges if parent in subtree}
            if expanded == subtree:
                break
            subtree = expanded
        require(self.current not in subtree, 'Target is current chat or its ancestor; use another independent chat')
        require(subtree <= threads.keys(), 'Incomplete descendant state')
        row = threads[self.thread]
        path = Path(row['rollout_path'])
        no_symlinks(path)
        require(path.suffix == '.jsonl' and path.is_file(), 'Only uncompressed JSONL targets supported')
        expected = self.home / ('archived_sessions' if row['archived'] else 'sessions')
        require(path.is_relative_to(expected), 'Rollout outside expected session directory')
        require(path.stat().st_uid == os.getuid(), 'Rollout ownership mismatch')
        return path, {i: threads[i]['archived'] for i in sorted(subtree)}

    def inspect(self):
        path, states = self.state()
        data = path.read_bytes()
        new, report, _ = transform(data, self.thread, self.root / 'images')
        require(path.read_bytes() == data, 'Session changed during inspection')
        report.update(thread=self.thread, source=str(path), archive_states=states)
        return report

    def record(self, filename):
        p = self.root / filename
        no_symlinks(p)
        result = json.loads(p.read_bytes())
        require(result.get('thread') == self.thread and result.get('codex_home') == str(self.home),
                'Repair manifest identity mismatch')
        return result

    def private_dir(self):
        no_symlinks(self.root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(self.root.stat().st_uid == os.getuid() and self.root.stat().st_mode & 0o077 == 0,
                'Repair directory must be owned by current user with mode 0700')
        for name in ('images', 'backups'):
            p = self.root / name
            no_symlinks(p)
            p.mkdir(mode=0o700, exist_ok=True)
            require(p.stat().st_uid == os.getuid() and p.stat().st_mode & 0o077 == 0,
                    'Private data subdirectory requires mode 0700')

    def backup(self, data):
        path = self.root / 'backups' / (sha(data) + '.jsonl')
        no_symlinks(path)
        if not path.exists():
            atomic(path, data)
        require(path.read_bytes() == data, 'Backup verification failed')
        return str(path)

    def prepare(self):
        require(not (self.root / 'plan.json').exists(), 'Existing plan; use its directory or choose a new repair directory')
        require(not (self.root / 'applied.json').exists(), 'Existing repair; inspect it first')
        path, states = self.state()
        data = path.read_bytes()
        _, plan, assets = transform(data, self.thread, self.root / 'images')
        self.private_dir()
        plan['backup'] = self.backup(data)
        for name, raw in assets.items():
            dest = self.root / 'images' / name
            no_symlinks(dest)
            if not dest.exists():
                atomic(dest, raw)
            require(dest.read_bytes() == raw, 'Image export verification failed')
        require(self.state() == (path, states) and path.read_bytes() == data, 'State changed during prepare')
        plan.update(thread=self.thread, codex_home=str(self.home), source=str(path), archive_states=states)
        save(self.root / 'plan.json', plan)
        return plan

    def no_open_handles(self, path):
        check = subprocess.run(['/usr/sbin/lsof', '-t', '--', str(path)], capture_output=True, text=True)
        require(check.returncode == 1 and not check.stdout.strip() and not check.stderr.strip(),
                'Open rollout handles or inconclusive lsof; stop without writing')

    def released(self, path, plan):
        require(sys.platform == 'darwin', 'Mutation workflow tested only on macOS')
        actual, states = self.state()
        require(actual == path and set(states) == set(plan['archive_states']), 'Thread path/subtree changed')
        require(all(v == 1 for v in states.values()), 'Archive the target subtree with the first-party app tool first')
        self.no_open_handles(path)

    def retain_inode(self, path, expected_inode):
        # A plain copied backup misses appends in the final check/replace window.
        # Pin the old inode as a second, explicitly mutable recovery artifact.
        dest = self.root / 'backups' / ('live-inode-' + str(uuid.uuid4()) + '.jsonl')
        no_symlinks(dest)
        os.link(path, dest)  # Cross-volume DATA stops here, before replacement.
        require(inode(dest) == expected_inode and inode(path) == expected_inode,
                'Rollout inode changed before recovery link creation')
        fd = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return str(dest)

    def recovery_path(self, result):
        path = Path(result['recovery_inode'])
        require(path.parent == self.root / 'backups' and
                re.fullmatch(r'live-inode-[0-9a-f-]{36}\.jsonl', path.name), 'Unexpected recovery inode path')
        no_symlinks(path)
        return path

    def quiet(self, path, plan):
        for i in range(3):
            self.released(path, plan)
            if i < 2:
                time.sleep(1)

    def verify_assets(self, plan):
        for item in plan['images']:
            name = item['file']
            require(re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|gif|webp)', name), 'Invalid asset filename')
            path = self.root / 'images' / name
            no_symlinks(path)
            require(sha(path.read_bytes()) == item['sha256'], 'Image asset hash mismatch')

    def apply(self):
        require(not (self.root / 'applied.json').exists(), 'Existing apply journal; verify or roll back before retry')
        plan = self.record('plan.json')
        path, _ = self.state()
        require(path.name == Path(plan['source']).name, 'Rollout filename changed')
        self.quiet(path, plan)
        data, identity = snapshot(path)
        prepared = Path(plan['backup'])
        require(prepared == self.root / 'backups' / (plan['sha256_before'] + '.jsonl'), 'Unexpected backup path')
        no_symlinks(prepared)
        before = prepared.read_bytes()
        require(sha(before) == plan['sha256_before'], 'Prepared backup hash mismatch')
        require(data.startswith(before), 'Existing records changed after prepare; re-diagnose')
        new, result, _ = transform(data, self.thread, self.root / 'images')
        require(result['checkpoint_sha256'] == plan['checkpoint_sha256'] and
                result['images'] == plan['images'], 'Checkpoint changed after prepare')
        self.verify_assets(plan)
        result.update(thread=self.thread, codex_home=str(self.home), source=str(path),
                      backup=self.backup(data), archive_states=plan['archive_states'], state='pending')
        result['recovery_inode'] = self.retain_inode(path, identity)
        # Durable journal BEFORE replacement makes recovery possible after process interruption.
        save(self.root / 'applied.json', result)
        self.released(path, plan)
        require(inode(path) == identity and path.read_bytes() == data, 'Concurrent rollout change detected')
        atomic(path, new, path.stat().st_mode & 0o777)
        require(path.read_bytes() == new, 'Post-write verification failed; keep archived for inspection')
        recovery = self.recovery_path(result)
        self.no_open_handles(recovery)
        require(recovery.read_bytes() == data, 'Concurrent changes retained in recovery inode; keep archived for review')
        result['state'] = 'applied_verified'
        save(self.root / 'applied.json', result)
        return result

    def verify(self):
        result = self.record('applied.json')
        path, states = self.state()
        data = path.read_bytes()
        # Same-length repair permits appended turns, but verification never drops them.
        backup = Path(result['backup'])
        require(backup == self.root / 'backups' / (result['sha256_before'] + '.jsonl'), 'Unexpected backup path')
        no_symlinks(backup)
        original = backup.read_bytes()
        require(sha(original) == result['sha256_before'], 'Backup hash mismatch')
        require(sha(data[:len(original)]) == result['sha256_after'], 'Repaired prefix mismatch')
        checkpoint(data, self.thread)  # Also validate complete appended JSONL records.
        require(sha(self.recovery_path(result).read_bytes()) == result['sha256_before'],
                'Recovery inode changed; preserve both versions and review')
        self.verify_assets(result)
        require(path.read_bytes() == data, 'Session changed during verification; retry when idle')
        return dict(state='disk_verified', archive_states_restored=states == result['archive_states'],
                    appended_bytes=len(data) - len(original), live_response_test='not_performed_by_script')

    def rollback(self):
        result = self.record('applied.json')
        path, _ = self.state()
        self.quiet(path, result)
        data, identity = snapshot(path)
        require(sha(data) == result['sha256_after'], 'Session changed after repair; preserve newer turns and stop')
        backup = Path(result['backup'])
        require(backup == self.root / 'backups' / (result['sha256_before'] + '.jsonl'), 'Unexpected backup path')
        no_symlinks(backup)
        original = backup.read_bytes()
        require(sha(original) == result['sha256_before'], 'Backup hash mismatch')
        require(sha(self.recovery_path(result).read_bytes()) == result['sha256_before'],
                'Recovery inode changed; preserve both versions and review')
        self.released(path, result)
        require(path.read_bytes() == data, 'Concurrent rollout change detected')
        result['rollback_recovery_inode'] = self.retain_inode(path, identity)
        result['state'] = 'rollback_pending'
        save(self.root / 'applied.json', result)
        self.released(path, result)
        require(inode(path) == identity and path.read_bytes() == data, 'Concurrent rollout change detected')
        atomic(path, original, path.stat().st_mode & 0o777)
        require(path.read_bytes() == original, 'Rollback verification failed')
        rollback_recovery = Path(result['rollback_recovery_inode'])
        self.no_open_handles(rollback_recovery)
        require(rollback_recovery.read_bytes() == data, 'Concurrent changes retained in rollback recovery inode; keep archived')
        result['state'] = 'rolled_back_verified'
        save(self.root / 'applied.json', result)
        return {'state': result['state']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inspect', 'prepare', 'apply', 'verify', 'rollback'])
    parser.add_argument('--thread', required=True)
    parser.add_argument('--current-thread', required=True)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    repair = Repair(args.codex_home, args.thread, args.current_thread, args.data_dir)
    if args.mode in ('inspect', 'verify'):
        report = getattr(repair, args.mode)()
    else:
        repair.private_dir()
        lockpath = repair.root / '.repair.lock'
        no_symlinks(lockpath)
        with lockpath.open('a') as lock:
            os.chmod(lockpath, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = getattr(repair, args.mode)()
    # Never print transcript, base64 images, or opaque summaries.
    print(json.dumps({k: v for k, v in report.items() if k != 'images'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(2)
