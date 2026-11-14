#!/usr/bin/env python3
"""隔離したHOMEと取得スタブで、インストーラの配置と失敗時の保護を確認する。"""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("install.sh").resolve()
TAG = "v0.1.0-alpha.1"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zigurui-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.home / ".local/bin"
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.commands}:{os.environ['PATH']}",
                    "FIXTURES": str(self.assets), "TEST_OS": "Darwin", "TEST_CPU": "arm64"}
        self.stub("uname", '#!/bin/sh\ncase "$1" in -s) echo "$TEST_OS";; -m) echo "$TEST_CPU";; esac\n')
        self.stub("curl", '''#!/bin/sh
[ "${FAIL_FETCH:-}" != 1 ] || exit 22
while [ "$#" -gt 0 ]; do
 case "$1" in
  https://*) url=$1; shift;;
  -o) out=$2; shift 2;;
  --proto|--proto-redir|--tlsv1.2) if [ "$1" = --tlsv1.2 ]; then shift; else shift 2; fi;;
  *) shift;;
 esac
done
cp "$FIXTURES/${url##*/}" "$out"
''')
        self.fixture()

    def stub(self, name, text):
        path = self.commands / name
        path.write_text(text)
        path.chmod(0o755)

    def fixture(self, tag=TAG, target="macos-arm64", invalid=None):
        stem = f"zigurui-{tag}-{target}"
        asset = self.assets / f"{stem}.tar.gz"
        binary = f'#!/bin/sh\nprintf "zigurui {tag}\\n"\n'.encode()
        with tarfile.open(asset, "w:gz", format=tarfile.USTAR_FORMAT) as tar:
            payload = {"zigurui": binary, "README.md": b"readme", "LICENSE-utf8proc.md": b"license", "LICENSE-libyaml.txt": b"yaml license"}
            if invalid == "missing":
                del payload["LICENSE-utf8proc.md"]
            if invalid == "missing_yaml":
                del payload["LICENSE-libyaml.txt"]
            for name, data in payload.items():
                item = tarfile.TarInfo(f"{stem}/{name}")
                item.size = len(data)
                item.mode = 0o755 if name == "zigurui" else 0o644
                if invalid == "symlink" and name == "zigurui":
                    item.type = tarfile.SYMTYPE
                    item.linkname = "/etc/passwd"
                    item.size = 0
                tar.addfile(item, io.BytesIO(data))
            if invalid == "traversal":
                item = tarfile.TarInfo("../outside")
                tar.addfile(item, io.BytesIO(b""))
            if invalid == "duplicate":
                item = tarfile.TarInfo(f"{stem}/zigurui")
                tar.addfile(item, io.BytesIO(b""))
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        line = f"{digest}  {asset.name}\n"
        if invalid == "checksum":
            line = f"{'0' * 64}  {asset.name}\n"
        if invalid == "duplicate_checksum":
            line *= 2
        if invalid == "truncated":
            asset.write_bytes(b"broken gzip")
            line = f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}\n"
        (self.assets / "SHA256SUMS").write_text(line)
        return binary

    def run_install(self, *args, success=True, env=None, tag=TAG):
        result = subprocess.run(["sh", str(SCRIPT), "--version", tag, "--channel", "prerelease", *args],
                                env=env or self.env, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout.decode() + result.stderr.decode())
        return result

    def test_install_reinstall_upgrade_downgrade_and_data_preservation(self):
        keep = self.home / ".config/zigurui/settings"
        keep.parent.mkdir(parents=True)
        keep.write_bytes(b"user configuration")
        index = self.home / ".local/share/zigurui/default.idx"
        index.parent.mkdir(parents=True)
        index.write_bytes(b"user index")
        original = self.home / "original.md"
        original.write_bytes(b"source")
        for tag in (TAG, TAG, "v0.1.0-alpha.2", TAG):
            expected = self.fixture(tag)
            self.run_install(tag=tag)
            self.assertEqual((self.bin / "zigurui").read_bytes(), expected)
            self.assertEqual(subprocess.check_output([str(self.bin / "zigurui"), "--version"]), f"zigurui {tag}\n".encode())
        self.assertEqual(keep.read_bytes(), b"user configuration")
        self.assertEqual(index.read_bytes(), b"user index")
        self.assertEqual(original.read_bytes(), b"source")
        self.assertTrue(list((self.bin / ".zigurui-install/licenses").glob("*/LICENSE-utf8proc.md")))
        self.assertTrue(list((self.bin / ".zigurui-install/licenses").glob("*/LICENSE-libyaml.txt")))

    def test_backslash_in_bin_dir_or_home_does_not_change_hash(self):
        for source in ("bin-dir", "HOME"):
            with self.subTest(source=source):
                home = self.home / ("home\\directory" if source == "HOME" else "other-home")
                home.mkdir()
                env = {**self.env, "HOME": str(home)}
                destination = home / ".local/bin" if source == "HOME" else self.home / "custom\\bin"
                args = () if source == "HOME" else ("--bin-dir", str(destination))
                self.fixture()
                self.run_install(*args, env=env)
                binary = destination / "zigurui"
                expected = binary.read_bytes()
                receipt = destination / ".zigurui-install/receipts" / hashlib.sha256(expected).hexdigest()
                self.assertEqual(receipt.read_text().splitlines()[0], hashlib.sha256(expected).hexdigest())
                self.run_install(*args, env=env)
                self.fixture(invalid="checksum")
                self.run_install(*args, env=env, success=False)
                self.assertEqual(binary.read_bytes(), expected)

    def test_fetch_checksum_archive_failures_leave_existing_binary(self):
        self.run_install()
        before = (self.bin / "zigurui").read_bytes()
        receipt_path = self.bin / ".zigurui-install/receipts" / hashlib.sha256(before).hexdigest()
        receipt = receipt_path.read_bytes()
        self.run_install(success=False, env={**self.env, "FAIL_FETCH": "1"})
        for invalid in ("checksum", "duplicate_checksum", "missing", "missing_yaml", "truncated", "symlink", "traversal", "duplicate"):
            with self.subTest(invalid=invalid):
                self.fixture(invalid=invalid)
                self.run_install(success=False)
                self.assertEqual((self.bin / "zigurui").read_bytes(), before)
                self.assertEqual(receipt_path.read_bytes(), receipt)
                self.assertFalse((self.bin / ".zigurui-install.lock").exists())

    def test_unmanaged_modified_and_symlink_destinations_are_refused(self):
        self.bin.mkdir(parents=True)
        binary = self.bin / "zigurui"
        binary.write_bytes(b"other manager")
        self.run_install(success=False)
        self.assertEqual(binary.read_bytes(), b"other manager")
        binary.unlink()
        binary.symlink_to(self.home / "missing")
        self.run_install(success=False)
        binary.unlink()
        self.run_install()
        binary.write_bytes(b"modified by another manager")
        self.run_install(success=False)
        self.assertEqual(binary.read_bytes(), b"modified by another manager")

    def test_receipt_and_binary_commit_failures_allow_retry(self):
        self.run_install()
        before = (self.bin / "zigurui").read_bytes()
        new_tag = "v0.1.0-alpha.2"
        self.fixture(new_tag)
        self.stub("mv", '''#!/bin/sh
case "${FAIL_COMMIT:-}:$1:$2" in
 receipt:-f:*/receipt) exit 1;;
 binary:-f:*/zigurui) exit 1;;
esac
/bin/mv "$@"
''')
        for failure in ("receipt", "binary"):
            with self.subTest(failure=failure):
                self.run_install(tag=new_tag, success=False, env={**self.env, "FAIL_COMMIT": failure})
                self.assertEqual((self.bin / "zigurui").read_bytes(), before)
        # rename完了直後にシグナルが来ても、次回は新版の管理記録を参照できる。
        self.stub("mv", '''#!/bin/sh
/bin/mv "$@" || exit 1
case "$1:$2" in -f:*/zigurui) kill -TERM "$PPID";; esac
''')
        self.run_install(tag=new_tag, success=False)
        self.stub("mv", '#!/bin/sh\nexec /bin/mv "$@"\n')
        self.run_install(tag=new_tag)
        self.fixture(TAG)
        self.run_install()

    @unittest.skipIf(os.geteuid() == 0, "rootはディレクトリの書込制限を回避するため")
    def test_permission_failure_preserves_existing_binary(self):
        self.run_install()
        binary = self.bin / "zigurui"
        before = binary.read_bytes()
        self.bin.chmod(0o555)
        try:
            result = self.run_install(success=False)
            self.assertIn("書き込めない".encode(), result.stderr)
            self.assertEqual(binary.read_bytes(), before)
        finally:
            self.bin.chmod(0o755)

    def test_platform_channel_paths_and_write_failure(self):
        self.run_install(success=False, env={**self.env, "TEST_CPU": "riscv64"})
        self.assertFalse(self.bin.exists())
        for args in (("--channel", "stable"), ("--bin-dir", "relative"), ("--unknown",), ("--channel", "other")):
            self.run_install(*args, success=False)
            self.assertFalse(self.bin.exists())
        self.run_install("--bin-dir", str(self.home / "custom bin"))
        self.assertTrue((self.home / "custom bin/zigurui").is_file())
        blocked = self.home / "file"
        blocked.write_text("not a directory")
        self.run_install("--bin-dir", str(blocked / "bin"), success=False)
        self.assertEqual(blocked.read_text(), "not a directory")
        self.fixture(target="linux-x86_64")
        self.run_install(env={**self.env, "TEST_OS": "Linux", "TEST_CPU": "x86_64"})
        self.fixture(tag="v1.0.0", target="linux-x86_64")
        self.run_install("--channel", "stable", tag="v1.0.0", env={**self.env, "TEST_OS": "Linux", "TEST_CPU": "x86_64"})
        result = subprocess.run(["sh", str(SCRIPT)], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"--version", result.stderr)


if __name__ == "__main__":
    unittest.main()
