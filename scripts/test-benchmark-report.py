#!/usr/bin/env python3
"""公開要約が個人パスと未知metadataを持ち出さないことを検証する。"""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import benchmark_report


def fixture():
    secret = "PRIVATE_HOST_AND_USERNAME_SENTINEL"
    private_path = f"/Users/{secret}/repository"
    metrics = {"count": 30, "median_ms": 2.0, "p95_ms": 3.0, "median_peak_rss_bytes": 1024, "p95_peak_rss_bytes": 2048, "private": secret}
    return {
        "status": "complete", "started_at_utc": "2026-11-13T15:33:11+00:00", "finished_at_utc": "2026-11-13T15:34:52+00:00", "hostname": secret,
        "configuration": {"sizes": [100], "index_repeats": 5, "query_repeats": 30, "warmups_per_method_query": 2, "seed": 20261114, "timeout_seconds": 120, "build_mode": "ReleaseFast", "invocation": [private_path + "/python3"], "private": secret},
        "environment": {
            "os": "macOS-26.6.2-arm64", "architecture": "arm64", "python": "3.14.7 (build at " + private_path + ")",
            "zig_version": {"value": "0.17.1", "private": secret}, "git_head": {"value": "a" * 40}, "binary_sha256": "b" * 64,
            "cpu_model": {"value": None, "error": private_path}, "memory_bytes": {"value": None, "error": secret}, "logical_cpu_count": 10,
            "source_sha256": {"src/main.zig": "c" * 64, private_path: secret}, "runner_sha256": {"scripts/benchmark.py": "d" * 64, private_path: secret},
            "binary_path": private_path + "/zigurui", "working_directory": private_path, "hostname": secret, "unknown": {"nested": secret},
        },
        "datasets": [{
            "document_count": 100, "corpus_bytes": 102400, "index_bytes": 204800, "corpus_sha256": "e" * 64,
            "manifest_path": private_path + "/manifest.json", "language_counts": {"ja": 34, "en": 33, "mixed": 33, "private": secret},
            "correctness": {"passed": True, "private": secret}, "query_suitability": {"passed": True, "private": secret},
            "measurement_validity": {"passed": True, "all_commands_succeeded": True, "binary_unchanged": True, "samples_complete": True, "private": secret},
            "index": {"summary": copy.deepcopy(metrics), "samples": [{"command": [private_path], "private": secret}]},
            "verification": {"query_results": [{"id": "ja-two", "expected_hit_count": 2, "actual_hit_count": 2, "stdout_equal": True, "oracle_equal": True, "intent_passed": True, "private": secret}]},
            "queries": [{"id": "ja-two", "text": "検索", "category": "two-codepoint", "language": "ja", "intent": "検索についての設計を見つける", "expected_documents": ["000000-ja.md"], "private": {"nested": secret}, "search": {"summary": copy.deepcopy(metrics), "samples": [{"command": [private_path]}]}, "scan": {"summary": copy.deepcopy(metrics)}}],
            "private": secret,
        }],
    }


class ReportTests(unittest.TestCase):
    def test_allowlist_omits_paths_hosts_unknown_fields_and_raw_samples(self):
        result = benchmark_report.project(fixture())
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("PRIVATE_HOST_AND_USERNAME_SENTINEL", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn('"samples"', serialized)
        self.assertEqual(result["environment"]["python_version"], "3.14.7")
        self.assertEqual(result["environment"]["os_version"], "26.6.2")
        self.assertEqual(result["datasets"][0]["queries"][0]["search"]["median_ms"], 2.0)
        self.assertEqual(result["queries"][0]["text"], "検索")

    def test_known_scalar_fields_cannot_smuggle_nested_metadata(self):
        raw = fixture()
        raw["datasets"][0]["queries"][0]["intent"] = {"private": "PRIVATE_HOST_AND_USERNAME_SENTINEL"}
        with self.assertRaises(ValueError):
            benchmark_report.project(raw)
        raw = fixture()
        raw["datasets"][0]["queries"][0]["search"]["summary"]["median_ms"] = {"private": "PRIVATE_HOST_AND_USERNAME_SENTINEL"}
        with self.assertRaises(ValueError):
            benchmark_report.project(raw)

    def test_export_preserves_raw_and_projects_supplemental_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "raw.json"
            output = Path(directory) / "summary.json"
            data = json.dumps(fixture()).encode()
            raw_path.write_bytes(data)
            hardware = {"cpu_model": "Apple M4", "memory_bytes": 17179869184, "logical_cpu_count": 10, "private": "PRIVATE_HOST_AND_USERNAME_SENTINEL"}
            benchmark_report.export(raw_path, output, hardware)
            result = json.loads(output.read_text())
            self.assertEqual(raw_path.read_bytes(), data)
            self.assertEqual(result["raw_result_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(result["supplemental_hardware"]["memory_bytes"], 17179869184)
            self.assertNotIn("PRIVATE_HOST_AND_USERNAME_SENTINEL", output.read_text())

    def test_failed_runs_and_non_synthetic_paths_are_rejected(self):
        raw = fixture()
        raw["status"] = "failed"
        with self.assertRaises(ValueError):
            benchmark_report.project(raw)

    def test_modified_source_paths_are_allowlisted_even_when_status_is_stripped(self):
        raw = fixture()
        raw["environment"]["git_dirty_paths"] = {"value": "M src/main.zig\n M src/storage.zig\n?? /Users/PRIVATE_HOST_AND_USERNAME_SENTINEL/note"}
        result = benchmark_report.project(raw)
        self.assertEqual(result["environment"]["source_files_reported_modified"], ["src/main.zig", "src/storage.zig"])
        self.assertNotIn("PRIVATE_HOST_AND_USERNAME_SENTINEL", json.dumps(result))
        raw = fixture()
        raw["datasets"][0]["queries"][0]["expected_documents"] = ["/Users/private/note.md"]
        with self.assertRaises(ValueError):
            benchmark_report.project(raw)


if __name__ == "__main__":
    unittest.main()
