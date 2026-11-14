#!/usr/bin/env python3
"""手書きの独立期待値と合成文書で、明示的な複数語ANDを評価する。"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import unicodedata

import benchmark
import benchmark_corpus
import benchmark_normalized as normalized

REPOSITORY = Path(__file__).resolve().parent.parent
CONFIGURATIONS = normalized.CONFIGURATIONS
PERFORMANCE_CASES = (
    {"id": "single", "terms": ["ガラス"], "all": False},
    {"id": "and-broad", "terms": ["ガラス", "東京", "駅前"], "all": True},
    {"id": "and-rare", "terms": ["ガラス", "希少"], "all": True},
    {"id": "and-all-one", "terms": ["ガ", "希"], "all": True},
    {"id": "and-false-candidate", "terms": ["東京駅前", "ガラス"], "all": True},
    {"id": "and-normalized-duplicate", "terms": ["ガラス", "ｶﾞﾗｽ"], "all": True},
)


def fixture():
    documents = {
        "a.md": "検索の設計\nZigで実装\n", "b.md": "Zigの設計\n検索を調べる\n", "c.md": "検索 Zig\n",
        "d.md": "alpha beta\nbeta alpha\ngamma\n", "e.md": "alpha\nbeta\n", "f.md": "前ｶﾞ後\n",
        "g.md": "前㌔後\n", "i.md": "前ＡＢＣ後\n", "j.md": "--all marker\n--mode normalized\n",
        "k.md": "東京と京駅と駅前\n", "l.md": "東京駅前の記録\n", "m.md": "ガ\nキ\n",
        "n.md": "ababa\n", "o.md": "手順\n改行をまたぐ\n", "p.md": "a\u0315\u0300\n",
        "q.md": "左" * 50 + "needle" + "右" * 100 + "\n末尾 anchor\n",
    }
    both = [("a.md", 1, "検索の設計"), ("b.md", 1, "Zigの設計"), ("c.md", 1, "検索 Zig")]
    cases = [
        {"id": "separate-lines", "terms": ["検索", "Zig"], "literal": both},
        {"id": "reverse-order", "terms": ["Zig", "検索"], "literal": both},
        {"id": "phrase-control", "terms": ["検索 Zig"], "all": False, "literal": [("c.md", 1, "検索 Zig")]},
        {"id": "phrase-term", "terms": ["alpha beta", "gamma"], "literal": [("d.md", 1, "alpha beta")]},
        {"id": "fullwidth-space-term", "terms": ["alpha　beta", "gamma"], "literal": [], "normalized": [("d.md", 1, "alpha beta")]},
        {"id": "cross-line-terms", "terms": ["alpha", "beta"], "literal": [("d.md", 1, "alpha beta"), ("e.md", 1, "alpha")]},
        {"id": "literal-duplicate", "terms": ["検索", "検索"], "literal": [("a.md", 1, "検索の設計"), ("b.md", 2, "検索を調べる"), ("c.md", 1, "検索 Zig")]},
        {"id": "normalized-duplicate", "terms": ["abc", "ＡＢＣ"], "literal": [], "normalized": [("i.md", 1, "前ＡＢＣ後")]},
        {"id": "normalized-one", "terms": ["ｶﾞ", "キ"], "literal": [], "normalized": [("m.md", 1, "ガ")]},
        {"id": "overlap", "terms": ["bab", "aba"], "literal": [("n.md", 1, "ababa")]},
        {"id": "false-candidate", "terms": ["東京駅前", "東京"], "literal": [("l.md", 1, "東京駅前の記録")]},
        {"id": "option-as-term", "terms": ["--all", "marker"], "literal": [("j.md", 1, "--all marker")]},
        {"id": "mode-as-term", "terms": ["--mode", "normalized"], "literal": [("j.md", 2, "--mode normalized")]},
        {"id": "earliest-original-display", "terms": ["anchor", "needle"], "literal": [("q.md", 1, "左" * 32 + "needle" + "右" * 90)]},
        {"id": "newline-phrase", "terms": ["手順\n改行", "またぐ"], "literal": [("o.md", 1, "手順")]},
        {"id": "reordering", "terms": ["\u0315", "à"], "literal": [], "normalized": [("p.md", 1, "a\u0315\u0300")]},
        {"id": "partial-expansion", "terms": ["ロ", "前"], "literal": [], "normalized": [("g.md", 1, "前㌔後")]},
        {"id": "halfwidth-origin", "terms": ["ガ", "前"], "literal": [], "normalized": [("f.md", 1, "前ｶﾞ後")]},
    ]
    return documents, cases


def prepared_terms(terms, mode):
    if not terms:
        raise ValueError("empty term array")
    transformed = [normalized.transform(term, mode) for term in terms]
    if any(not term for term in transformed):
        raise ValueError("empty term")
    return list(dict.fromkeys(transformed))


def expected(texts, terms, configuration, method):
    """性能ケースは改行なしの語だけ。原文行ごとに位置を求める。"""
    mode = configuration["query_mode"]
    needles = prepared_terms(terms, mode)
    if any("\n" in needle for needle in needles):
        raise ValueError("performance oracle requires newline-free terms")
    transformed = {path: normalized.transform(text, mode) for path, text in texts.items()}
    grams = {needle[i:i + 2] for needle in needles for i in range(len(needle) - 1)}
    scan = method == "scan" or configuration["index_mode"] != mode or not grams
    if scan:
        seed_count, candidate_count, verified = 0, 0, set(texts)
    else:
        postings = [{path for path, text in transformed.items() if gram in text} for gram in grams]
        seed_count, verified = min(map(len, postings)), set.intersection(*postings)
        candidate_count = len(verified)
    hits = []
    for path, text in sorted(texts.items()):
        lines = []
        for needle in needles:
            for number, line in enumerate(text.split("\n"), 1):
                if needle in normalized.transform(line, mode):
                    lines.append(number)
                    break
            else:
                break
        else:
            hits.append((path, min(lines)))
    counts = {
        "shortest_posting_count": seed_count, "candidate_count": candidate_count,
        "documents_verified": len(verified), "hit_count": len(hits),
        "normalized_document_count": len(verified) if mode == "normalized" else 0,
        "normalized_input_bytes": sum(len(texts[path].encode("utf-8")) for path in verified) if mode == "normalized" else 0,
    }
    return hits, counts


def command(binary, method, mode, target, terms, all_terms):
    return [binary, method, "--mode", mode, *(["--all"] if all_terms else []), target, *terms]


def project(raw):
    if raw["status"] != "complete":
        raise ValueError("incomplete run")
    output = {
        "schema_version": 1, "scope": "synthetic-smoke", "status": "complete",
        "limitations": ["Host-wide load was not controlled.", "AND and phrase are different search contracts; their timings do not measure an interchangeable optimization."],
        "configuration": {key: raw["configuration"][key] for key in ("size", "seed", "repeats", "warmups", "index_repeats")},
        "binary_sha256": {key: raw["binary_sha256"][key] for key in ("plain", "profile", "phrase_baseline")},
        "corpus_sha256": raw["corpus"]["corpus_sha256"], "corpus_bytes": raw["corpus"]["total_bytes"],
        "cases": [{key: case[key] for key in ("id", "terms", "all")} for case in PERFORMANCE_CASES],
        "correctness": {
            "handwritten_cases": raw["quality"]["cases"], "invalid_cases": raw["quality"]["invalid_cases"],
            "option_path_passed": raw["quality"]["option_path_passed"], "phrase_regression_cases": raw["regression"]["cases"],
            "passed": raw["quality"]["passed"] and raw["regression"]["passed"],
        },
        "binary_unchanged": raw["binary_unchanged"], "indices": [], "results": [],
    }
    for index in raw["indices"]:
        output["indices"].append({
            **{key: index[key] for key in ("mode", "instrumentation", "index_bytes", "format_version", "index_sha256")},
            "summary": {key: index["summary"][key] for key in normalized.SUMMARY_FIELDS},
        })
    for result in raw["results"]:
        item = {key: result[key] for key in ("configuration_id", "case_id", "method")}
        item["expected_counts"] = {key: result["expected_counts"][key] for key in (*normalized.EXPECTED_COUNTS, "normalized_document_count", "normalized_input_bytes")}
        for instrumentation in ("plain", "profile"):
            item[instrumentation] = benchmark.summarize(result[instrumentation]["samples"])
        item["profile_counts"] = {key: normalized.numeric_summary([sample["profile"]["counts"][key] for sample in result["profile"]["samples"]]) for key in normalized.COUNTS}
        item["profile_timings_ns"] = {key: normalized.numeric_summary([sample["profile"]["timings_ns"][key] for sample in result["profile"]["samples"]]) for key in normalized.PHASES}
        output["results"].append(item)
    serialized = json.dumps(output, ensure_ascii=False)
    if any(marker in serialized for marker in ("/Users/", "/home/", "/private/", "hostname", "cpu_model", "MacBook", "architecture")):
        raise ValueError("private marker in projection")
    return output


def run(args):
    root = args.output_root.resolve() / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir()
    indices = root / "indices"
    indices.mkdir()
    binaries = {"plain": args.plain.resolve(), "profile": args.profile.resolve()}
    baseline = args.phrase_baseline.resolve() if args.phrase_baseline else None
    raw = {
        "schema_version": 1, "status": "running", "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "configuration": {key: getattr(args, key) for key in ("size", "seed", "repeats", "warmups", "index_repeats")},
        "binary_sha256": {**{name: benchmark.file_hash(path) for name, path in binaries.items()}, "phrase_baseline": benchmark.file_hash(baseline) if baseline else None},
        "environment": {"current_checkout": benchmark.environment(binaries["plain"]), "python_unicode_version": unicodedata.unidata_version},
        "provenance": json.loads(args.provenance.read_text()) if args.provenance else None,
        "indices": [], "results": [],
    }
    raw["environment"]["source_sha256"] = {str(path.relative_to(REPOSITORY)): benchmark.file_hash(path)
        for path in [*sorted((REPOSITORY / "src").glob("*.zig")), *sorted((REPOSITORY / "src").glob("*.c")),
                     *sorted((REPOSITORY / "vendor").rglob("*.c")), *sorted((REPOSITORY / "vendor").rglob("*.h")),
                     REPOSITORY / "build.zig", REPOSITORY / "build.zig.zon", REPOSITORY / "mise.toml"]}
    raw["environment"]["runner_sha256"] = {str(path.relative_to(REPOSITORY)): benchmark.file_hash(path)
        for path in (Path(__file__), Path(normalized.__file__), Path(benchmark.__file__), Path(benchmark_corpus.__file__))}
    order = 0

    def correctness(cmd, success=True, error=None, cwd=None):
        nonlocal order
        process = subprocess.run([os.fspath(arg) for arg in cmd], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout)
        (diagnostics / f"{order:06d}.stdout").write_bytes(process.stdout)
        (diagnostics / f"{order:06d}.stderr").write_bytes(process.stderr)
        order += 1
        if (process.returncode == 0) != success or (error and error.encode() not in process.stderr):
            raise ValueError(f"unexpected command result: {error or 'success required'}")
        return process.stdout, process.stderr

    def measured(binary, method, mode, target, terms, all_terms):
        nonlocal order
        path = diagnostics / f"{order:06d}.stderr"
        sample, stderr, _ = normalized.execute(command(binary, method, mode, target, terms, all_terms), path, args.timeout)
        sample["order"] = order
        order += 1
        return sample, stderr

    result_path = root / "results.json"

    def save():
        result_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        docs, quality_cases = fixture()
        quality_root = root / "quality"
        quality_root.mkdir()
        for path, text in docs.items():
            (quality_root / path).write_text(text, encoding="utf-8")
        quality_indices = {}
        for mode in ("literal", "normalized"):
            for instrumentation, binary in binaries.items():
                path = indices / f"quality-{mode}-{instrumentation}.zgi"
                correctness(command(binary, "index", mode, quality_root, [path], False))
                quality_indices[mode, instrumentation] = path
        cases = 0
        invalid_cases = 0
        for configuration in CONFIGURATIONS:
            mode = configuration["query_mode"]
            for case in quality_cases:
                expected_hits = case.get(mode, case["literal"])
                wanted = "".join(f"{quality_root / path}:{line}: {excerpt}\n" for path, line, excerpt in expected_hits).encode()
                for instrumentation, binary in binaries.items():
                    for method in ("search", "scan"):
                        target = quality_indices[configuration["index_mode"], instrumentation] if method == "search" else quality_root
                        stdout, _ = correctness(command(binary, method, mode, target, case["terms"], case.get("all", True)))
                        if stdout != wanted:
                            raise ValueError(f"handwritten fixture failed: {configuration['id']} / {case['id']} / {method} / {instrumentation}")
                cases += 1
            for instrumentation, binary in binaries.items():
                for method in ("search", "scan"):
                    target = quality_indices[configuration["index_mode"], instrumentation] if method == "search" else quality_root
                    for terms, error in (([], "EmptyQuery"), ([""], "EmptyQuery"), (["absent", ""], "EmptyQuery"), (["absent", b"\xff"], "InvalidUtf8")):
                        correctness(command(binary, method, mode, target, terms, True), success=False, error=error)
                        invalid_cases += 1
                    correctness([binary, method, "--all"], success=False, error="InvalidArguments")
                    invalid_cases += 1
        option_index = indices / "--all"
        correctness(command(binaries["plain"], "index", "literal", quality_root, [option_index], False))
        stdout, _ = correctness([binaries["plain"], "search", "--all", "./--all", "検索", "Zig"], cwd=indices)
        if stdout != "".join(f"{quality_root / path}:{line}: {excerpt}\n" for path, line, excerpt in quality_cases[0]["literal"]).encode():
            raise ValueError("./--all input path failed")
        raw["quality"] = {"passed": True, "cases": cases, "invalid_cases": invalid_cases, "option_path_passed": True,
                          "scope": "Handwritten full CLI outputs. Byte-span tie behavior remains the Zig unit tests' responsibility."}
        manifest, texts = normalized.corpus(root / "corpus", args.size, args.seed)
        raw["corpus"] = manifest
        documents = root / "corpus" / "documents"
        paths = {}
        for mode in ("literal", "normalized"):
            for instrumentation, binary in binaries.items():
                samples = []
                for repeat in range(args.index_repeats):
                    path = indices / f"{mode}-{instrumentation}-{repeat}.zgi"
                    sample, stderr = measured(binary, "index", mode, documents, [path], False)
                    if instrumentation == "profile":
                        sample["profile"] = normalized.parse_profile(stderr, "index", mode)
                    samples.append(sample)
                header = path.read_bytes()[:12]
                if header[:8] != b"ZIGURUI\x00":
                    raise ValueError("unexpected index header")
                raw["indices"].append({"mode": mode, "instrumentation": instrumentation, "samples": samples,
                    "summary": benchmark.summarize(samples), "index_bytes": path.stat().st_size,
                    "format_version": int.from_bytes(header[8:12], "little"), "index_sha256": benchmark.file_hash(path)})
                paths[mode, instrumentation] = path
        for mode in ("literal", "normalized"):
            if len({item["index_sha256"] for item in raw["indices"] if item["mode"] == mode}) != 1:
                raise ValueError("instrumentation changes index contents")
        regressions = 0
        for configuration in CONFIGURATIONS:
            mode = configuration["query_mode"]
            for query in benchmark_corpus.queries():
                wanted = normalized.expected_hits(texts, query["text"], mode)
                reference = None
                targets = [*binaries.items(), *([("phrase-baseline", baseline)] if baseline else [])]
                for instrumentation, binary in targets:
                    for method in ("search", "scan"):
                        target = paths[configuration["index_mode"], "plain" if instrumentation == "phrase-baseline" else instrumentation] if method == "search" else documents
                        stdout, _ = correctness(command(binary, method, mode, target, [query["text"]], False))
                        if normalized.parse_hits(stdout, documents) != wanted or (reference is not None and stdout != reference):
                            raise ValueError("existing phrase behavior differs")
                        reference = stdout
                regressions += 1
        raw["regression"] = {"passed": True, "cases": regressions, "baseline_compared": baseline is not None}
        for configuration in CONFIGURATIONS:
            mode = configuration["query_mode"]
            for case in PERFORMANCE_CASES:
                wanted, _ = expected(texts, case["terms"], configuration, "scan")
                reference = None
                for instrumentation, binary in binaries.items():
                    for method in ("search", "scan"):
                        target = paths[configuration["index_mode"], instrumentation] if method == "search" else documents
                        stdout, stderr = correctness(command(binary, method, mode, target, case["terms"], case["all"]))
                        _, counts = expected(texts, case["terms"], configuration, method)
                        if normalized.parse_hits(stdout, documents) != wanted or (reference is not None and stdout != reference):
                            raise ValueError("AND corpus oracle or search/scan equality failed")
                        reference = stdout
                        if instrumentation == "profile":
                            normalized.validate_counts(normalized.parse_profile(stderr, method, mode), counts)
                for method in ("search", "scan"):
                    _, counts = expected(texts, case["terms"], configuration, method)
                    raw["results"].append({"configuration_id": configuration["id"], "case_id": case["id"], "method": method,
                                           "expected_counts": counts, "plain": {"warmups": [], "samples": []}, "profile": {"warmups": [], "samples": []}})
        by_id = {(record["configuration_id"], record["case_id"], record["method"]): record for record in raw["results"]}
        rng = random.Random(args.seed)
        for phase, repeats in (("warmups", args.warmups), ("samples", args.repeats)):
            for repeat in range(repeats):
                conditions = [(configuration, case) for configuration in CONFIGURATIONS for case in PERFORMANCE_CASES]
                rng.shuffle(conditions)
                for position, (configuration, case) in enumerate(conditions):
                    mode = configuration["query_mode"]
                    methods = ("search", "scan") if (repeat + position) % 2 == 0 else ("scan", "search")
                    for method in methods:
                        record = by_id[configuration["id"], case["id"], method]
                        instruments = ("plain", "profile") if (repeat + position) % 2 == 0 else ("profile", "plain")
                        for instrumentation in instruments:
                            target = paths[configuration["index_mode"], instrumentation] if method == "search" else documents
                            sample, stderr = measured(binaries[instrumentation], method, mode, target, case["terms"], case["all"])
                            if instrumentation == "profile":
                                sample["profile"] = normalized.parse_profile(stderr, method, mode)
                                normalized.validate_counts(sample["profile"], record["expected_counts"])
                            sample["repeat"] = repeat
                            record[instrumentation][phase].append(sample)
                save()
        raw["binary_unchanged"] = all(benchmark.file_hash(path) == raw["binary_sha256"][key] for key, path in binaries.items()) and (baseline is None or benchmark.file_hash(baseline) == raw["binary_sha256"]["phrase_baseline"])
        if not raw["binary_unchanged"] or any(benchmark.file_hash(documents / item["path"]) != item["sha256"] for item in manifest["documents"]):
            raise ValueError("binary or corpus changed during run")
        raw["status"] = "complete"
        raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        public = project(raw)
        public["raw_result_sha256"] = benchmark.file_hash(result_path)
        public["runner_sha256"] = benchmark.file_hash(Path(__file__))
        (root / "summary.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException as error:
        raw["status"] = "failed"
        raw["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        if "finished_at_utc" not in raw:
            raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        print(result_path, flush=True)
    return result_path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plain", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--phrase-baseline", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / ".local/benchmarks")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=benchmark_corpus.DEFAULT_SEED)
    parser.add_argument("--index-repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.run_id) or args.size < 6 or min(args.index_repeats, args.repeats) < 1 or args.warmups < 0 or args.timeout <= 0:
        parser.error("safe run-id, size>=6, positive repeats/timeout, nonnegative warmups required")
    return args


if __name__ == "__main__":
    run(arguments())
