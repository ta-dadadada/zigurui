"""合成corpusの決定性・fixtureの妥当性・独立oracleを確認する。"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from benchmark_corpus import DEFAULT_SEED, prepare, queries, verify


class CorpusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zigurui-corpus-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_deterministic_manifest_and_common_prefix(self):
        small = prepare(self.base / "small", 12)
        repeated = prepare(self.base / "repeated", 12)
        large = prepare(self.base / "large", 100)
        self.assertEqual(small, repeated)
        self.assertEqual(small["documents"], large["documents"][:12])
        self.assertEqual(small["language_counts"], {"ja": 4, "en": 4, "mixed": 4})
        self.assertLessEqual(max(large["language_counts"].values()) - min(large["language_counts"].values()), 1)
        self.assertEqual(json.loads((self.base / "small" / "manifest.json").read_text()), small)
        generated = [doc["bytes"] for doc in large["documents"] if not doc["handcrafted"]]
        self.assertTrue(all(1024 <= count < 1200 for count in generated))
        changed = prepare(self.base / "changed", 12, DEFAULT_SEED + 1)
        self.assertNotEqual(small["corpus_sha256"], changed["corpus_sha256"])

    def test_existing_directory_is_not_modified(self):
        root = self.base / "existing"
        root.mkdir()
        sentinel = root / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            prepare(root, 100)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(list(root.iterdir()), [sentinel])
        with self.assertRaises(ValueError):
            prepare(self.base / "invalid", 5)
        self.assertFalse((self.base / "invalid").exists())

    def test_handcrafted_intents_and_false_candidates(self):
        root = self.base / "fixture"
        manifest = prepare(root, 100)
        documents = {doc["path"]: (root / "documents" / doc["path"]).read_text() for doc in manifest["documents"]}
        handmade = {doc["path"] for doc in manifest["documents"] if doc["handcrafted"]}
        for query in queries():
            with self.subTest(query=query["id"]):
                actual = sorted(path for path in handmade if query["text"] in documents[path])
                self.assertEqual(actual, sorted(query["expected_documents"]))
                if query["category"] in ("no-hit", "false-candidate"):
                    self.assertFalse(any(query["text"] in text for text in documents.values()))
                if query["category"] == "false-candidate":
                    candidate = documents[query["candidate_document"]]
                    self.assertTrue(all(query["text"][i:i + 2] in candidate for i in range(len(query["text"]) - 1)))

    def test_real_cli_and_independent_oracle(self):
        binary = Path(__file__).resolve().parents[1] / "zig-out" / "bin" / "zigurui"
        if not binary.is_file():
            self.skipTest("ziguruiを先にビルドする")
        root = self.base / "cli"
        manifest = prepare(root, 100)
        index = root / "notes.idx"
        subprocess.run([str(binary), "index", str(root / "documents"), str(index)], check=True, capture_output=True)
        result = verify(binary, root, index, manifest)
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(result["query_results"]), 14)
        first_doc = root / "documents" / manifest["documents"][0]["path"]
        first_doc.write_text(first_doc.read_text() + "追記")
        self.assertFalse(verify(binary, root, index, manifest)["corpus_integrity_passed"])


if __name__ == "__main__":
    unittest.main()
