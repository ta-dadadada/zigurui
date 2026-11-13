#!/usr/bin/env python3
"""計測値の集計、子別RSS、失敗したコマンドの除外を検証する。"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
SPEC = importlib.util.spec_from_file_location("benchmark_runner", Path(__file__).with_name("benchmark.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class BenchmarkTest(unittest.TestCase):
    def test_nearest_rank_p95_and_median(self):
        samples = [{"elapsed_ms": n, "peak_rss_bytes": n * 1000} for n in range(1, 31)]
        result = runner.summarize(samples)
        self.assertEqual(result["count"], 30)
        self.assertEqual(result["median_ms"], 15.5)
        self.assertEqual(result["p95_ms"], 29)
        self.assertEqual(result["p95_peak_rss_bytes"], 29000)
        with self.assertRaises(ValueError):
            runner.summarize([])

    def test_rss_platform_units(self):
        self.assertEqual(runner.rss_bytes(100, "Darwin"), 100)
        self.assertEqual(runner.rss_bytes(100, "Linux"), 102400)
        with self.assertRaises(RuntimeError):
            runner.rss_bytes(100, "Other")

    def test_large_output_has_no_pipe_deadlock(self):
        sample = runner.measure([sys.executable, "-c", "import sys;sys.stdout.write('x'*1000000);sys.stderr.write('x'*1000000)"], 10)
        self.assertGreater(sample["peak_rss_bytes"], 0)
        self.assertGreater(sample["elapsed_ms"], 0)

    def test_rss_belongs_to_each_child_not_previous_peak(self):
        large = runner.measure([sys.executable, "-c", "data=bytearray(32*1024*1024)"], 10)
        small = runner.measure([sys.executable, "-c", "pass"], 10)
        self.assertGreater(large["peak_rss_bytes"] - small["peak_rss_bytes"], 16 * 1024 * 1024)

    def test_failed_command_is_not_a_sample(self):
        with self.assertRaises(runner.MeasurementError) as failure:
            runner.measure([sys.executable, "-c", "import sys;sys.stderr.write('intentional');sys.exit(7)"], 10)
        self.assertEqual(failure.exception.detail["returncode"], 7)
        self.assertEqual(failure.exception.detail["stderr"], "intentional")

    def test_timeout_is_not_a_sample(self):
        with self.assertRaises(runner.MeasurementError) as failure:
            runner.measure([sys.executable, "-c", "import time;time.sleep(10)"], 0.05)
        self.assertIn("timeout_seconds", failure.exception.detail)

    def test_metadata_failure_records_failure_json(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(binary=Path(directory) / "missing", output_root=Path(directory), run_id="missing-binary", sizes=[100], index_repeats=1, repeats=1, warmups=0, seed=1, timeout=1)
            with self.assertRaises(FileNotFoundError):
                runner.benchmark(args)
            result = json.loads((Path(directory) / "missing-binary/results.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure"]["type"], "FileNotFoundError")
            self.assertEqual(result["datasets"], [])

    def test_failed_run_records_failure_without_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(binary=Path(sys.executable), output_root=Path(directory), run_id="failed-run", sizes=[100], index_repeats=1, repeats=1, warmups=0, seed=1, timeout=1)
            manifest = {"total_bytes": 100, "corpus_sha256": "hash", "language_counts": {}}
            with mock.patch.object(runner, "environment", return_value={}), mock.patch.object(runner.benchmark_corpus, "prepare", return_value=manifest), mock.patch.object(runner, "measure", side_effect=runner.MeasurementError("failed", {"returncode": 2})):
                with self.assertRaises(runner.MeasurementError):
                    runner.benchmark(args)
            result = json.loads((Path(directory) / "failed-run/results.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["datasets"][0]["index"]["samples"], [])
            self.assertNotIn("summary", result["datasets"][0]["index"])
            self.assertFalse(result["datasets"][0]["measurement_validity"]["passed"])


if __name__ == "__main__":
    unittest.main()
