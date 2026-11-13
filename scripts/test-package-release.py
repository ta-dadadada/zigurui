"""配布タグ・限定archive・checksumと既存ファイル保護を検証する。"""

import hashlib
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

from package_release import TARGETS, aggregate, archive_stem, build_target, package, stage_target, target_matrix, validate_tag, verify


class ReleasePackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zigurui-package-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("# zigurui\n")
        (self.repo / "LICENSE").write_text("test license\n")
        (self.repo / "private.md").write_text("do not distribute\n")
        macos = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0)
        linux = bytearray(64 + 56)
        linux[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH", linux, 16, 2, 62)
        struct.pack_into("<Q", linux, 32, 64)
        struct.pack_into("<HH", linux, 54, 56, 1)
        struct.pack_into("<I", linux, 64, 1)
        self.binaries = {}
        for name, data in (("macos-arm64", macos), ("linux-x86_64", linux)):
            path = self.root / name
            path.write_bytes(data)
            self.binaries[name] = path
        self.tag = "v0.1.0-alpha.1"

    def test_only_prerelease_semver_is_accepted(self):
        for tag in (self.tag, "v1.2.3-beta.0", "v2.0.0-rc.1"):
            self.assertEqual(validate_tag(tag), tag)
        for tag in ("v1.0.0", "v01.0.0-alpha.1", "v1.0.0-alpha.01", "v1.0.0-preview.1", "v1.0.0-alpha.1+meta", "../evil", "v1.0.0-alpha.1\n", "$(touch x)", "-x", "v１.0.0-alpha.1"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_tag(tag)

    def test_archives_are_deterministic_and_contain_only_allowed_files(self):
        first = package(self.tag, self.binaries, self.root / "first", self.repo)
        second = package(self.tag, self.binaries, self.root / "second", self.repo)
        self.assertEqual({p.name: p.read_bytes() for p in first.iterdir()}, {p.name: p.read_bytes() for p in second.iterdir()})
        for target in TARGETS:
            stem = archive_stem(self.tag, target)
            archive = first / f"{stem}.tar.gz"
            with tarfile.open(archive) as tar:
                self.assertEqual(set(tar.getnames()), {f"{stem}/zigurui", f"{stem}/README.md", f"{stem}/LICENSE"})
                for member in tar.getmembers():
                    self.assertEqual((member.uid, member.gid, member.uname, member.gname, member.mtime), (0, 0, "", "", 0))
                self.assertEqual(tar.getmember(f"{stem}/zigurui").mode, 0o755)
                self.assertEqual(tar.extractfile(f"{stem}/zigurui").read(), self.binaries[target].read_bytes())
            self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(), (first / "SHA256SUMS").read_text())
        verify(self.tag, first)

    def test_wrong_architecture_dynamic_link_and_private_paths_are_rejected(self):
        macos = self.binaries["macos-arm64"]
        original = macos.read_bytes()
        for data in (b"not a binary", original + str(self.repo.resolve()).encode()):
            macos.write_bytes(data)
            with self.assertRaises(ValueError):
                package(self.tag, self.binaries, self.root / "rejected", self.repo)
            self.assertFalse((self.root / "rejected").exists())
        macos.write_bytes(original)
        linux = self.binaries["linux-x86_64"]
        data = bytearray(linux.read_bytes())
        struct.pack_into("<I", data, 64, 3)
        linux.write_bytes(data)
        with self.assertRaises(ValueError):
            package(self.tag, self.binaries, self.root / "dynamic", self.repo)

    def test_existing_output_is_not_overwritten_and_tampering_is_rejected(self):
        output = package(self.tag, self.binaries, self.root / "output", self.repo)
        original = {path.name: path.read_bytes() for path in output.iterdir()}
        with self.assertRaises(FileExistsError):
            package(self.tag, self.binaries, output, self.repo)
        self.assertEqual(original, {path.name: path.read_bytes() for path in output.iterdir()})
        (output / "private.txt").write_text("do not distribute")
        with self.assertRaises(ValueError):
            verify(self.tag, output)
        (output / "private.txt").unlink()
        archive = output / f"{archive_stem(self.tag, 'macos-arm64')}.tar.gz"
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            verify(self.tag, output)

    def make_inputs(self, name="inputs", targets=None):
        inputs = self.root / name
        inputs.mkdir()
        for target in (TARGETS if targets is None else targets):
            stage_target(self.tag, target, self.binaries[target], inputs / f"release-target-{target}", self.repo)
        return inputs

    def test_matrix_and_single_target_build_keep_target_definition_central(self):
        self.assertEqual([entry["target"] for entry in target_matrix()["include"]], list(TARGETS))
        artifacts = [entry["artifact"] for entry in target_matrix()["include"]]
        self.assertEqual(len(set(artifacts)), len(TARGETS))
        with mock.patch("package_release.compile_target", return_value=self.binaries["macos-arm64"]) as compile_one:
            output = build_target(self.tag, "macos-arm64", self.root / "single-target")
        compile_one.assert_called_once_with("macos-arm64")
        self.assertEqual({path.name for path in output.iterdir()}, {"zigurui", "target.json"})
        metadata = json.loads((output / "target.json").read_text())
        self.assertEqual(metadata["target"], "macos-arm64")
        self.assertEqual(set(metadata), {"schema_version", "tag", "target", "zig_target", "binary_sha256"})

    def test_successful_target_artifacts_aggregate_into_complete_release(self):
        inputs = self.make_inputs()
        output = aggregate(self.tag, inputs, self.root / "aggregated", self.repo)
        verify(self.tag, output)
        self.assertEqual(len(list(output.glob("*.tar.gz"))), len(TARGETS))
        self.assertEqual(len((output / "SHA256SUMS").read_text().splitlines()), len(TARGETS))
        with self.assertRaises(FileExistsError):
            aggregate(self.tag, inputs, output, self.repo)

    def test_missing_duplicate_and_unknown_targets_are_rejected_before_output(self):
        missing = self.make_inputs("missing", ["macos-arm64"])
        with self.assertRaisesRegex(ValueError, "all release targets"):
            aggregate(self.tag, missing, self.root / "missing-output", self.repo)
        self.assertFalse((self.root / "missing-output").exists())
        duplicate = self.make_inputs("duplicate")
        stage_target(self.tag, "macos-arm64", self.binaries["macos-arm64"], duplicate / "duplicate-target", self.repo)
        with self.assertRaisesRegex(ValueError, "duplicate target"):
            aggregate(self.tag, duplicate, self.root / "duplicate-output", self.repo)
        unknown = self.make_inputs("unknown")
        metadata_file = unknown / "release-target-macos-arm64" / "target.json"
        metadata = json.loads(metadata_file.read_text())
        metadata["target"] = "unknown-architecture"
        metadata_file.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "unsupported target"):
            aggregate(self.tag, unknown, self.root / "unknown-output", self.repo)

    def test_corrupt_unexpected_or_cross_runner_private_artifacts_are_rejected(self):
        for case in ("checksum", "extra-file", "extra-metadata", "wrong-tag", "wrong-triple", "symlink", "cross-runner-home", "debug-info"):
            with self.subTest(case=case):
                inputs = self.make_inputs(case)
                artifact = inputs / "release-target-macos-arm64"
                binary = artifact / "zigurui"
                metadata_file = artifact / "target.json"
                metadata = json.loads(metadata_file.read_text())
                if case == "checksum":
                    binary.write_bytes(binary.read_bytes() + b"changed")
                elif case == "extra-file":
                    (artifact / "private.txt").write_text("private")
                elif case == "extra-metadata":
                    metadata["producer_home"] = "/home/another-producer"
                elif case == "wrong-tag":
                    metadata["tag"] = "v9.9.9-alpha.1"
                elif case == "wrong-triple":
                    metadata["zig_target"] = "x86_64-linux-musl"
                elif case == "symlink":
                    source = self.root / "symlink-source"
                    binary.rename(source)
                    binary.symlink_to(source)
                else:
                    marker = b"/home/another-producer/private-source/main.zig" if case == "cross-runner-home" else b"__DWARF"
                    binary.write_bytes(binary.read_bytes() + marker)
                    metadata["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
                metadata_file.write_text(json.dumps(metadata))
                output = self.root / f"{case}-output"
                with self.assertRaises(ValueError):
                    aggregate(self.tag, inputs, output, self.repo)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
