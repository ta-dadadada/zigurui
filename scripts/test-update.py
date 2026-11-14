#!/usr/bin/env python3
"""実行中の隔離バイナリを、既存インストーラの保護契約で更新する結合テスト。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

BINARY = Path(sys.argv.pop(1) if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else 'zig-out/bin/zigurui').resolve()
SPEC = importlib.util.spec_from_file_location('installer_tests', Path(__file__).with_name('test-install.py'))
installer_tests = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer_tests)

INITIAL = 'v0.1.0-alpha.4'
NEW = 'v0.1.0-alpha.5'


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.installer = installer_tests.InstallTest()
        self.installer.setUp()
        self.addCleanup(self.installer.doCleanups)
        self.root = self.installer.root
        self.home = self.installer.home
        self.bin = self.installer.bin
        self.target = 'macos-arm64' if sys.platform == 'darwin' else 'linux-x86_64'
        self.env = self.installer.env
        self.env.update(TEST_OS='Darwin' if sys.platform == 'darwin' else 'Linux',
                        TEST_CPU='arm64' if sys.platform == 'darwin' else 'x86_64')
        for key, suffix in {'CONFIG': '.config', 'DATA': '.local/share', 'CACHE': '.cache', 'STATE': '.local/state'}.items():
            self.env[f'XDG_{key}_HOME'] = str(self.home / suffix)
        self.fetch_log = self.root / 'fetch.log'
        self.env['UPDATE_FETCH_LOG'] = str(self.fetch_log)
        curl = (self.installer.commands / 'curl').read_text()
        curl = curl.replace('[ "${FAIL_FETCH:-}"', 'printf "fetch\\n" >> "$UPDATE_FETCH_LOG"\n[ "${FAIL_FETCH:-}"', 1)
        self.installer.stub('curl', curl)

    def fixture(self, tag=INITIAL, invalid=None, real=False):
        return self.installer.fixture(tag=tag, target=self.target, invalid=invalid,
                                      executable=BINARY.read_bytes() if real else None)

    def install(self, custom=None):
        self.fixture(real=True)
        args = ('--bin-dir', str(custom)) if custom else ()
        self.installer.run_install(*args, tag=INITIAL)
        return (custom or self.bin) / 'zigurui'

    def update(self, binary, *args, success=True, env=None):
        result = subprocess.run([str(binary), 'update', *args], env=env or self.env, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
        return result

    def fetches(self):
        return self.fetch_log.read_bytes() if self.fetch_log.exists() else b''

    def test_running_copy_is_replaced_and_config_index_sources_licenses_survive(self):
        binary = self.install()
        documents = self.home / 'documents'
        documents.mkdir()
        source = documents / 'note.md'
        source.write_text('ＡＢＣ ガラス 原本を保持', encoding='utf-8')
        index = self.home / '.local/share/zigurui/saved.idx'
        index.parent.mkdir(parents=True)
        subprocess.run([str(binary), 'index', '--mode', 'normalized', str(documents), str(index)],
                       env=self.env, check=True, capture_output=True)
        config = self.home / '.config/zigurui/config.json'
        config.parent.mkdir(parents=True)
        config.write_text('{ invalid config')
        profile = self.home / '.profile'
        profile.write_text('# keep shell settings')
        protected = {p: p.read_bytes() for p in [source, index, config, profile]}
        licenses = {p: p.read_bytes() for p in (self.bin / '.zigurui-install/licenses').rglob('*') if p.is_file()}
        old = binary.read_bytes()
        new = self.fixture(tag=NEW)
        result = self.update(binary, '--version', NEW, '--channel', 'prerelease')
        self.assertIn(NEW.encode(), result.stdout)
        self.assertEqual(binary.read_bytes(), new)
        self.assertEqual(subprocess.check_output([str(binary), '--version']), f'zigurui {NEW}\n'.encode())
        for path, contents in {**protected, **licenses}.items():
            self.assertEqual(path.read_bytes(), contents)
        for data in [old, new]:
            receipt = self.bin / '.zigurui-install/receipts' / hashlib.sha256(data).hexdigest()
            self.assertEqual(receipt.read_text().splitlines()[0], hashlib.sha256(data).hexdigest())
        self.assertEqual(len(list((self.bin / '.zigurui-install/licenses').glob('*/LICENSE-libyaml.txt'))), 2)

    def test_same_version_repeats_and_explicit_downgrade_is_allowed(self):
        binary = self.install()
        before = binary.read_bytes()
        self.update(binary, '--version', INITIAL, '--channel', 'prerelease')
        self.update(binary, '--channel', 'prerelease', '--version', INITIAL)
        self.assertEqual(binary.read_bytes(), before)
        old_tag = 'v0.1.0-alpha.3'
        older = self.fixture(tag=old_tag)
        self.update(binary, '--version', old_tag, '--channel', 'prerelease')
        self.assertEqual(binary.read_bytes(), older)

    def test_custom_install_location_is_used_without_home(self):
        custom = self.home / 'custom bin'
        binary = self.install(custom=custom)
        replacement = self.fixture(tag='v1.0.0')
        env = {k: v for k, v in self.env.items() if k != 'HOME' and not k.startswith('XDG_')}
        self.update(binary, '--version', 'v1.0.0', env=env)
        self.assertEqual(binary.read_bytes(), replacement)
        self.assertFalse((self.bin / 'zigurui').exists())

    def test_update_arguments_and_channel_validation_do_not_fetch_or_replace(self):
        binary = self.install()
        before, fetches = binary.read_bytes(), self.fetches()
        for args in ((), ('--version',), ('--version', NEW), ('--version', 'v1.0.0', '--channel', 'prerelease'),
                     ('--version', 'other', '--channel', 'prerelease'), ('--version', NEW, '--channel', 'other'),
                     ('--bin-dir', str(self.root / 'forbidden')), ('--version', NEW, '--update'),
                     ('--version', '$(touch should-not-exist)', '--channel', 'prerelease')):
            with self.subTest(args=args):
                self.update(binary, *args, success=False)
                self.assertEqual(binary.read_bytes(), before)
                self.assertEqual(self.fetches(), fetches)
        self.assertFalse((self.root / 'forbidden').exists())
        self.assertFalse((Path.cwd() / 'should-not-exist').exists())

    def test_unmanaged_renamed_and_mismatched_receipts_are_refused(self):
        unmanaged = self.home / 'mise/installs/zigurui'
        unmanaged.parent.mkdir(parents=True)
        unmanaged.write_bytes(BINARY.read_bytes())
        unmanaged.chmod(0o755)
        alias = self.home / 'alias'
        alias.symlink_to(unmanaged)
        before, fetches = unmanaged.read_bytes(), self.fetches()
        for path in (unmanaged, alias):
            self.update(path, '--version', NEW, '--channel', 'prerelease', success=False)
            self.assertEqual(unmanaged.read_bytes(), before)
            self.assertEqual(self.fetches(), fetches)
        binary = self.install()
        before, fetches = binary.read_bytes(), self.fetches()
        receipt = self.bin / '.zigurui-install/receipts' / hashlib.sha256(before).hexdigest()
        receipt.write_text('0' * 64 + '\n' + INITIAL + '\n')
        self.update(binary, '--version', NEW, '--channel', 'prerelease', success=False)
        self.assertEqual(binary.read_bytes(), before)
        self.assertEqual(self.fetches(), fetches)
        renamed = binary.with_name('renamed')
        binary.rename(renamed)
        self.update(renamed, '--version', NEW, '--channel', 'prerelease', success=False)
        self.assertEqual(renamed.read_bytes(), before)
        self.assertFalse(binary.exists())
        self.assertEqual(self.fetches(), fetches)

    def test_fetch_checksum_and_archive_failures_keep_the_running_binary(self):
        binary = self.install()
        before = binary.read_bytes()
        self.fixture(tag=NEW)
        self.update(binary, '--version', NEW, '--channel', 'prerelease', env={**self.env, 'FAIL_FETCH': '1'}, success=False)
        self.assertEqual(binary.read_bytes(), before)
        for invalid in ('checksum', 'truncated', 'missing_yaml', 'symlink', 'traversal'):
            with self.subTest(invalid=invalid):
                self.fixture(tag=NEW, invalid=invalid)
                self.update(binary, '--version', NEW, '--channel', 'prerelease', success=False)
                self.assertEqual(binary.read_bytes(), before)
                self.assertFalse((self.bin / '.zigurui-install.lock').exists())

    def test_symlink_to_managed_binary_updates_its_real_location(self):
        binary = self.install()
        alias = self.home / 'entrypoint'
        alias.symlink_to(binary)
        replacement = self.fixture(tag=NEW)
        self.update(alias, '--version', NEW, '--channel', 'prerelease')
        self.assertTrue(alias.is_symlink())
        self.assertEqual(binary.read_bytes(), replacement)
        self.assertEqual(alias.read_bytes(), replacement)

    def test_installer_signal_failure_is_not_reported_as_success(self):
        binary = self.install()
        before = binary.read_bytes()
        self.fixture(tag=NEW)
        self.installer.stub('curl', '#!/bin/sh\nkill -KILL "$PPID"\nexit 1\n')
        result = self.update(binary, '--version', NEW, '--channel', 'prerelease', success=False)
        self.assertIn(b'UpdateFailed', result.stderr)
        self.assertEqual(binary.read_bytes(), before)

    def test_update_help_needs_neither_config_nor_managed_install(self):
        config = self.home / '.config/zigurui/config.json'
        config.parent.mkdir(parents=True)
        config.write_text('invalid')
        result = self.update(BINARY, '--help')
        self.assertIn(b'zigurui update --version', result.stdout)
        self.assertEqual(self.fetches(), b'')


if __name__ == '__main__':
    unittest.main()
