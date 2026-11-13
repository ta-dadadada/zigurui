#!/usr/bin/env python3
"""合成文書に対するCLI全体の時間と子プロセス別peak RSSを逐次計測する。"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import time

import benchmark_corpus

REPOSITORY = Path(__file__).resolve().parent.parent


class MeasurementError(RuntimeError):
    """失敗した計測は成功サンプルに混ぜず、診断情報として保存する。"""

    def __init__(self, message, detail):
        super().__init__(message)
        self.detail = detail


def rss_bytes(value, system=None):
    system = system or platform.system()
    if system == "Darwin":
        return int(value)
    if system == "Linux":
        return int(value * 1024)
    raise RuntimeError(f"unsupported ru_maxrss unit on {system}")


def summarize(samples):
    if not samples:
        raise ValueError("cannot summarize empty samples")
    times = sorted(sample["elapsed_ms"] for sample in samples)
    rss = sorted(sample["peak_rss_bytes"] for sample in samples)
    rank = math.ceil(0.95 * len(samples)) - 1
    return {
        "count": len(samples),
        "median_ms": statistics.median(times),
        "p95_ms": times[rank],
        "median_peak_rss_bytes": statistics.median(rss),
        "p95_peak_rss_bytes": rss[rank],
    }


def measure(command, timeout_seconds=120):
    """stdoutを捨ててpipe待ちを避け、wait4の同一子プロセスのrusageを記録する。"""
    if not hasattr(os, "wait4") or platform.system() not in ("Darwin", "Linux"):
        raise RuntimeError("measurement requires Darwin/Linux os.wait4")
    command = list(map(str, command))
    with tempfile.TemporaryFile() as stderr:
        def timeout_handler(_signum, _frame):
            raise TimeoutError("child process timed out")

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        old_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        child = None
        started = time.perf_counter_ns()
        try:
            child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr)
            _, status, usage = os.wait4(child.pid, 0)
            finished = time.perf_counter_ns()
            child.returncode = os.waitstatus_to_exitcode(status)
        except BaseException as error:
            if child is not None and child.returncode is None:
                try:
                    os.kill(child.pid, signal.SIGKILL)
                    _, status, _ = os.wait4(child.pid, 0)
                    child.returncode = os.waitstatus_to_exitcode(status)
                except ProcessLookupError:
                    pass
            if isinstance(error, TimeoutError):
                raise MeasurementError("command timed out", {"command": command, "timeout_seconds": timeout_seconds}) from error
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
        stderr.seek(0)
        diagnostic = stderr.read(16384).decode("utf-8", errors="replace")
        if child.returncode != 0:
            raise MeasurementError("command failed", {"command": command, "returncode": child.returncode, "stderr": diagnostic})
        return {
            "command": command,
            "elapsed_ms": (finished - started) / 1_000_000,
            "peak_rss_bytes": rss_bytes(usage.ru_maxrss),
            "user_seconds": usage.ru_utime,
            "system_seconds": usage.ru_stime,
        }


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capture(command):
    result = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True)
    return {"value": result.stdout.strip() if result.returncode == 0 else None,
            "error": result.stderr.strip() if result.returncode else None}


def environment(binary):
    source_paths = [*sorted((REPOSITORY / "src").glob("*.zig")), REPOSITORY / "build.zig", REPOSITORY / "build.zig.zon", REPOSITORY / "mise.toml"]
    runner_paths = [Path(__file__).resolve(), Path(benchmark_corpus.__file__).resolve()]
    memory = capture(["sysctl", "-n", "hw.memsize"]) if platform.system() == "Darwin" else {"value": None, "error": "not collected"}
    cpu = capture(["sysctl", "-n", "machdep.cpu.brand_string"]) if platform.system() == "Darwin" else {"value": None, "error": "not collected"}
    return {
        "os": platform.platform(), "architecture": platform.machine(),
        "cpu_model": cpu, "logical_cpu_count": os.cpu_count(), "memory_bytes": memory,
        "python": sys.version, "zig_version": capture(["mise", "exec", "--", "zig", "version"]),
        "git_head": capture(["git", "rev-parse", "HEAD"]),
        "git_dirty_paths": capture(["git", "status", "--porcelain=v1", "--untracked-files=all"]),
        "binary_path": str(binary), "binary_sha256": file_hash(binary),
        "source_sha256": {str(p.relative_to(REPOSITORY)): file_hash(p) for p in source_paths},
        "runner_sha256": {str(p.relative_to(REPOSITORY)): file_hash(p) for p in runner_paths},
        "ru_maxrss_input_unit": "bytes" if platform.system() == "Darwin" else "KiB",
    }


def save(path, result):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def benchmark(args):
    run_id = args.run_id or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    if Path(run_id).name != run_id or run_id in (".", ".."):
        raise ValueError("run-id must be a single directory name")
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    result_path = run_dir / "results.json"
    result = {
        "schema_version": 1, "status": "running",
        "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "configuration": {
            "sizes": args.sizes, "index_repeats": args.index_repeats,
            "query_repeats": args.repeats, "warmups_per_method_query": args.warmups,
            "seed": args.seed, "timeout_seconds": args.timeout,
            "build_mode": "ReleaseFast (caller-built binary)",
            "timing_scope": "CLI wall time: process startup, I/O, search/build, formatting and output to DEVNULL; parent launch/wait overhead included",
            "percentile_method": "nearest-rank: sorted[ceil(0.95*n)-1]",
            "schedule": "sequential; query order shuffled per round with fixed seed; search/scan order alternates by round and query",
            "cache_policy": "warm filesystem cache; no cache eviction; correctness verification precedes timed queries",
            "invocation": [sys.executable, *sys.argv],
        },
        "environment": None, "datasets": [],
    }
    save(result_path, result)
    order = 0
    rng = random.Random(args.seed)
    try:
        result["environment"] = environment(binary)
        save(result_path, result)
        for size in args.sizes:
            print(f"preparing {size} documents", flush=True)
            corpus_root = run_dir / f"corpus-{size}"
            manifest = benchmark_corpus.prepare(corpus_root, size, args.seed)
            index_path = corpus_root / "index.zgi"
            dataset = {
                "document_count": size, "corpus_bytes": manifest["total_bytes"],
                "corpus_sha256": manifest["corpus_sha256"],
                "manifest_path": str(corpus_root / "manifest.json"),
                "language_counts": manifest["language_counts"],
                "index": {"samples": []}, "queries": [],
                "correctness": {"passed": False, "reason": "not checked"},
                "measurement_validity": {"passed": False, "reason": "measurement incomplete"},
            }
            result["datasets"].append(dataset)
            for repeat in range(args.index_repeats):
                sample = measure([binary, "index", corpus_root / "documents", index_path], args.timeout)
                sample.update(order=order, repeat=repeat)
                order += 1
                dataset["index"]["samples"].append(sample)
                save(result_path, result)
            dataset["index"]["summary"] = summarize(dataset["index"]["samples"])
            dataset["index_bytes"] = index_path.stat().st_size
            verification = benchmark_corpus.verify(binary, corpus_root, index_path, manifest)
            dataset["verification"] = verification
            dataset["correctness"] = {
                "passed": verification["corpus_integrity_passed"] and all(q["stdout_equal"] and q["oracle_equal"] for q in verification["query_results"]),
                "method": "search/scan exact stdout equality and independent literal oracle path/line equality",
            }
            dataset["query_suitability"] = {
                "passed": all(q["intent_passed"] for q in verification["query_results"]),
                "scope": verification["quality_scope"],
                "method": "handcrafted documents matched against query intent and explicit expected documents; ranking not evaluated",
            }
            if not verification["passed"]:
                raise MeasurementError("correctness verification failed", {"document_count": size})
            queries = manifest["queries"]
            dataset["queries"] = [{**query, "search": {"warmups": [], "samples": []}, "scan": {"warmups": [], "samples": []}} for query in queries]
            for phase, repeat_count in (("warmups", args.warmups), ("samples", args.repeats)):
                for repeat in range(repeat_count):
                    positions = list(range(len(queries)))
                    rng.shuffle(positions)
                    for position in positions:
                        query = dataset["queries"][position]
                        methods = ("search", "scan") if (repeat + position) % 2 == 0 else ("scan", "search")
                        for method in methods:
                            target = index_path if method == "search" else corpus_root / "documents"
                            sample = measure([binary, method, target, query["text"]], args.timeout)
                            sample.update(order=order, repeat=repeat)
                            order += 1
                            query[method][phase].append(sample)
                    save(result_path, result)
            for query in dataset["queries"]:
                for method in ("search", "scan"):
                    query[method]["summary"] = summarize(query[method]["samples"])
            dataset["measurement_validity"] = {
                "passed": True, "all_commands_succeeded": True,
                "binary_unchanged": file_hash(binary) == result["environment"]["binary_sha256"],
                "samples_complete": all(len(q[m]["samples"]) == args.repeats for q in dataset["queries"] for m in ("search", "scan")),
                "limitations": ["warm cache and CLI-wide measurements", "peak RSS is a process high-water mark, not isolated index allocation", "machine load is not controlled"],
            }
            dataset["measurement_validity"]["passed"] = dataset["measurement_validity"]["binary_unchanged"] and dataset["measurement_validity"]["samples_complete"]
            print(f"finished {size} documents", flush=True)
            save(result_path, result)
        result["status"] = "complete"
        if not all(d["measurement_validity"]["passed"] for d in result["datasets"]):
            result["status"] = "invalid"
    except BaseException as error:
        result["status"] = "failed"
        result["failure"] = {"type": type(error).__name__, "message": str(error), "detail": getattr(error, "detail", None)}
        raise
    finally:
        result["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(result_path, result)
        print(result_path, flush=True)
    return result_path


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=REPOSITORY / ".local/benchmarks/release/bin/zigurui")
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / ".local/benchmarks")
    parser.add_argument("--run-id")
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--index-repeats", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20261114)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if min(args.sizes) < 6 or len(args.sizes) != len(set(args.sizes)) or args.index_repeats < 1 or args.repeats < 1 or args.warmups < 0 or args.timeout <= 0:
        parser.error("sizes must be >=6 and unique; repeats/timeout must be positive; warmups must be nonnegative")
    return args


if __name__ == "__main__":
    benchmark(arguments())
