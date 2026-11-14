#!/usr/bin/env python3
"""ANDの独立評価が語句・正規化・原文行と候補件数を区別することを確認する。"""
import copy
import json
import os
from pathlib import Path
import tempfile
from unittest import mock
import unittest

import evaluate_and as evaluation


class AndOracleTest(unittest.TestCase):
    def test_runner_ignores_user_config_for_correctness_and_measurement(self):
        binary = Path(__file__).resolve().parents[1] / "zig-out/bin/zigurui"
        if not binary.is_file():
            self.skipTest("ziguruiを先にビルドする")

        class MeasurementReached(Exception):
            pass

        with tempfile.TemporaryDirectory(prefix="zigurui-and-config-") as temporary:
            root = Path(temporary).resolve()
            user_config = root / "user-config/zigurui/config.json"
            user_config.parent.mkdir(parents=True)
            for case, contents in (("normalized", '{"mode":"normalized"}'), ("invalid", '{"unknown":true}')):
                with self.subTest(case=case):
                    user_config.write_text(contents)
                    args = evaluation.arguments(["--plain", str(binary), "--profile", str(binary),
                        "--output-root", str(root / "runs"), "--run-id", case,
                        "--size", "6", "--index-repeats", "1", "--warmups", "0", "--repeats", "1"])
                    def stop_before_measurement(*_args, **kwargs):
                        # 正解照合を実際に終えてから、性能は計測せず環境の伝播だけを確認する。
                        self.assertEqual(kwargs["env"]["XDG_CONFIG_HOME"], str(root / "runs" / case / "config"))
                        raise MeasurementReached
                    with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(user_config.parent.parent)}), \
                            mock.patch.object(evaluation.benchmark, "environment", return_value={}), \
                            mock.patch.object(evaluation.normalized, "execute", side_effect=stop_before_measurement) as measured:
                        with self.assertRaises(MeasurementReached):
                            evaluation.run(args)
                        measured.assert_called_once()
                    self.assertEqual(user_config.read_text(), contents)
                    self.assertFalse((root / "runs" / case / "config").exists())

    def test_phrase_terms_do_not_split_spaces(self):
        texts = {"a.md": "alpha beta\ngamma", "b.md": "alpha\nbeta\ngamma"}
        config = evaluation.CONFIGURATIONS[0]
        hits, _ = evaluation.expected(texts, ["alpha beta", "gamma"], config, "search")
        self.assertEqual(hits, [("a.md", 1)])
        hits, _ = evaluation.expected(texts, ["alpha", "beta"], config, "search")
        self.assertEqual(hits, [("a.md", 1), ("b.md", 1)])

    def test_display_line_uses_earliest_original_term_not_input_order(self):
        texts = {"a.md": "㌔\n前ｶﾞ後\nanchor"}
        hits, _ = evaluation.expected(texts, ["anchor", "ガ"], evaluation.CONFIGURATIONS[1], "search")
        self.assertEqual(hits, [("a.md", 2)])

    def test_normalization_duplicate_and_fullwidth_space(self):
        self.assertEqual(evaluation.prepared_terms(["Ａ", "a", "Ｂ"], "normalized"), ["a", "b"])
        self.assertEqual(evaluation.prepared_terms(["alpha　beta"], "literal"), ["alpha　beta"])
        self.assertEqual(evaluation.prepared_terms(["alpha　beta"], "normalized"), ["alpha beta"])
        with self.assertRaises(ValueError):
            evaluation.prepared_terms(["absent", ""], "normalized")

    def test_candidates_and_transform_count_are_per_document(self):
        texts = {"a.md": "東京と京駅と駅前 ガラス", "b.md": "東京駅前 ガラス", "c.md": "ガラス"}
        hits, counts = evaluation.expected(texts, ["東京駅前", "ガラス"], evaluation.CONFIGURATIONS[1], "search")
        self.assertEqual(hits, [("b.md", 1)])
        self.assertEqual(counts["candidate_count"], 2)
        self.assertEqual(counts["documents_verified"], 2)
        self.assertEqual(counts["normalized_document_count"], 2)
        self.assertEqual(counts["normalized_input_bytes"], len((texts["a.md"] + texts["b.md"]).encode()))
        _, counts = evaluation.expected(texts, ["東", "ガ"], evaluation.CONFIGURATIONS[1], "search")
        self.assertEqual(counts["shortest_posting_count"], 0)
        self.assertEqual(counts["candidate_count"], 0)
        self.assertEqual(counts["normalized_document_count"], 3)

    def test_public_projection_omits_environment_and_diagnostics(self):
        raw = {
            "status": "complete", "configuration": dict(size=6, seed=1, repeats=1, warmups=0, index_repeats=1),
            "binary_sha256": dict(plain="a" * 64, profile="b" * 64, phrase_baseline=None),
            "corpus": dict(corpus_sha256="c" * 64, total_bytes=100),
            "quality": dict(cases=1, invalid_cases=1, option_path_passed=True, passed=True),
            "regression": dict(cases=1, passed=True), "binary_unchanged": True, "indices": [], "results": [],
            "environment": {"hostname": "private-host", "path": "/Users/private"}, "failure": "private-diagnostic",
        }
        original = copy.deepcopy(raw)
        public = json.dumps(evaluation.project(raw))
        self.assertNotIn("private", public)
        self.assertEqual(raw, original)


if __name__ == "__main__":
    unittest.main()
