#!/usr/bin/env python3
"""計測値の集計、子別RSS、失敗したコマンドの除外を検証する。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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

    def test_rss_and_cpu_usage_belong_to_the_waited_child(self):
        children = [mock.Mock(pid=41001, returncode=None), mock.Mock(pid=41002, returncode=None)]
        usages = [
            SimpleNamespace(ru_maxrss=80000, ru_utime=0.5, ru_stime=0.2),
            SimpleNamespace(ru_maxrss=12000, ru_utime=0.1, ru_stime=0.05),
        ]
        # 値が小さくなる2件目も、そのPIDのrusageを使い、過去の最大値を引き継がない。
        with mock.patch.object(runner.subprocess, "Popen", side_effect=children), \
                mock.patch.object(runner.os, "wait4", side_effect=[(child.pid, 0, usage) for child, usage in zip(children, usages)]) as wait, \
                mock.patch.object(runner.platform, "system", return_value="Linux"):
            samples = [runner.measure(["unused-test-command"], 10) for _ in children]
        self.assertEqual(wait.call_args_list, [mock.call(41001, 0), mock.call(41002, 0)])
        for sample, usage in zip(samples, usages):
            self.assertEqual(sample["peak_rss_bytes"], usage.ru_maxrss * 1024)
            self.assertEqual(sample["user_seconds"], usage.ru_utime)
            self.assertEqual(sample["system_seconds"], usage.ru_stime)

    @unittest.skipUnless(platform.system() == "Linux", "Linuxのwait4とRUSAGE_SELFを照合する")
    def test_linux_rss_matches_child_self_report_with_parent_memory_pressure(self):
        # exec前の親の履歴がru_maxrssの下限になる条件を作る。子間の固定差は仮定しない。
        parent_memory = bytearray(64 * 1024 * 1024)
        for offset in range(0, len(parent_memory), os.sysconf("SC_PAGE_SIZE")):
            parent_memory[offset] = 1
        code = """import os, resource, struct, sys
report = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
buffer = bytearray(8)
pack = struct.Struct('=Q').pack_into
get_usage = resource.getrusage
write = os.write
exit_process = os._exit
data = bytearray(int(sys.argv[2]) * 1024 * 1024)
for offset in range(0, len(data), os.sysconf('SC_PAGE_SIZE')):
    data[offset] = 1
get_usage(resource.RUSAGE_SELF)
pack(buffer, 0, get_usage(resource.RUSAGE_SELF).ru_maxrss)
write(report, buffer)
exit_process(0)
"""
        with tempfile.TemporaryDirectory() as directory:
            for index, allocation_mib in enumerate((32, 0)):
                report = Path(directory) / f"child-{index}.rss"
                sample = runner.measure([sys.executable, "-c", code, str(report), str(allocation_mib)], 10)
                report_bytes = report.read_bytes()
                self.assertEqual(len(report_bytes), 8)
                child_peak_kib = int.from_bytes(report_bytes, sys.byteorder)
                self.assertGreater(child_peak_kib, 0)
                self.assertEqual(sample["peak_rss_bytes"], child_peak_kib * 1024)

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
