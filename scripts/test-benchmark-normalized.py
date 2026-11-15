#!/usr/bin/env python3
"""正規化の独立oracle、候補条件、計測形式と公開投影の境界を確認する。"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import benchmark_normalized as runner


class OracleTest(unittest.TestCase):
    def test_original_lines_do_not_use_normalized_byte_offsets(self):
        texts = {"a.md": "㌔㌔\n前ｶﾞ後\n", "b.md": "a\u0315\u0300\n"}
        self.assertEqual(runner.expected_hits(texts, "ガ", "normalized"), [("a.md", 2)])
        self.assertEqual(runner.expected_hits(texts, "ロ", "normalized"), [("a.md", 1)])
        self.assertEqual(runner.expected_hits(texts, "à", "normalized"), [("b.md", 1)])
        self.assertEqual(runner.expected_hits(texts, "ガ", "literal"), [])

    def test_heading_order_uses_fixture_metadata_and_keeps_lines(self):
        texts = {"a.md": "ガラス\n", "b.md": "ガラス\n# ガラス\n", "c.md": "ガラス\n# ｶﾞﾗｽ\n"}
        headings = {"a.md": [], "b.md": ["ガラス", "ガラス"], "c.md": ["ｶﾞﾗｽ"]}
        self.assertEqual(runner.expected_hits(texts, "ガラス", "literal", headings), [("b.md", 1), ("a.md", 1), ("c.md", 1)])
        self.assertEqual(runner.expected_hits(texts, "ガラス", "normalized", headings), [("b.md", 1), ("c.md", 1), ("a.md", 1)])
        self.assertEqual(runner.expected_hits(texts, "ガラス", "normalized"), [("a.md", 1), ("b.md", 1), ("c.md", 1)])

    def test_corpus_headings_are_reproducible_and_match_generated_text(self):
        with tempfile.TemporaryDirectory(prefix="zigurui-heading-corpus-") as temporary:
            left, texts = runner.corpus(Path(temporary) / "left", 6, 20261114)
            right, _ = runner.corpus(Path(temporary) / "right", 6, 20261114)
            self.assertEqual(left["corpus_sha256"], right["corpus_sha256"])
            for document in left["documents"]:
                actual = [line[2:] for line in texts[document["path"]].splitlines() if line.startswith("# ")]
                self.assertEqual(document["headings"], actual)

    def test_false_candidates_are_separate_from_hits(self):
        texts = {"a.md": "東京と京駅と駅前", "b.md": "東京駅前", "c.md": "東京"}
        config = runner.CONFIGURATIONS[1]
        self.assertEqual(runner.expected_counts(texts, "東京駅前", config, "search"), {
            "shortest_posting_count": 2, "candidate_count": 2, "documents_verified": 2, "hit_count": 1,
        })
        self.assertEqual(runner.expected_counts(texts, "東", config, "search"), {
            "shortest_posting_count": 0, "candidate_count": 0, "documents_verified": 3, "hit_count": 3,
        })
        self.assertEqual(runner.expected_counts(texts, "不存在", config, "search")["documents_verified"], 0)

    def test_normalized_index_literal_search_is_scan(self):
        config = runner.CONFIGURATIONS[2]
        actual = runner.expected_counts({"a.md": "ガラス", "b.md": "ｶﾞﾗｽ"}, "ガラス", config, "search")
        self.assertEqual(actual, {"shortest_posting_count": 0, "candidate_count": 0, "documents_verified": 2, "hit_count": 1})

    def test_index_identity_requires_bytes_not_only_size(self):
        indices = [{"variant_id": variant, "instrumentation": "plain", "mode": mode,
                    "format_version": 2, "index_bytes": 100, "index_sha256": "a" * 64}
                   for mode in ("literal", "normalized") for variant in (0, 1)]
        self.assertTrue(all(group["identical"] for group in runner.index_identity(indices, True)["groups"]))
        indices[-1]["index_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "index bytes differ"):
            runner.index_identity(indices, True)
        self.assertFalse(runner.index_identity(indices, False)["groups"][1]["identical"])


class ExecutionTest(unittest.TestCase):
    def test_execute_passes_config_environment_to_the_child(self):
        with tempfile.TemporaryDirectory(prefix="zigurui-measure-env-") as temporary:
            root = Path(temporary).resolve()
            env = {**os.environ, "XDG_CONFIG_HOME": str(root / "empty-config")}
            _, _, output = runner.execute(
                [sys.executable, "-c", "import os;print(os.environ['XDG_CONFIG_HOME'])"],
                root / "child.stderr", 10, capture=True, env=env)
            self.assertEqual(output.decode().strip(), env["XDG_CONFIG_HOME"])


class ProfileTest(unittest.TestCase):
    def profile(self):
        return {
            "schema_version": 1, "profile": True, "command": "search", "mode": "normalized", "status": "ok",
            "total_ns": 11, "timings_ns": {name: 1 for name in runner.PHASES}, "counts": {name: 0 for name in runner.COUNTS},
        }

    def test_profile_shape_and_nonoverlap(self):
        profile = self.profile()
        self.assertEqual(runner.parse_profile(json.dumps(profile).encode(), "search", "normalized"), profile)
        profile["total_ns"] = 10
        with self.assertRaisesRegex(ValueError, "overlap"):
            runner.parse_profile(json.dumps(profile).encode(), "search", "normalized")

    def test_profile_v2_requires_ranking_and_preserves_v1(self):
        old = self.profile()
        self.assertNotIn("ranking", runner.parse_profile(json.dumps(old).encode(), "search", "normalized")["timings_ns"])
        new = copy.deepcopy(old)
        new["schema_version"] = 2
        new["timings_ns"]["ranking"] = 3
        new["total_ns"] += 3
        self.assertEqual(runner.parse_profile(json.dumps(new).encode(), "search", "normalized"), new)
        del new["timings_ns"]["ranking"]
        with self.assertRaisesRegex(ValueError, "keys"):
            runner.parse_profile(json.dumps(new).encode(), "search", "normalized")
        old["schema_version"] = 3
        with self.assertRaisesRegex(ValueError, "schema"):
            runner.parse_profile(json.dumps(old).encode(), "search", "normalized")

    def test_diagnostics_and_missing_counts_are_rejected(self):
        profile = self.profile()
        del profile["counts"]["hit_count"]
        with self.assertRaises(ValueError):
            runner.parse_profile(json.dumps(profile).encode(), "search", "normalized")
        with self.assertRaises(ValueError):
            runner.parse_profile(b"private diagnostic\n{}", "search", "normalized")

    def test_projection_discards_private_metadata_and_unknown_summary_keys(self):
        summary = {name: 1 for name in runner.SUMMARY_FIELDS}
        raw = {
            "status": "complete", "configuration": dict(document_count=6, seed=1, index_repeats=1, repeats=1, warmups=0),
            "corpus": dict(corpus_sha256="a" * 64, total_bytes=100), "verification": {"passed": True}, "positions": {"passed": True},
            "binary_unchanged": True, "environment": {"hostname": "private-host", "cpu_model": "private-model", "path": "/Users/private"},
            "variants": [{"id": 0, "plain_sha256": "a" * 64, "profile_sha256": "b" * 64, "provenance_sha256": None, "name": "private-name"}],
            "index_identity": {"required": False, "groups": []}, "cross_read": {"passed": True, "cases": [], "skipped": []},
            "results": [], "indices": [{"variant_id": 0, "mode": "normalized", "instrumentation": "plain", "format_version": 2, "index_bytes": 100, "index_sha256": "c" * 64,
                                         "summary": {**summary, "unknown": "private-value"}}],
        }
        sample = {"elapsed_ms": 1, "peak_rss_bytes": 1, "profile": self.profile()}
        raw["results"] = [{"variant_id": 0, "configuration_id": "normalized", "query_id": "rare", "method": "search", "expected_counts": {name: 0 for name in runner.EXPECTED_COUNTS}, "plain": {"samples": [sample]}, "profile": {"samples": [sample]}}]
        original = copy.deepcopy(raw)
        projected = runner.project(raw)
        serialized = json.dumps(projected)
        self.assertNotIn("private", serialized)
        self.assertEqual(projected["indices"][0]["summary"], summary)
        self.assertEqual(raw, original)
        self.assertIsNone(projected["results"][0]["profile_timings_ns"]["ranking"])
        raw["configuration"]["rank"] = "heading"
        sample["profile"]["timings_ns"]["ranking"] = 4
        sample["profile"]["schema_version"] = 2
        sample["profile"]["total_ns"] += 4
        projected = runner.project(raw)
        self.assertEqual(projected["configuration"]["rank"], "heading")
        self.assertEqual(projected["results"][0]["profile_timings_ns"]["ranking"], {"median": 4, "p95": 4})


if __name__ == "__main__":
    unittest.main()
