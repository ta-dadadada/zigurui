#!/usr/bin/env python3
"""合成文書で正規化検索の内訳と無計測CLIを比較する。生成物は削除しない。"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import statistics
import subprocess
import sys
import time
import unicodedata

import benchmark
import benchmark_corpus

REPOSITORY = Path(__file__).resolve().parent.parent
PHASES = (
    "read", "checksum", "decode", "query_prepare", "candidates", "span_transform",
    "index_of", "original_span", "output", "cleanup", "other",
)
COUNTS = (
    "shortest_posting_count", "candidate_count", "documents_verified", "hit_count",
    "normalized_document_count", "normalized_input_bytes", "span_points_visited",
)
EXPECTED_COUNTS = ("shortest_posting_count", "candidate_count", "documents_verified", "hit_count")
SUMMARY_FIELDS = ("count", "median_ms", "p95_ms", "median_peak_rss_bytes", "p95_peak_rss_bytes")
QUERIES = (
    {"id": "rare", "text": "希少ガラス記録", "category": "rare"},
    {"id": "broad-highhit", "text": "ガラス", "category": "broad-highhit"},
    {"id": "broad-lowhit", "text": "東京駅前", "category": "broad-lowhit"},
    {"id": "one-highhit", "text": "ガ", "category": "one-codepoint-highhit"},
    {"id": "one-lowhit", "text": "希", "category": "one-codepoint-lowhit"},
    {"id": "missing", "text": "ZZZ不存在98765", "category": "no-hit"},
)
CONFIGURATIONS = (
    {"id": "literal", "index_mode": "literal", "query_mode": "literal"},
    {"id": "normalized", "index_mode": "normalized", "query_mode": "normalized"},
    {"id": "normalized-index-literal", "index_mode": "normalized", "query_mode": "literal"},
)


def transform(text, mode):
    if mode == "literal":
        return text
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())


def corpus(root, size, seed):
    """既存の生成器を使い、新規文書だけに選択性を固定した検証行を加える。"""
    manifest = benchmark_corpus.prepare(root, size, seed)
    texts = {}
    digest = hashlib.sha256()
    for number, document in enumerate(manifest["documents"]):
        path = root / "documents" / document["path"]
        text = path.read_text(encoding="utf-8") + "\nガラス ｶﾞﾗｽ\n東京と京駅と駅前\n"
        if number == 0:
            text += "希少ｶﾞﾗｽ記録 Ω\n"
        data = text.encode("utf-8")
        path.write_bytes(data)
        texts[document["path"]] = text
        document.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        digest.update(document["path"].encode("utf-8") + b"\x00" + data + b"\x00")
    manifest.update(
        generator="zigurui-normalized-synthetic-v1", total_bytes=sum(d["bytes"] for d in manifest["documents"]),
        corpus_sha256=digest.hexdigest(), queries=list(QUERIES),
        quality_scope="固定した選択性と連続部分一致の正しさ。実ノートの品質やランキングは評価しない。",
    )
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest, texts


def expected_hits(texts, query, mode):
    """検索語は改行を含まない。原文の各行を変換し、原文の行番号を返す。"""
    if "\n" in query:
        raise ValueError("the performance oracle requires a query without newlines")
    needle = transform(query, mode)
    if not needle:
        raise ValueError("empty transformed query")
    hits = []
    for path, text in sorted(texts.items()):
        for line_number, line in enumerate(text.split("\n"), 1):
            if needle in transform(line, mode):
                hits.append((path, line_number))
                break
    return hits


def expected_counts(texts, query, configuration, method):
    mode = configuration["query_mode"]
    needle = transform(query, mode)
    transformed = {path: transform(text, mode) for path, text in texts.items()}
    scan = method == "scan" or configuration["index_mode"] != mode or len(needle) == 1
    grams = {needle[i:i + 2] for i in range(len(needle) - 1)}
    if scan:
        seed_count, candidate_count = 0, 0
        verified = set(texts)
    else:
        postings = [{path for path, text in transformed.items() if gram in text} for gram in grams]
        seed_count = min(map(len, postings))
        verified = set.intersection(*postings)
        candidate_count = len(verified)
    return {
        "shortest_posting_count": seed_count,
        "candidate_count": candidate_count,
        "documents_verified": len(verified),
        "hit_count": len(expected_hits(texts, query, mode)),
    }


def parse_hits(stdout, documents):
    hits = []
    for line in stdout.decode("utf-8").splitlines():
        match = re.fullmatch(r"(.*):([0-9]+): .*", line)
        if not match:
            raise ValueError("invalid CLI result format")
        hits.append((str(Path(match[1]).relative_to(documents)), int(match[2])))
    return hits


def parse_profile(stderr, command, mode):
    rows = stderr.decode("utf-8").splitlines()
    if len(rows) != 1:
        raise ValueError("profile output must contain exactly one JSON line")
    profile = json.loads(rows[0])
    if any(profile.get(key) != expected for key, expected in (
        ("schema_version", 1), ("profile", True), ("command", command), ("mode", mode), ("status", "ok"),
    )):
        raise ValueError("unexpected profile metadata")
    for category, names in (("timings_ns", PHASES), ("counts", COUNTS)):
        if set(profile[category]) != set(names):
            raise ValueError(f"unexpected {category} keys")
        if any(type(profile[category][name]) is not int or profile[category][name] < 0 for name in names):
            raise ValueError(f"invalid {category} value")
    if type(profile["total_ns"]) is not int or profile["total_ns"] < 0:
        raise ValueError("invalid total_ns")
    if sum(profile["timings_ns"].values()) > profile["total_ns"]:
        raise ValueError("profile phases overlap")
    return profile


def validate_counts(profile, expected):
    for name, count in expected.items():
        if profile["counts"][name] != count:
            raise ValueError(f"unexpected {name}: expected {count}, got {profile['counts'][name]}")


def execute(command, stderr_path, timeout, capture=False, env=None):
    """既存runnerと同じwait4計測。診断と計測JSONを新規ファイルに保持する。"""
    if not hasattr(os, "wait4"):
        raise RuntimeError("os.wait4 is required")
    command = list(map(str, command))
    stdout_path = stderr_path.with_suffix(".stdout") if capture else None
    with stderr_path.open("xb+") as stderr:
        stdout = stdout_path.open("xb+") if capture else subprocess.DEVNULL
        old_handler = signal.getsignal(signal.SIGALRM)

        def timed_out(_signum, _frame):
            raise TimeoutError("command timed out")

        signal.signal(signal.SIGALRM, timed_out)
        old_timer = signal.setitimer(signal.ITIMER_REAL, timeout)
        child = None
        started = time.perf_counter_ns()
        try:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
            _, status, usage = os.wait4(child.pid, 0)
            finished = time.perf_counter_ns()
            child.returncode = os.waitstatus_to_exitcode(status)
        except BaseException:
            if child is not None and child.returncode is None:
                try:
                    child.kill()
                    _, status, _ = os.wait4(child.pid, 0)
                    child.returncode = os.waitstatus_to_exitcode(status)
                except ProcessLookupError:
                    pass
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
            if capture:
                stdout.close()
        stderr.seek(0)
        diagnostic = stderr.read()
        if child.returncode:
            raise benchmark.MeasurementError("command failed", {"command": command, "returncode": child.returncode, "stderr": diagnostic.decode("utf-8", errors="replace")})
    sample = {
        "elapsed_ms": (finished - started) / 1_000_000,
        "peak_rss_bytes": benchmark.rss_bytes(usage.ru_maxrss),
        "user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime,
        "command": command, "stderr_path": str(stderr_path),
    }
    return sample, diagnostic, stdout_path.read_bytes() if capture else None


def verify_positions(run_dir, variants, invoke):
    documents = run_dir / "positions"
    documents.mkdir()
    fixtures = {
        "expansion.md": "前㌔後\n", "halfwidth.md": "前ｶﾞ後\n",
        "reorder.md": "a\u0315\u0300\n", "multiline.md": "見出し\n前ｶﾞ後\n",
    }
    for path, text in fixtures.items():
        (documents / path).write_text(text, encoding="utf-8")
    cases = (
        ("ロ", [("expansion.md", 1, "前㌔後")]),
        ("ガ", [("halfwidth.md", 1, "前ｶﾞ後"), ("multiline.md", 2, "前ｶﾞ後")]),
        ("à", [("reorder.md", 1, "a\u0315\u0300")]),
        ("\u0315", [("reorder.md", 1, "a\u0315\u0300")]),
    )
    for variant in variants:
        index = run_dir / "indices" / variant["name"] / "positions.zgi"
        invoke(variant["plain"], "index", "normalized", documents, index, capture=True)
        for query, expected in cases:
            wanted = "".join(f"{documents / path}:{line}: {excerpt}\n" for path, line, excerpt in expected).encode("utf-8")
            for method, target in (("search", index), ("scan", documents)):
                _, _, actual = invoke(variant["plain"], method, "normalized", target, query, capture=True)
                if actual != wanted:
                    raise ValueError("handwritten original-line/excerpt fixture failed")
    return {
        "passed": True, "cases": len(cases),
        "scope": "CLI original line and excerpt. Byte spans are separately covered by the existing Zig unit tests.",
        "reference_byte_spans": [
            {"document": "expansion.md", "query": "ロ", "start": 3, "end": 6},
            {"document": "halfwidth.md", "query": "ガ", "start": 3, "end": 9},
            {"document": "reorder.md", "query": "à", "start": 0, "end": 5},
            {"document": "reorder.md", "query": "\u0315", "start": 1, "end": 3},
        ],
    }


def index_identity(indices, required):
    """同一コーパス・modeの保存結果を比較する。パスを記録に含めない。"""
    groups = []
    for mode in ("literal", "normalized"):
        items = [item for item in indices if item["mode"] == mode]
        identical = len({(item["format_version"], item["index_bytes"], item["index_sha256"]) for item in items}) == 1
        group = {
            "mode": mode, "identical": identical,
            "indices": [{key: item[key] for key in ("variant_id", "instrumentation", "format_version", "index_bytes", "index_sha256")} for item in items],
        }
        groups.append(group)
        if required and not identical:
            raise ValueError(f"{mode} index bytes differ between variants or instrumentation")
    return {"required": required, "groups": groups}


def verify_cross_read(variants, index_paths, indices, texts, documents, invoke):
    """同形式の別variantが作成した索引を両方向に読み、独立oracleへ照合する。"""
    versions = {(item["variant_id"], item["mode"]): item["format_version"] for item in indices if item["instrumentation"] == "plain"}
    cases = []
    skipped = []
    own_outputs = {}
    for source_id, source in enumerate(variants):
        for target_id, target in enumerate(variants):
            if source_id == target_id:
                continue
            for configuration in CONFIGURATIONS:
                index_mode = configuration["index_mode"]
                if versions[source_id, index_mode] != versions[target_id, index_mode]:
                    skipped.append({"source_variant_id": source_id, "target_variant_id": target_id, "configuration_id": configuration["id"], "reason": "different-format-version"})
                    continue
                for query in QUERIES:
                    index = index_paths[source["name"], index_mode, "plain"]
                    _, _, stdout = invoke(target["plain"], "search", configuration["query_mode"], index, query["text"], capture=True)
                    if parse_hits(stdout, documents) != expected_hits(texts, query["text"], configuration["query_mode"]):
                        raise ValueError("cross-variant index read differs from independent oracle")
                    own_key = target_id, configuration["id"], query["id"]
                    if own_key not in own_outputs:
                        own_index = index_paths[target["name"], index_mode, "plain"]
                        _, _, own_outputs[own_key] = invoke(target["plain"], "search", configuration["query_mode"], own_index, query["text"], capture=True)
                    if stdout != own_outputs[own_key]:
                        raise ValueError("cross-variant index read changes full CLI output")
                    cases.append({"source_variant_id": source_id, "target_variant_id": target_id,
                                  "configuration_id": configuration["id"], "query_id": query["id"], "passed": True})
    return {"passed": True, "cases": cases, "skipped": skipped}


def numeric_summary(values):
    ordered = sorted(values)
    return {"median": statistics.median(ordered), "p95": ordered[math.ceil(0.95 * len(ordered)) - 1]}


def project(raw):
    """公開用は固定されたフィールドだけを投影し、端末・パス・自由文を含めない。"""
    if raw["status"] != "complete":
        raise ValueError("only complete runs can be projected")
    output = {
        "schema_version": 1, "status": "complete", "scope": "synthetic-smoke",
        "performance_claim": "No performance guarantee: host-wide load was not controlled.",
        "configuration": {name: raw["configuration"][name] for name in ("document_count", "seed", "index_repeats", "repeats", "warmups")},
        "corpus_sha256": raw["corpus"]["corpus_sha256"], "corpus_bytes": raw["corpus"]["total_bytes"],
        "queries": [{key: query[key] for key in ("id", "text", "category")} for query in QUERIES],
        "correctness_passed": raw["verification"]["passed"], "position_excerpt_passed": raw["positions"]["passed"],
        "binary_unchanged": raw["binary_unchanged"], "variants": [], "results": [],
    }
    output["index_identity"] = {
        "required": raw["index_identity"]["required"],
        "groups": [{"mode": group["mode"], "identical": group["identical"], "indices": [
            {key: item[key] for key in ("variant_id", "instrumentation", "format_version", "index_bytes", "index_sha256")}
            for item in group["indices"]
        ]} for group in raw["index_identity"]["groups"]],
    }
    output["cross_read"] = {
        "passed": raw["cross_read"]["passed"],
        "cases": [{key: case[key] for key in ("source_variant_id", "target_variant_id", "configuration_id", "query_id", "passed")} for case in raw["cross_read"]["cases"]],
        "skipped": [{key: case[key] for key in ("source_variant_id", "target_variant_id", "configuration_id", "reason")} for case in raw["cross_read"]["skipped"]],
    }
    for variant in raw["variants"]:
        output["variants"].append({key: variant[key] for key in ("id", "plain_sha256", "profile_sha256", "provenance_sha256")})
    for record in raw["results"]:
        item = {key: record[key] for key in ("variant_id", "configuration_id", "query_id", "method")}
        item["expected_counts"] = {key: record["expected_counts"][key] for key in EXPECTED_COUNTS}
        item["plain"] = benchmark.summarize(record["plain"]["samples"])
        item["profile"] = benchmark.summarize(record["profile"]["samples"])
        item["profile_timings_ns"] = {phase: numeric_summary([sample["profile"]["timings_ns"][phase] for sample in record["profile"]["samples"]]) for phase in PHASES}
        item["profile_total_ns"] = numeric_summary([sample["profile"]["total_ns"] for sample in record["profile"]["samples"]])
        item["profile_counts"] = {name: numeric_summary([sample["profile"]["counts"][name] for sample in record["profile"]["samples"]]) for name in COUNTS}
        output["results"].append(item)
    output["indices"] = [{
        **{key: item[key] for key in ("variant_id", "mode", "instrumentation", "format_version", "index_bytes", "index_sha256")},
        "summary": {key: item["summary"][key] for key in SUMMARY_FIELDS},
    } for item in raw["indices"]]
    encoded = json.dumps(output, ensure_ascii=False)
    if any(marker in encoded for marker in ("/Users/", "/home/", "/private/", "MacBook", "cpu_model", "hostname", "architecture")):
        raise ValueError("private marker in public projection")
    return output


def run(args):
    run_dir = args.output_root.resolve() / args.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "diagnostics").mkdir()
    (run_dir / "indices").mkdir()
    result_path = run_dir / "results.json"
    variants = [{"name": name, "plain": Path(plain).resolve(), "profile": Path(profile).resolve()} for name, plain, profile in args.variant]
    provenance = dict(item.split("=", 1) for item in args.provenance)
    raw = {
        "schema_version": 1, "status": "running", "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "configuration": {"document_count": args.size, "seed": args.seed, "index_repeats": args.index_repeats, "repeats": args.repeats, "warmups": args.warmups},
        "measurement_scope": "synthetic-smoke; CLI wall includes launch/I/O/output; warm cache; no host-wide load control; profile phases are separate from uninstrumented CLI samples",
        "environment": {"python": sys.version, "python_unicode_version": unicodedata.unidata_version, "current_checkout": benchmark.environment(variants[0]["plain"])},
        "variants": [], "indices": [], "verification": {"passed": False}, "results": [],
    }
    source_paths = [*sorted((REPOSITORY / "src").glob("*.zig")), *sorted((REPOSITORY / "src").glob("*.c")),
                    *sorted((REPOSITORY / "vendor").rglob("*.c")), *sorted((REPOSITORY / "vendor").rglob("*.h")),
                    *sorted((REPOSITORY / "testdata").rglob("*.txt")), REPOSITORY / "build.zig", REPOSITORY / "build.zig.zon", REPOSITORY / "mise.toml"]
    raw["environment"]["current_source_sha256"] = {str(path.relative_to(REPOSITORY)): benchmark.file_hash(path) for path in source_paths}
    raw["environment"]["runner_sha256"] = {str(path.relative_to(REPOSITORY)): benchmark.file_hash(path) for path in (Path(__file__), Path(benchmark.__file__), Path(benchmark_corpus.__file__))}
    order = 0

    def invoke(binary, command, mode, target, value, capture=False):
        nonlocal order
        path = run_dir / "diagnostics" / f"{order:06d}.stderr"
        sample, diagnostic, stdout = execute([binary, command, "--mode", mode, target, value], path, args.timeout, capture)
        sample["order"] = order
        order += 1
        return sample, diagnostic, stdout

    def save():
        # このrun自身が作成した結果だけを更新する。元データと既存runは触らない。
        result_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        for variant_id, variant in enumerate(variants):
            (run_dir / "indices" / variant["name"]).mkdir()
            provenance_path = Path(provenance[variant["name"]]) if variant["name"] in provenance else None
            raw["variants"].append({
                "id": variant_id, "name": variant["name"], "plain": str(variant["plain"]), "profile": str(variant["profile"]),
                "plain_sha256": benchmark.file_hash(variant["plain"]), "profile_sha256": benchmark.file_hash(variant["profile"]),
                "provenance": json.loads(provenance_path.read_text()) if provenance_path else None,
                "provenance_sha256": benchmark.file_hash(provenance_path) if provenance_path else None,
            })
        manifest, texts = corpus(run_dir / "corpus", args.size, args.seed)
        raw["corpus"] = manifest
        documents = run_dir / "corpus" / "documents"
        index_paths = {}
        for variant_id, variant in enumerate(variants):
            for mode in ("literal", "normalized"):
                for instrumentation in ("plain", "profile"):
                    samples = []
                    for repeat in range(args.index_repeats):
                        path = run_dir / "indices" / variant["name"] / f"{mode}-{instrumentation}-{repeat}.zgi"
                        sample, diagnostic, _ = invoke(variant[instrumentation], "index", mode, documents, path)
                        if instrumentation == "profile":
                            sample["profile"] = parse_profile(diagnostic, "index", mode)
                        samples.append(sample)
                    header = path.read_bytes()[:12]
                    if header[:8] != b"ZIGURUI\x00":
                        raise ValueError("unexpected index header")
                    raw["indices"].append({"variant_id": variant_id, "mode": mode, "instrumentation": instrumentation,
                                           "samples": samples, "summary": benchmark.summarize(samples), "index_bytes": path.stat().st_size,
                                           "format_version": int.from_bytes(header[8:12], "little"), "index_sha256": benchmark.file_hash(path)})
                    index_paths[variant["name"], mode, instrumentation] = path
        raw["index_identity"] = index_identity(raw["indices"], args.require_index_identity)
        raw["cross_read"] = verify_cross_read(variants, index_paths, raw["indices"], texts, documents, invoke)
        raw["positions"] = verify_positions(run_dir, variants, invoke)
        verification_cases = []
        extra_queries = [{"id": "short-after-normalization", "text": "ｶﾞ"}, {"id": "folded-one", "text": "ω"}]
        regression_queries = [{"id": "regression-" + item["id"], "text": item["text"]} for item in benchmark_corpus.queries()]
        all_queries = [*QUERIES, *extra_queries, *regression_queries]
        for configuration in CONFIGURATIONS:
            for query in all_queries:
                expected = expected_hits(texts, query["text"], configuration["query_mode"])
                first_stdout = None
                for variant in variants:
                    for instrumentation in ("plain", "profile"):
                        method_outputs = []
                        for method in ("search", "scan"):
                            target = index_paths[variant["name"], configuration["index_mode"], instrumentation] if method == "search" else documents
                            _, diagnostic, stdout = invoke(variant[instrumentation], method, configuration["query_mode"], target, query["text"], capture=True)
                            if parse_hits(stdout, documents) != expected:
                                raise ValueError(f"independent oracle mismatch: {query['id']}")
                            if instrumentation == "profile":
                                profile = parse_profile(diagnostic, method, configuration["query_mode"])
                                validate_counts(profile, expected_counts(texts, query["text"], configuration, method))
                            method_outputs.append(stdout)
                        if method_outputs[0] != method_outputs[1]:
                            raise ValueError("search/scan stdout mismatch")
                        if first_stdout is None:
                            first_stdout = method_outputs[0]
                        elif first_stdout != method_outputs[0]:
                            raise ValueError("variant or instrumentation changes stdout")
                verification_cases.append({"configuration_id": configuration["id"], "query_id": query["id"], "hit_count": len(expected), "passed": True})
        raw["verification"] = {"passed": True, "cases": verification_cases,
                               "method": "independent per-original-line Python NFKC/casefold/NFKC oracle, full stdout equality across search/scan/variant/instrumentation, and independent gram/candidate counts"}
        for variant_id, variant in enumerate(variants):
            for configuration in CONFIGURATIONS:
                for query in QUERIES:
                    for method in ("search", "scan"):
                        raw["results"].append({"variant_id": variant_id, "variant_name": variant["name"], "configuration_id": configuration["id"],
                                               "query_id": query["id"], "method": method,
                                               "expected_counts": expected_counts(texts, query["text"], configuration, method),
                                               "plain": {"warmups": [], "samples": []}, "profile": {"warmups": [], "samples": []}})
        by_id = {(record["variant_name"], record["configuration_id"], record["query_id"], record["method"]): record for record in raw["results"]}
        rng = random.Random(args.seed)
        for phase, repeats in (("warmups", args.warmups), ("samples", args.repeats)):
            for repeat in range(repeats):
                conditions = [(configuration, query) for configuration in CONFIGURATIONS for query in QUERIES]
                rng.shuffle(conditions)
                for position, (configuration, query) in enumerate(conditions):
                    variant_order = list(variants)
                    rng.shuffle(variant_order)
                    for variant in variant_order:
                        methods = ("search", "scan") if (repeat + position) % 2 == 0 else ("scan", "search")
                        for method in methods:
                            instruments = ("plain", "profile") if (repeat + position) % 2 == 0 else ("profile", "plain")
                            for instrumentation in instruments:
                                record = by_id[variant["name"], configuration["id"], query["id"], method]
                                target = index_paths[variant["name"], configuration["index_mode"], instrumentation] if method == "search" else documents
                                sample, diagnostic, _ = invoke(variant[instrumentation], method, configuration["query_mode"], target, query["text"])
                                if instrumentation == "profile":
                                    sample["profile"] = parse_profile(diagnostic, method, configuration["query_mode"])
                                    validate_counts(sample["profile"], record["expected_counts"])
                                sample["repeat"] = repeat
                                record[instrumentation][phase].append(sample)
                save()
        raw["binary_unchanged"] = all(benchmark.file_hash(variant[instrumentation]) == metadata[instrumentation + "_sha256"]
                                      for variant, metadata in zip(variants, raw["variants"]) for instrumentation in ("plain", "profile"))
        if not raw["binary_unchanged"]:
            raise ValueError("binary changed during measurement")
        if any(benchmark.file_hash(documents / item["path"]) != item["sha256"] for item in manifest["documents"]):
            raise ValueError("corpus changed during measurement")
        raw["status"] = "complete"
        raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        public = project(raw)
        public["raw_result_sha256"] = benchmark.file_hash(result_path)
        public["runner_sha256"] = benchmark.file_hash(Path(__file__))
        (run_dir / "summary.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException as error:
        raw["status"] = "failed"
        raw["failure"] = {"type": type(error).__name__, "message": str(error), "detail": getattr(error, "detail", None)}
        raise
    finally:
        if "finished_at_utc" not in raw:
            raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        print(result_path, flush=True)
    return result_path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", nargs=3, action="append", metavar=("NAME", "PLAIN", "PROFILE"), required=True)
    parser.add_argument("--provenance", action="append", default=[], metavar="NAME=JSON")
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / ".local/benchmarks")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=benchmark_corpus.DEFAULT_SEED)
    parser.add_argument("--index-repeats", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--require-index-identity", action="store_true", help="同一modeの保存形式・容量・SHA-256一致を必須とする")
    args = parser.parse_args(argv)
    names = [item[0] for item in args.variant]
    if args.size < 6 or min(args.index_repeats, args.repeats) < 1 or args.warmups < 0 or args.timeout <= 0:
        parser.error("size>=6, positive repeats/timeout and nonnegative warmups required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.run_id) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) for name in names) or len(set(names)) != len(names):
        parser.error("run-id and unique variant names must be safe directory names")
    if any("=" not in item or item.split("=", 1)[0] not in names for item in args.provenance):
        parser.error("provenance must be NAME=JSON for an existing variant")
    return args


if __name__ == "__main__":
    run(arguments())
