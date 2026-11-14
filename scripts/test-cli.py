#!/usr/bin/env python3
"""実行ファイルを別プロセスから呼び、保存・検索・再構築を確認する。"""
import hashlib
import json
import os
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
        self.base = pathlib.Path(self.temp.name).resolve()
        self.docs = self.base / "文書"
        self.docs.mkdir()
        (self.docs / "nested").mkdir()
        (self.docs / "a.md").write_text("# 技術メモ\n日本語で検索😀 engine\n", encoding="utf-8")
        (self.docs / "nested" / "b.markdown").write_text("Engine\nab--bc\n語と日本\n", encoding="utf-8")
        (self.docs / "ignore.txt").write_text("検索 hidden", encoding="utf-8")
        (self.docs / "link.md").symlink_to(self.docs / "a.md")
        (self.docs / "loop").symlink_to(self.docs, target_is_directory=True)
        self.index = self.base / "notes.idx"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        self.env["HOME"] = str(self.base / "home")

    def command(self, *args, success=True, env=None):
        result = subprocess.run([BINARY, *map(str, args)], capture_output=True, env=self.env if env is None else env)
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

    def test_normalized_search_scan_original_excerpt_and_literal_mode(self):
        source = "前置き\nＡＢＣ ｶﾞﾗｽ か\u3099 ㌔ Straße\n👩\u200d💻 葛\U000e0100\n"
        path = self.docs / "normalized.md"
        path.write_text(source, encoding="utf-8")
        self.command("index", "--mode", "normalized", self.docs, self.index)
        for query in ("abc", "ＡＢＣ", "ガラス", "ｶﾞﾗｽ", "が", "か\u3099", "㌔", "ロ", "STRASSE", "s", "👩\u200d💻", "葛\U000e0100", " ", "\n", "absent"):
            with self.subTest(query=query):
                indexed = self.command("search", "--mode", "normalized", self.index, query).stdout
                scanned = self.command("scan", "--mode", "normalized", self.docs, query).stdout
                self.assertEqual(indexed, scanned)
                indexed.decode("utf-8")
        expected = f"{path}:2: ＡＢＣ ｶﾞﾗｽ か\u3099 ㌔ Straße\n".encode()
        self.assertEqual(self.command("search", "--mode", "normalized", self.index, "abc").stdout, expected)
        self.assertEqual(self.command("search", self.index, "abc").stdout, b"")
        self.assertEqual(self.command("search", "--mode", "literal", self.index, "ＡＢＣ").stdout, expected)
        for query in ("👩💻", "か\u309b", "ガラズ", "STRASSE missing"):
            self.assertEqual(self.command("search", "--mode", "normalized", self.index, query).stdout, b"")
        self.assertEqual(path.read_text(encoding="utf-8"), source)
        path.write_text("変更した本文", encoding="utf-8")
        self.assertEqual(self.command("search", "--mode", "normalized", self.index, "abc").stdout, expected)
        self.command("index", "--mode", "normalized", self.docs, self.index)
        self.assertEqual(self.command("search", "--mode", "normalized", self.index, "abc").stdout, b"")

    def test_modes_empty_query_and_old_index_fail_explicitly(self):
        self.command("index", self.docs, self.index)
        result = self.command("search", "--mode", "normalized", self.index, "日本", success=False)
        self.assertIn(b"IndexModeMismatch", result.stderr)
        self.assertIn("再構築".encode(), result.stderr)
        for command, target in (("search", self.index), ("scan", self.docs)):
            self.command(command, "--mode", "other", target, "日本", success=False)
            self.command(command, "--mode", "normalized", target, "", success=False)
        self.command("index", "--mode", "normalized", self.docs, self.index)
        self.command("search", "--mode", "normalized", self.index, "", success=False)
        old = bytearray(self.index.read_bytes())
        old[8:12] = (1).to_bytes(4, "little")
        self.index.write_bytes(old)
        result = self.command("search", self.index, "日本", success=False)
        self.assertIn(b"UnsupportedVersion", result.stderr)
        self.assertIn("再構築".encode(), result.stderr)

    def test_failed_build_preserves_existing_index(self):
        self.command("index", self.docs, self.index)
        original = self.index.read_bytes()
        (self.docs / "invalid.md").write_bytes(b"\xff")
        self.command("index", self.docs, self.index, success=False)
        self.assertEqual(self.index.read_bytes(), original)
        self.command("index", self.docs, self.docs / "a.md", success=False)
        self.assertIn("技術メモ", (self.docs / "a.md").read_text(encoding="utf-8"))

    def test_xdg_paths_defaults_and_explicit_compatibility(self):
        home = self.base / "home"
        env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        env["HOME"] = str(home)
        result = self.command("paths", env=env).stdout.decode()
        self.assertIn(f"config: {home}/.config/zigurui", result)
        self.assertIn(f"data: {home}/.local/share/zigurui", result)
        self.assertIn(f"cache: {home}/.cache/zigurui", result)
        self.assertIn(f"state: {home}/.local/state/zigurui", result)
        self.assertFalse(home.exists())
        for kind in ("CONFIG", "DATA", "CACHE", "STATE"):
            env[f"XDG_{kind}_HOME"] = str(self.base / kind)
        paths = self.command("paths", env=env).stdout.decode()
        for kind in ("CONFIG", "DATA", "CACHE", "STATE"):
            self.assertIn(f"{kind.lower()}: {self.base / kind}/zigurui", paths)
        data_index = self.base / "DATA/zigurui/default.idx"
        self.command("index", "--mode", "normalized", self.docs, env=env)
        self.assertTrue(data_index.is_file())
        self.assertEqual(self.command("search", "--mode", "normalized", "ENGINE", env=env).stdout,
                         self.command("search", "--mode", "normalized", data_index, "ENGINE", env=env).stdout)
        config = self.base / "CONFIG/zigurui"
        config.mkdir(parents=True)
        sentinel = config / "user-settings"
        sentinel.write_bytes(b"keep")
        self.command("index", self.docs, env=env)
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.assertIn("検索".encode(), self.command("search", "検索", env=env).stdout)
        env["XDG_DATA_HOME"] = "relative/path"
        self.command("index", self.docs, env=env)
        self.assertTrue((home / ".local/share/zigurui/default.idx").is_file())
        env["XDG_DATA_HOME"] = ""
        self.assertIn(f"index: {home}/.local/share/zigurui/default.idx", self.command("paths", env=env).stdout.decode())
        env.pop("HOME")
        self.command("paths", env=env, success=False)
        self.command("index", self.docs, self.index, env=env)
        self.command("search", self.index, "検索", env=env)
        self.assertTrue(self.command("--version", env=env).stdout.startswith(b"zigurui "))
        self.assertEqual(self.command("version", env=env).stdout, self.command("--version", env=env).stdout)
        env["XDG_DATA_HOME"] = str(self.base / "DATA")
        self.command("search", "検索", env=env)

    def write_config(self, contents):
        path = pathlib.Path(self.env["HOME"]) / ".config/zigurui/config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def test_config_defaults_cli_overrides_and_relative_path_base(self):
        path = self.write_config(json.dumps({"mode": "normalized", "index_path": "indexes/notes.idx"}))
        configured_index = path.parent / "indexes/notes.idx"
        settings = path.read_bytes()
        self.command("index", self.docs)
        self.assertTrue(configured_index.exists())
        self.assertIn(b"b.markdown", self.command("search", "ENGINE").stdout)
        self.assertEqual(self.command("search", "ENGINE").stdout, self.command("scan", self.docs, "ENGINE").stdout)
        self.assertEqual(self.command("search", "--mode", "literal", "ENGINE").stdout, b"")
        self.command("index", "--mode", "literal", self.docs, self.index)
        self.assertIn(b"b.markdown", self.command("search", "--mode", "literal", self.index, "Engine").stdout)
        # 設定modeを適用しても、CLI索引パスは設定より優先する。
        mismatch = self.command("search", self.index, "Engine", success=False)
        self.assertIn(b"IndexModeMismatch", mismatch.stderr)
        self.assertEqual(self.command("scan", "--mode", "literal", self.docs, "ENGINE").stdout, b"")
        paths = self.command("paths").stdout.decode()
        self.assertIn(f"config_file: {path}", paths)
        self.assertIn(f"index: {configured_index}", paths)
        self.assertIn("mode: normalized", paths)
        other_cwd = self.base / "other"
        other_cwd.mkdir()
        result = subprocess.run([BINARY, "search", "ENGINE"], env=self.env, cwd=other_cwd, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.command("search", "ENGINE").stdout)
        self.assertEqual(path.read_bytes(), settings)
        relative_cli = subprocess.run([BINARY, "index", "--mode", "literal", str(self.docs), "cli.idx"],
                                      env=self.env, cwd=other_cwd, capture_output=True)
        self.assertEqual(relative_cli.returncode, 0, relative_cli.stderr)
        self.assertTrue((other_cwd / "cli.idx").exists())
        self.assertFalse((path.parent / "cli.idx").exists())
        # 空オブジェクトは従来の既定値、項目の省略も可能。
        path.write_text("{}")
        self.command("index", self.docs)
        self.assertEqual(self.command("search", "ENGINE").stdout, b"")
        self.assertTrue((pathlib.Path(self.env["HOME"]) / ".local/share/zigurui/default.idx").exists())
        path.write_text(json.dumps({"index_path": str(configured_index)}))
        self.assertIn(b"a.md", self.command("search", "engine").stdout)
        path.write_text(json.dumps({"mode": "normalized"}))
        self.command("index", self.docs)
        self.assertIn(b"b.markdown", self.command("search", "ENGINE").stdout)

    def test_invalid_config_is_diagnosed_before_writing_even_with_cli_overrides(self):
        self.command("index", self.docs, self.index)
        original = self.index.read_bytes()
        cases = (
            ("{", "JSON"), ("", "JSON"), ("[]", "全体"), ("null", "全体"),
            ('{"unknown": 1}', "unknown"), ('{"mode": 1}', "mode"),
            ('{"mode": null}', "mode"), ('{"mode": "other"}', "mode"),
            ('{"index_path": []}', "index_path"), ('{"index_path": null}', "index_path"),
            ('{"index_path": ""}', "index_path"), ('{"index_path": "a\\u0000.idx"}', "index_path"),
            ('{"mode": "literal", "mode": "normalized"}', "DuplicateField"),
            ('{"mode": true}', "mode"), ('{"index_path": false}', "index_path"),
            (" " * (64 * 1024 + 1), "読み込めません"),
        )
        for contents, reason in cases:
            with self.subTest(contents=contents[:80]):
                path = self.write_config(contents)
                config_bytes = path.read_bytes()
                result = self.command("index", "--mode", "literal", self.docs, self.index, success=False)
                self.assertIn(str(path).encode(), result.stderr)
                self.assertIn(reason.encode(), result.stderr)
                self.assertEqual(self.index.read_bytes(), original)
                self.assertEqual(path.read_bytes(), config_bytes)
                self.command("help")
                self.command("--version")
                self.command("paths", success=False)
        path.write_bytes(b'{"index_path":"\xff"}')
        self.command("index", self.docs, success=False)
        path.unlink()
        path.mkdir()
        result = self.command("scan", self.docs, "engine", success=False)
        self.assertIn(str(path).encode(), result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "rootはファイルの読取制限を回避するため")
    def test_unreadable_config_fails_without_changing_index(self):
        self.command("index", self.docs, self.index)
        before = self.index.read_bytes()
        path = self.write_config("{}")
        path.chmod(0)
        try:
            result = self.command("index", self.docs, self.index, success=False)
            self.assertIn(str(path).encode(), result.stderr)
            self.assertIn("読み込めません".encode(), result.stderr)
            self.assertEqual(self.index.read_bytes(), before)
        finally:
            path.chmod(0o600)

    def test_config_is_protected_from_index_overwrite_and_can_use_xdg_without_home(self):
        path = self.write_config('{"index_path": "config.json"}')
        before = path.read_bytes()
        self.assertIn(b"ConfigFileIsIndex", self.command("index", self.docs, success=False).stderr)
        self.command("index", self.docs, path, success=False)
        self.assertEqual(path.read_bytes(), before)
        # 設定ファイルのリンク先も保護する。
        actual = self.base / "settings.json"
        path.rename(actual)
        path.symlink_to(actual)
        self.command("index", self.docs, actual, success=False)
        self.assertEqual(actual.read_bytes(), before)
        actual.write_text(json.dumps({"index_path": str(self.index), "mode": "normalized"}))
        env = {k: v for k, v in self.env.items() if k != "HOME"}
        env["XDG_CONFIG_HOME"] = str(path.parent.parent)
        self.command("index", self.docs, env=env)
        self.assertIn(b"b.markdown", self.command("search", "ENGINE", env=env).stdout)
        self.assertEqual(actual.read_text(), json.dumps({"index_path": str(self.index), "mode": "normalized"}))

    def test_config_mode_with_and_frontmatter_and_schema(self):
        source = self.docs / "metadata.md"
        source.write_text('---\nkind: note\n---\nＡＢＣ ガラス\n', encoding="utf-8")
        self.write_config(json.dumps({"mode": "normalized", "index_path": str(self.index)}))
        self.command("index", self.docs)
        indexed = self.command("search", "--all", self.index, "abc", "ガラス").stdout
        self.assertIn(b"metadata.md", indexed)
        self.assertEqual(indexed, self.command("scan", "--all", self.docs, "abc", "ガラス").stdout)
        self.assertEqual(self.command("search", "--mode", "literal", "--all", self.index, "abc", "ガラス").stdout, b"")
        self.assertIn(b"metadata.md", self.command("search", "--frontmatter", "--where", "/kind", '"note"', "abc").stdout)
        self.assertIn(b"metadata.md", self.command("search", "--where", "file:/name", '"metadata.md"', "abc").stdout)
        self.command("schema", self.index)
        self.command("schema", success=False)
        self.command("search", "--all", self.index, success=False)

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
