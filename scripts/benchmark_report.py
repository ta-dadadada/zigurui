#!/usr/bin/env python3
"""ローカルの測定raw JSONから、公開を許可した項目だけの要約を作る。"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path
import re


_SOURCE_PATHS = (
    "src/main.zig", "src/search.zig", "src/storage.zig",
    "build.zig", "build.zig.zon", "mise.toml",
)
_RUNNER_PATHS = ("scripts/benchmark.py", "scripts/benchmark_corpus.py")
_METRICS = ("count", "median_ms", "p95_ms", "median_peak_rss_bytes", "p95_peak_rss_bytes")


def _digest(value, length=64):
    return value if isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) else None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid nonnegative numeric metric")
    return value


def _summary(value):
    return {key: _number(value[key]) for key in _METRICS}


def _text(value):
    if not isinstance(value, str):
        raise ValueError("expected scalar text")
    return value


def _timestamp(value):
    return datetime.datetime.fromisoformat(_text(value)).isoformat()


def _version(value):
    match = re.match(r"^(\d+\.\d+\.\d+)(?:\s|$)", value or "")
    return match.group(1) if match else None


def _hardware(value):
    model = value.get("cpu_model")
    # rawの失敗診断や任意の環境変数は公開せず、実験条件の値だけを取り出す。
    if not isinstance(model, str) or any(character in model for character in ("/", "\\", "\n", "\r")):
        model = None
    memory = value.get("memory_bytes")
    if isinstance(memory, str) and memory.isdecimal():
        memory = int(memory)
    return {
        "cpu_model": model,
        "memory_bytes": _number(memory) if memory is not None else None,
        "logical_cpu_count": _number(value["logical_cpu_count"]) if value.get("logical_cpu_count") is not None else None,
    }


def _relative_document(value):
    # このベンチマークの合成文書名だけを公開する。絶対パスや未知形式は持ち出さない。
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}-(?:ja|en|mixed)\.md", value):
        raise ValueError("unexpected synthetic document path")
    return value


def project(raw):
    """未知のmetadataを既定で除外し、公開schemaに必要な値だけを投影する。"""
    if raw.get("status") != "complete":
        raise ValueError("only complete runs can be published")
    env = raw["environment"]
    config = raw["configuration"]
    os_name = env.get("os", "")
    architecture = env.get("architecture")
    result = {
        "schema_version": 1,
        "status": "complete",
        "started_at_utc": _timestamp(raw["started_at_utc"]),
        "finished_at_utc": _timestamp(raw["finished_at_utc"]),
        "configuration": {
            key: [_number(item) for item in config[key]] if key == "sizes" else _number(config[key])
            for key in ("sizes", "index_repeats", "query_repeats", "warmups_per_method_query", "seed", "timeout_seconds")
        },
        "environment": {
            "os_family": "macOS" if os_name.startswith("macOS") else "Linux" if os_name.startswith("Linux") else "unknown",
            "os_version": (match.group(1) if (match := re.match(r"^(?:macOS|Linux)-([0-9]+(?:\.[0-9]+)*)", os_name)) else None),
            "architecture": architecture if architecture in ("arm64", "aarch64", "x86_64", "AMD64", "i386", "i686") else "unknown",
            "hardware": _hardware({
                "cpu_model": env.get("cpu_model", {}).get("value"),
                "memory_bytes": env.get("memory_bytes", {}).get("value"),
                "logical_cpu_count": env.get("logical_cpu_count"),
            }),
            "python_version": _version(env.get("python")),
            "zig_version": _version(env.get("zig_version", {}).get("value")),
            "repository_head_at_measurement": _digest(env.get("git_head", {}).get("value"), 40),
            "source_files_reported_modified": [
                path for path in _SOURCE_PATHS
                if any(line.split(maxsplit=1)[-1] == path for line in str(env.get("git_dirty_paths", {}).get("value") or "").splitlines() if line.strip())
            ],
            "binary_sha256": _digest(env.get("binary_sha256")),
            "source_sha256": {path: _digest(env.get("source_sha256", {}).get(path)) for path in _SOURCE_PATHS},
            "runner_sha256": {path: _digest(env.get("runner_sha256", {}).get(path)) for path in _RUNNER_PATHS},
        },
        "queries": [],
        "datasets": [],
    }
    result["configuration"]["build_mode"] = "ReleaseFast (caller declaration)" if str(config.get("build_mode", "")).startswith("ReleaseFast") else "unknown"
    result["configuration"]["timing_scope"] = "CLI wall time including launch/wait, I/O, processing and output to DEVNULL"
    result["configuration"]["percentile_method"] = "nearest-rank: ceil(0.95*n)"
    result["configuration"]["cache_policy"] = "warm filesystem cache; no eviction"
    result["configuration"]["schedule"] = "sequential; seeded query shuffle; alternating method order"
    descriptors = None
    for dataset in raw["datasets"]:
        current_descriptors = [{
            **{key: _text(query[key]) for key in ("id", "text", "category", "language", "intent")},
            "expected_documents": [_relative_document(path) for path in query["expected_documents"]],
            "expected_scope": "handcrafted-documents-only",
        } for query in dataset["queries"]]
        # 検索語がサイズ間で違う結果を、共通条件の比較として要約しない。
        if descriptors is not None and current_descriptors != descriptors:
            raise ValueError("query definitions differ between datasets")
        descriptors = current_descriptors
        checks = {query["id"]: query for query in dataset["verification"]["query_results"]}
        compact = {
            key: _number(dataset[key]) for key in ("document_count", "corpus_bytes", "index_bytes")
        }
        compact["corpus_sha256"] = _digest(dataset["corpus_sha256"])
        compact["language_counts"] = {language: _number(dataset["language_counts"][language]) for language in ("ja", "en", "mixed")}
        compact["manifest_path"] = f"$RUN/corpus-{compact['document_count']}/manifest.json"
        compact["correctness_passed"] = dataset["correctness"]["passed"] is True
        compact["query_suitability_passed"] = dataset["query_suitability"]["passed"] is True
        compact["measurement_validity"] = {key: dataset["measurement_validity"].get(key) is True for key in ("passed", "all_commands_succeeded", "binary_unchanged", "samples_complete")}
        compact["index"] = _summary(dataset["index"]["summary"])
        compact["queries"] = []
        for query in dataset["queries"]:
            check = checks[query["id"]]
            compact["queries"].append({
                "id": query["id"],
                "expected_hit_count": _number(check["expected_hit_count"]),
                "actual_hit_count": _number(check["actual_hit_count"]),
                "verification": {key: check.get(key) is True for key in ("stdout_equal", "oracle_equal", "intent_passed")},
                "search": _summary(query["search"]["summary"]),
                "scan": _summary(query["scan"]["summary"]),
            })
        result["datasets"].append(compact)
    result["queries"] = descriptors or []
    return result


def export(raw_path: Path, output_path: Path, supplemental_hardware=None):
    """rawを変更せず要約を保存する。rawの場所は出力せず内容ハッシュで対応付ける。"""
    data = raw_path.read_bytes()
    result = project(json.loads(data))
    result["raw_result_sha256"] = hashlib.sha256(data).hexdigest()
    result["exporter_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if supplemental_hardware is not None:
        result["supplemental_hardware"] = {
            **_hardware(supplemental_hardware),
            "source": "Read separately with sysctl on the same machine; runner hardware metadata unavailable due to permissions",
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--hardware-metadata", type=Path, help="同端末で別途取得したcpu_model/memory_bytes/logical_cpu_countのJSON")
    arguments = parser.parse_args()
    if arguments.raw.resolve() == arguments.output.resolve():
        parser.error("raw and output must be different files")
    hardware = json.loads(arguments.hardware_metadata.read_text()) if arguments.hardware_metadata else None
    export(arguments.raw, arguments.output, hardware)
