#!/usr/bin/env python3
"""frontmatter検証失敗の停止・ファイル文脈・内容を漏らさない診断を確認する。"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess

import benchmark
import evaluate_frontmatter as frontmatter

REPOSITORY = Path(__file__).resolve().parent.parent
HEADER_MARKER = "HEADER_PRIVATE_SENTINEL_42"
BODY_MARKER = "BODY_PRIVATE_SENTINEL_42"


def check_diagnostic(returncode, stdout, stderr, path, reason, context="frontmatter"):
    if returncode != 1 or stdout:
        raise ValueError("validation failure must exit 1 without partial stdout")
    if not stderr.endswith(b"\n") or b"\n" in stderr[:-1]:
        raise ValueError("plain diagnostic must be exactly one physical line")
    if any(byte < 32 or byte == 127 for byte in stderr[:-1]):
        raise ValueError("raw control byte in diagnostic")
    if any(marker.encode() in stderr for marker in (HEADER_MARKER, BODY_MARKER)):
        raise ValueError("frontmatter or body value leaked to stderr")
    match = re.fullmatch(r'zigurui: (frontmatter|schema_aggregation): ([A-Za-z0-9_]+): file=("(?:\\.|[^"\\])*")\n', stderr.decode("utf-8"))
    if match is None:
        raise ValueError("diagnostic contains text outside the reason/path contract")
    if match[1] != context or match[2] != reason or json.loads(match[3]) != str(path):
        raise ValueError("diagnostic file, reason or context differs")


def fixtures():
    prefix = f"state: blocked\nprivate: {HEADER_MARKER}\n"
    syntax = prefix + "broken: [unterminated\n"
    resource = prefix + 'pad: "' + "x" * (256 * 1024) + '"\n'
    definitions = [
        ("syntax", "01-problem.md", syntax, "InvalidFrontmatter"),
        ("unsupported", "01-problem.md", prefix + "tagged: !!str value\n", "UnsupportedFrontmatter"),
        ("resource", "01-problem.md", resource, "FrontmatterTooLarge"),
        ("number", "01-problem.md", prefix + "number: 1e999\n", "FrontmatterNumberOutOfRange"),
    ]
    definitions.extend((case_id, basename, syntax, "InvalidFrontmatter") for case_id, basename in (
        ("filename-lf", "01-bad\nforged.md"), ("filename-cr", "01-bad\rforged.md"),
        ("filename-tab", "01-bad\tforged.md"), ("filename-esc", "01-bad\x1b[31mforged.md"),
        ("filename-del", "01-bad\x7fforged.md"), ("filename-quote-backslash", '01-bad"\\forged.md'),
        ("filename-unicode", "01-問題.md"),
    ))
    result = []
    for case_id, basename, payload, reason in definitions:
        before = frontmatter.document("00-before.md", "state: ready\n", "BodyNeedle before\n", {"state": "ready"})
        bad = frontmatter.document(basename, payload, BODY_MARKER + "\nNoQueryHere\n", None)
        after = frontmatter.document("02-after.md", "state: ready\n", "BodyNeedle after\n", {"state": "ready"})
        result.append({"id": case_id, "documents": [before, bad, after], "problem": basename, "reason": reason})
    return result


def aggregate_fixture():
    documents = []
    for path, begin, end in (("00-before.md", 0, 2047), ("01-before.md", 2047, 4094), ("02-context.md", 4094, 4097)):
        metadata = {f"k{number}": HEADER_MARKER for number in range(begin, end)}
        payload = "".join(f"{key}: {value}\n" for key, value in metadata.items())
        documents.append(frontmatter.document(path, payload, "BodyNeedle\n" + BODY_MARKER + "\n", metadata))
    documents.append(frontmatter.document("03-after.md", None, "BodyNeedle after\n", None))
    return documents


def run(args):
    root = args.output_root.resolve() / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir()
    binary = args.binary.resolve()
    raw = {"schema_version": 1, "status": "running", "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "binary_sha256": benchmark.file_hash(binary), "provenance": json.loads(args.provenance.read_text()) if args.provenance else None,
           "git_head": benchmark.capture(["git", "rev-parse", "HEAD"]), "git_status": benchmark.capture(["git", "status", "--short"]),
           "checks": [], "success_checks": []}
    order = 0

    def invoke(command):
        nonlocal order
        process = subprocess.run(list(map(os.fspath, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        (diagnostics / f"{order:04d}.stdout").write_bytes(process.stdout)
        (diagnostics / f"{order:04d}.stderr").write_bytes(process.stderr)
        order += 1
        return process

    def success(command, expected=None):
        result = invoke(command)
        if result.returncode != 0 or result.stderr or (expected is not None and result.stdout != expected):
            raise ValueError(f"successful path differs at diagnostic {order - 1}")
        return result.stdout

    def write(directory, documents):
        directory.mkdir()
        for doc in documents:
            (directory / doc["path"]).write_bytes(doc["raw"].encode())

    try:
        for case in fixtures():
            directory = root / case["id"]
            write(directory, case["documents"])
            index = root / (case["id"] + ".zgi")
            success([binary, "index", directory, index])
            for method, target in (("search", index), ("scan", directory)):
                for condition, filters in (("candidate-outside", []), ("builtin-filter-outside", [("file:/name", "00-before.md")]),
                                           ("fm-filter-outside", [("/state", "ready")])):
                    process = invoke(frontmatter.search_command(binary, method, "literal", target, ["BodyNeedle"], filters=filters))
                    check_diagnostic(process.returncode, process.stdout, process.stderr, directory / case["problem"], case["reason"])
                    raw["checks"].append({"case_id": case["id"], "method": method, "condition": condition, "reason": case["reason"], "passed": True})
            process = invoke([binary, "schema", index])
            check_diagnostic(process.returncode, process.stdout, process.stderr, directory / case["problem"], case["reason"])
            raw["checks"].append({"case_id": case["id"], "method": "schema", "condition": "parse-all", "reason": case["reason"], "passed": True})
            normal_docs = [doc for doc in case["documents"] if doc["path"] != case["problem"]]
            for method, target in (("search", index), ("scan", directory)):
                wanted, _ = frontmatter.original_output(normal_docs, ["BodyNeedle"], [], "literal", directory, frontmatter=False)
                success(frontmatter.search_command(binary, method, "literal", target, ["BodyNeedle"], frontmatter=False), wanted)
                raw["success_checks"].append({"case_id": case["id"], "method": method, "condition": "raw", "passed": True})
                wanted, _ = frontmatter.original_output([normal_docs[0]], ["BodyNeedle"], [], "literal", directory, frontmatter=False)
                success(frontmatter.search_command(binary, method, "literal", target, ["BodyNeedle"], frontmatter=False, filters=[("file:/name", "00-before.md")]), wanted)
                raw["success_checks"].append({"case_id": case["id"], "method": method, "condition": "file-only", "passed": True})
                if not case["id"].startswith("filename-"):
                    problem_doc = next(doc for doc in case["documents"] if doc["path"] == case["problem"])
                    wanted, _ = frontmatter.original_output([problem_doc], ["NoQueryHere"], [], "literal", directory, frontmatter=False)
                    success(frontmatter.search_command(binary, method, "literal", target, ["NoQueryHere"], frontmatter=False, filters=[("file:/name", case["problem"])]), wanted)
                    raw["success_checks"].append({"case_id": case["id"], "method": method, "condition": "file-only-problem-selected", "passed": True})
        aggregate = root / "schema-aggregation"
        documents = aggregate_fixture()
        write(aggregate, documents)
        index = root / "schema-aggregation.zgi"
        success([binary, "index", aggregate, index])
        process = invoke([binary, "schema", index])
        check_diagnostic(process.returncode, process.stdout, process.stderr, aggregate / "02-context.md", "FrontmatterTooLarge", "schema_aggregation")
        raw["checks"].append({"case_id": "schema-aggregation", "method": "schema", "condition": "aggregate-context", "reason": "FrontmatterTooLarge", "passed": True})
        for method, target in (("search", index), ("scan", aggregate)):
            wanted, _ = frontmatter.original_output(documents, ["BodyNeedle"], [], "literal", aggregate)
            success(frontmatter.search_command(binary, method, "literal", target, ["BodyNeedle"]), wanted)
            raw["success_checks"].append({"case_id": "schema-aggregation", "method": method, "condition": "per-document-valid", "passed": True})
        raw["binary_unchanged"] = benchmark.file_hash(binary) == raw["binary_sha256"]
        if not raw["binary_unchanged"]:
            raise ValueError("binary changed during evaluation")
        raw["status"] = "complete"
    except BaseException as error:
        raw["status"] = "failed"
        raw["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result_path = root / "results.json"
        result_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(result_path, flush=True)
    summary = {"schema_version": 1, "status": "complete", "scope": "synthetic-diagnostic-correctness-no-performance-measurement",
               "binary_sha256": raw["binary_sha256"], "binary_unchanged": raw["binary_unchanged"],
               "failure_checks": len(raw["checks"]), "success_checks": len(raw["success_checks"]),
               "failure_classes": sorted({check["reason"] for check in raw["checks"]}),
               "filename_safety_cases": len([case for case in fixtures() if case["id"].startswith("filename-")]),
               "schema_aggregation_context_passed": True, "partial_stdout_absent": True, "header_body_values_absent_from_stderr": True,
               "raw_result_sha256": benchmark.file_hash(result_path), "runner_sha256": benchmark.file_hash(Path(__file__))}
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if any(marker in serialized for marker in ("/Users/", "/home/", "/private/", "hostname", "cpu_model", "MacBook", "architecture")):
        raise ValueError("private marker in summary")
    (root / "summary.json").write_text(serialized, encoding="utf-8")
    return result_path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / ".local/benchmarks")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.run_id):
        parser.error("safe run-id required")
    return args


if __name__ == "__main__":
    run(arguments())
