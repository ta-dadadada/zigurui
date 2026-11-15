#!/usr/bin/env python3
"""匿名一時文書でv3差分更新、v2互換、失敗時保持を検証する。"""
import hashlib
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import unittest

BINARY = str(Path(sys.argv.pop(1) if len(sys.argv) > 1 else 'zig-out/bin/zigurui').resolve())


def legacy_index(documents):
    integer = lambda n: struct.pack('<Q', n)
    string = lambda data: integer(len(data)) + data
    grams = {}
    for i, (_, text) in enumerate(documents):
        for gram in set(text[j:j + 2] for j in range(len(text) - 1)):
            grams.setdefault(gram.encode(), []).append(i)
    payload = integer(len(documents)) + integer(len(grams)) + struct.pack('<III', 0, 0, 0)
    for path, text in documents:
        payload += string(str(path).encode()) + string(text.encode())
    for gram, ids in sorted(grams.items()):
        payload += string(gram) + integer(len(ids)) + b''.join(integer(i) for i in ids)
    checksum = 14695981039346656037
    for byte in payload:
        checksum = ((checksum ^ byte) * 1099511628211) % (1 << 64)
    return b'ZIGURUI\0' + struct.pack('<IQQ', 2, len(payload), checksum) + payload


class IncrementalCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zigurui-incremental-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.docs = self.base / 'documents'
        self.docs.mkdir()
        self.index = self.base / 'index.idx'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('XDG_')}
        self.env['HOME'] = str(self.base / 'home')
        self.env['XDG_CONFIG_HOME'] = str(self.base / 'config')
        (self.docs / 'b.md').write_text('# 古い見出し\nｶﾞﾗｽと猫\n')
        (self.docs / 'c.md').write_text('# 記録\n断熱と窓\n')
        (self.docs / 'd.md').write_text('消える記録\n')

    def command(self, *args, success=True, preexec_fn=None, env=None):
        result = subprocess.run([BINARY, *map(str, args)], env=env or self.env,
                                capture_output=True, preexec_fn=preexec_fn)
        self.assertEqual(result.returncode == 0, success, result.stderr.decode(errors='replace'))
        return result

    def test_incremental_equals_rebuild_in_both_modes(self):
        for mode in ('literal', 'normalized'):
            with self.subTest(mode=mode):
                self.command('index', '--mode', mode, self.docs, self.index)
                self.assertEqual(struct.unpack('<I', self.index.read_bytes()[8:12])[0], 3)
                old = self.index.read_bytes()
                os.utime(self.index, ns=(1_600_000_000_000_000_123, 1_600_000_000_000_000_123))
                previous_stat = self.index.stat()
                result = self.command('index', '--mode', mode, '--incremental', self.docs, self.index)
                self.assertIn(b'0 added, 0 modified, 0 deleted, 3 unchanged', result.stdout)
                self.assertEqual(self.index.read_bytes(), old)
                self.assertEqual(self.index.stat().st_mtime_ns, previous_stat.st_mtime_ns)
                self.assertEqual(self.index.stat().st_ino, previous_stat.st_ino)
                self.assertEqual(result.stdout, b'3 documents indexed (0 added, 0 modified, 0 deleted, 3 unchanged)\n')
                (self.docs / 'a.md').write_text('追加した猫と断熱\n')
                (self.docs / 'c.md').write_text('新しいガラスと断熱\n')
                (self.docs / 'd.md').rename(self.docs / 'd.removed')
                result = self.command('index', '--mode', mode, '--incremental', self.docs, self.index)
                self.assertIn(b'1 added, 1 modified, 1 deleted, 1 unchanged', result.stdout)
                rebuilt = self.base / 'rebuilt.idx'
                self.command('index', '--mode', mode, self.docs, rebuilt)
                self.assertEqual(self.index.read_bytes(), rebuilt.read_bytes())
                for query in ('猫', 'ガラス', 'ｶﾞﾗｽ', '断熱', '消える', '不存在'):
                    self.assertEqual(self.command('search', '--mode', mode, self.index, query).stdout,
                                     self.command('scan', '--mode', mode, self.docs, query).stdout)
                cat = self.command('search', '--mode', mode, self.index, '猫').stdout.decode()
                self.assertEqual([line.split(':', 1)[0] for line in cat.splitlines()],
                                 [str(self.docs / 'a.md'), str(self.docs / 'b.md')])
                all_args = ('--mode', mode, '--all')
                self.assertEqual(self.command('search', *all_args, self.index, 'ガラス', '断熱').stdout,
                                 self.command('scan', *all_args, self.docs, 'ガラス', '断熱').stdout)
                # 次のモードの基準状態へ戻す。生成した文書だけを使う。
                (self.docs / 'a.md').rename(self.docs / f'a-{mode}.removed')
                (self.docs / 'c.md').write_text('# 記録\n断熱と窓\n')
                (self.docs / 'd.removed').rename(self.docs / 'd.md')

    def test_fail_closed_and_no_root_in_diagnostic(self):
        self.command('index', self.docs, self.index)
        original = self.index.read_bytes()
        other = self.base / 'other'
        other.mkdir()
        result = self.command('index', '--incremental', other, self.index, success=False)
        self.assertIn(b'SourceRootMismatch', result.stderr)
        self.assertNotIn(str(self.base).encode(), result.stderr + result.stdout)
        self.assertEqual(self.index.read_bytes(), original)
        self.command('index', '--mode', 'normalized', '--incremental', self.docs, self.index, success=False)
        self.assertEqual(self.index.read_bytes(), original)
        missing = self.base / 'missing.idx'
        self.command('index', '--incremental', self.docs, missing, success=False)
        self.assertFalse(missing.exists())
        corrupted = original[:-1] + bytes([original[-1] ^ 1])
        self.index.write_bytes(corrupted)
        self.command('index', '--incremental', self.docs, self.index, success=False)
        self.assertEqual(self.index.read_bytes(), corrupted)

    def test_v2_read_compatibility_and_explicit_rebuild_required(self):
        documents = [(p, p.read_text()) for p in sorted(self.docs.glob('*.md'))]
        legacy = legacy_index(documents)
        self.index.write_bytes(legacy)
        self.assertIn(b'b.md', self.command('search', self.index, '猫').stdout)
        self.command('schema', self.index)
        result = self.command('index', '--incremental', self.docs, self.index, success=False)
        self.assertIn(b'IncrementalNeedsRebuild', result.stderr)
        self.assertEqual(self.index.read_bytes(), legacy)
        self.command('index', self.docs, self.index)
        self.command('index', '--incremental', self.docs, self.index)

    def test_canonical_alias_default_config_and_offline_search(self):
        config_dir = Path(self.env['XDG_CONFIG_HOME']) / 'zigurui'
        config_dir.mkdir(parents=True)
        import json
        (config_dir / 'config.json').write_text(json.dumps({'mode': 'normalized', 'index_path': str(self.index)}))
        self.command('index', self.docs)
        alias = self.base / 'alias'
        alias.symlink_to(self.docs, target_is_directory=True)
        self.command('index', '--incremental', alias)
        self.docs.rename(self.base / 'unavailable')
        self.assertIn(b'b.md', self.command('search', 'ガラス').stdout)
        self.command('schema', self.index)

    @unittest.skipUnless(os.name == 'posix', 'POSIX resource limit required')
    def test_failed_write_preserves_old_index(self):
        import resource
        self.command('index', self.docs, self.index)
        original = self.index.read_bytes()
        (self.docs / 'b.md').write_text('変更したガラス\n' * 10000)
        def limited():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64))
        self.command('index', '--incremental', self.docs, self.index, success=False, preexec_fn=limited)
        self.assertEqual(hashlib.sha256(self.index.read_bytes()).digest(), hashlib.sha256(original).digest())
        self.assertIn(b'b.md', self.command('search', self.index, '猫').stdout)

    def test_empty_noop_preserves_bytes_and_mtime(self):
        empty = self.base / 'empty'
        empty.mkdir()
        self.command('index', empty, self.index)
        original = self.index.read_bytes()
        os.utime(self.index, ns=(1_600_000_000_000_000_123, 1_600_000_000_000_000_123))
        before = self.index.stat()
        result = self.command('index', '--incremental', empty, self.index)
        self.assertEqual(result.stdout, b'0 documents indexed (0 added, 0 modified, 0 deleted, 0 unchanged)\n')
        self.assertEqual(self.index.read_bytes(), original)
        self.assertEqual(self.index.stat().st_mtime_ns, before.st_mtime_ns)

    def test_same_size_and_mtime_changed_content_is_updated(self):
        self.command('index', self.docs, self.index)
        source = self.docs / 'b.md'
        before = source.stat()
        source.write_text(source.read_text().replace('猫', '犬'))
        self.assertEqual(source.stat().st_size, before.st_size)
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        result = self.command('index', '--incremental', self.docs, self.index)
        self.assertIn(b'0 added, 1 modified, 0 deleted, 2 unchanged', result.stdout)
        self.assertEqual(self.command('search', self.index, '猫').stdout, b'')
        self.assertIn(b'b.md', self.command('search', self.index, '犬').stdout)

    def test_valid_checksum_but_invalid_varint_is_not_skipped(self):
        self.command('index', self.docs, self.index)
        data = bytearray(self.index.read_bytes())
        data[-1] = 128  # 最後のIDを途中で切れたvarintにし、checksumだけは正しくする。
        checksum = 14695981039346656037
        for byte in data[28:]:
            checksum = ((checksum ^ byte) * 1099511628211) % (1 << 64)
        data[20:28] = struct.pack('<Q', checksum)
        self.index.write_bytes(data)
        before = self.index.stat()
        result = self.command('index', '--incremental', self.docs, self.index, success=False)
        self.assertIn(b'InvalidIndex', result.stderr)
        self.assertEqual(self.index.read_bytes(), data)
        self.assertEqual(self.index.stat().st_mtime_ns, before.st_mtime_ns)

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions and pipe required')
    def test_noop_readonly_destination_and_output_failure_preserve_index(self):
        self.command('index', self.docs, self.index)
        original = self.index.read_bytes()
        os.utime(self.index, ns=(1_600_000_000_000_000_123, 1_600_000_000_000_000_123))
        before = self.index.stat()
        self.index.chmod(0o400)
        self.base.chmod(0o500)
        try:
            self.command('index', '--incremental', self.docs, self.index)
            read_fd, write_fd = os.pipe()
            os.close(read_fd)
            with os.fdopen(write_fd, 'wb') as output:
                result = subprocess.run([BINARY, 'index', '--incremental', str(self.docs), str(self.index)],
                                        stdout=output, stderr=subprocess.PIPE, env=self.env,
                                        preexec_fn=lambda: signal.signal(signal.SIGPIPE, signal.SIG_IGN))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.index.read_bytes(), original)
            self.assertEqual(self.index.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(self.index.stat().st_ino, before.st_ino)
        finally:
            self.base.chmod(0o700)
            self.index.chmod(0o600)

    def test_unreadable_source_preserves_old_index(self):
        self.command('index', self.docs, self.index)
        original = self.index.read_bytes()
        source = self.docs / 'broken.md'
        source.write_bytes(b'\xff')
        self.command('index', '--incremental', self.docs, self.index, success=False)
        self.assertEqual(self.index.read_bytes(), original)

    def test_heading_after_incremental_preserves_original_lines(self):
        header = '---\ntags: [house]\n---\n'
        for mode in ('literal', 'normalized'):
            with self.subTest(mode=mode):
                (self.docs / 'b.md').write_text(header + 'ガラス 断熱\n# 日誌\n')
                (self.docs / 'c.md').write_text(header + '# ｶﾞﾗｽ ガラス\n断熱\n')
                (self.docs / 'd.md').write_text(header + '対象外\n')
                self.command('index', '--mode', mode, self.docs, self.index)
                opts = ('--mode', mode, '--all', '--frontmatter', '--where', '/tags', '"house"')
                before = self.command('search', *opts, '--rank', 'heading', self.index, 'ガラス', '断熱').stdout
                self.assertEqual(before, (f'{self.docs / "c.md"}:4: # ｶﾞﾗｽ ガラス\n'
                                          f'{self.docs / "b.md"}:4: ガラス 断熱\n').encode())
                # 追加と削除で文書IDが変わり、変更した文書の原文行も移動する。
                (self.docs / 'a.md').write_text(header + '# ガラス\n断熱\n')
                (self.docs / 'c.md').write_text(header + '前置き\n# 新しいガラス\n断熱\n')
                (self.docs / 'd.md').rename(self.docs / f'd-{mode}.removed')
                updated = self.command('index', '--mode', mode, '--incremental', self.docs, self.index)
                self.assertIn(b'1 added, 1 modified, 1 deleted, 1 unchanged', updated.stdout)
                expected = (f'{self.docs / "a.md"}:4: # ガラス\n'
                            f'{self.docs / "b.md"}:4: ガラス 断熱\n'
                            f'{self.docs / "c.md"}:5: # 新しいガラス\n').encode()
                for rank in ('path', 'heading'):
                    found = self.command('search', *opts, '--rank', rank, self.index, 'ガラス', '断熱').stdout
                    rows = expected.splitlines(keepends=True)
                    self.assertEqual(found, expected if rank == 'path' else rows[0] + rows[2] + rows[1])
                    self.assertEqual(found, self.command('scan', *opts, '--rank', rank, self.docs, 'ガラス', '断熱').stdout)
                filtered = self.command('search', *opts, '--rank', 'heading', '--where', 'file:/name', '"c.md"',
                                        self.index, 'ガラス', '断熱').stdout
                self.assertEqual(filtered, f'{self.docs / "c.md"}:5: # 新しいガラス\n'.encode())
                rebuilt = self.base / f'rebuilt-{mode}.idx'
                self.command('index', '--mode', mode, self.docs, rebuilt)
                self.assertEqual(self.index.read_bytes(), rebuilt.read_bytes())
                old = self.index.read_bytes(), self.index.stat().st_mtime_ns, self.index.stat().st_ino
                self.command('index', '--mode', mode, '--incremental', self.docs, self.index)
                self.assertEqual((self.index.read_bytes(), self.index.stat().st_mtime_ns, self.index.stat().st_ino), old)
                (self.docs / 'a.md').rename(self.docs / f'a-{mode}.removed')

    def test_rank_and_incremental_flags_reject_wrong_commands(self):
        self.command('index', self.docs, self.index)
        before = self.index.read_bytes(), self.index.stat().st_mtime_ns, self.index.stat().st_ino
        invalid = [
            ('index', '--incremental', '--rank', 'heading', self.docs, self.index),
            ('index', '--rank', 'path', '--incremental', self.docs, self.index),
            ('index', '--rank', 'heading', self.docs),
            ('search', '--incremental', self.index, 'ガラス'),
            ('scan', '--incremental', self.docs, 'ガラス'),
            ('search', '--rank', self.index),
            ('search', '--rank', 'heading', '--rank', 'path', self.index, 'ガラス'),
            ('search', '--rank', 'invalid', self.index, 'ガラス'),
        ]
        for argv in invalid:
            with self.subTest(argv=argv):
                self.assertIn(b'InvalidArguments', self.command(*argv, success=False).stderr)
                self.assertEqual((self.index.read_bytes(), self.index.stat().st_mtime_ns, self.index.stat().st_ino), before)
        # 入力パス後はオプションではなく検索語として扱う。
        for term in ('--rank', '--incremental'):
            self.assertEqual(self.command('search', '--rank', 'heading', self.index, term).stdout, b'')

    def test_legacy_heading_uses_saved_original_without_root(self):
        documents = [(self.docs / 'a.md', 'ガラス\n'), (self.docs / 'z.md', '# ガラス\n')]
        self.index.write_bytes(legacy_index(documents))
        before = self.index.read_bytes()
        self.assertEqual(self.command('search', '--rank', 'heading', self.index, 'ガラス').stdout,
                         (f'{self.docs / "z.md"}:1: # ガラス\n{self.docs / "a.md"}:1: ガラス\n').encode())
        self.command('schema', self.index)
        self.command('index', '--incremental', self.docs, self.index, success=False)
        self.assertEqual(self.index.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
