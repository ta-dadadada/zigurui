#!/usr/bin/env python3
"""実行ファイルを別プロセスから呼び、保存・検索・再構築を確認する。"""
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest

BINARY = str(pathlib.Path(sys.argv.pop(1) if len(sys.argv) > 1 else "zig-out/bin/zigurui").resolve())


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zigurui-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.docs = self.base / "文書"
        self.docs.mkdir()
        (self.docs / "nested").mkdir()
        (self.docs / "a.md").write_text("# 技術メモ\n日本語で検索😀 engine\n", encoding="utf-8")
        (self.docs / "nested" / "b.markdown").write_text("Engine\nab--bc\n語と日本\n", encoding="utf-8")
        (self.docs / "ignore.txt").write_text("検索 hidden", encoding="utf-8")
        (self.docs / "link.md").symlink_to(self.docs / "a.md")
        (self.docs / "loop").symlink_to(self.docs, target_is_directory=True)
        self.index = self.base / "notes.idx"

    def command(self, *args, success=True):
        result = subprocess.run([BINARY, *map(str, args)], capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stderr.decode("utf-8", errors="replace"))
        return result

    def test_saved_search_matches_scan_and_preserves_sources(self):
        originals = {p: hashlib.sha256(p.read_bytes()).digest() for p in self.docs.rglob("*") if p.is_file()}
        result = self.command("index", self.docs, self.index)
        self.assertIn(b"2 documents", result.stdout)
        for query in ("検索", "語", "😀", "engine", "Engine", "abc", "検索😀", "日本", "absent"):
            with self.subTest(query=query):
                indexed = self.command("search", self.index, query).stdout
                scanned = self.command("scan", self.docs, query).stdout
                self.assertEqual(indexed, scanned)
                indexed.decode("utf-8")
        self.assertEqual(self.command("search", self.index, "abc").stdout, b"")
        self.assertIn(":2: ".encode(), self.command("search", self.index, "検索").stdout)
        for path, digest in originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
        (self.docs / "a.md").write_text("本文を改訂 新しい検索", encoding="utf-8")
        self.command("index", self.docs, self.index)
        self.assertEqual(self.command("search", self.index, "engine").stdout, b"")
        self.assertIn("改訂".encode(), self.command("search", self.index, "改訂").stdout)
        (self.docs / "a.md").unlink()
        self.assertIn("検索".encode(), self.command("search", self.index, "検索").stdout)
        self.command("index", self.docs, self.index)
        self.assertEqual(self.command("search", self.index, "検索").stdout, b"")
        (self.docs / "new.MD").write_text("追加した検索", encoding="utf-8")
        self.command("index", self.docs, self.index)
        self.assertIn(b"new.MD", self.command("search", self.index, "検索").stdout)

    def test_failed_build_preserves_existing_index(self):
        self.command("index", self.docs, self.index)
        original = self.index.read_bytes()
        (self.docs / "invalid.md").write_bytes(b"\xff")
        self.command("index", self.docs, self.index, success=False)
        self.assertEqual(self.index.read_bytes(), original)
        self.command("index", self.docs, self.docs / "a.md", success=False)
        self.assertIn("技術メモ", (self.docs / "a.md").read_text(encoding="utf-8"))

    def test_errors_and_help(self):
        self.assertIn(b"zigurui index", self.command("help").stdout)
        self.command("unknown", success=False)
        self.command("index", self.docs, self.index)
        self.command("search", self.index, "", success=False)
        self.command("scan", self.docs, "", success=False)
        original = self.index.read_bytes()
        for corrupted in (b"", original[:10], b"bad index", original + b"trailing"):
            self.index.write_bytes(corrupted)
            self.command("search", self.index, "検索", success=False)


if __name__ == "__main__":
    unittest.main()
