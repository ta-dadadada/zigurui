#!/usr/bin/env python3
"""組み込みファイルmetadataを小さい合成文書で独立評価する。性能測定はしない。"""
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


def fixture_documents():
    return [
        frontmatter.document("one/same.md", "file:\n  name: fake.md\n  path: fake/path.md\nstatus: draft\n", "BodyNeedle\nZigで検索\n", {"file": {"name": "fake.md", "path": "fake/path.md"}, "status": "draft"}),
        frontmatter.document("two/same.md", "status: published\n", "BodyNeedle\n検索\n", {"status": "published"}),
        frontmatter.document("upper/Guide.md", None, "BodyNeedle Zig\n検索\n", None),
        frontmatter.document("lower/guide.md", None, "BodyNeedle\n", None),
        frontmatter.document("unicode/案内.md", "status: draft\n", "前ｶﾞ後 BodyNeedle\n", {"status": "draft"}),
        frontmatter.document("width/Ｇuide.md", None, "BodyNeedle\n", None),
        frontmatter.document("space/with space.md", None, "BodyNeedle\n", None),
        frontmatter.document("equals/a=b.md", None, "BodyNeedle\n", None),
        frontmatter.document("empty.md", "", "BodyNeedle\n", {}),
        frontmatter.document("plain.md", None, "BodyNeedle --where\n", None),
    ]


def select_documents(documents, filters, root):
    for selector, wanted in filters:
        frontmatter.scalar_type(wanted)
        if selector.startswith("file:"):
            if selector not in ("file:/name", "file:/path"):
                raise ValueError("unknown builtin selector")
        else:
            frontmatter.resolve(None, selector)
    selected = []
    for doc in documents:
        stored_path = str(root / doc["path"])
        for selector, wanted in filters:
            if selector.startswith("file:"):
                if selector not in ("file:/name", "file:/path"):
                    raise ValueError("unknown builtin selector")
                actual = Path(stored_path).name if selector == "file:/name" else stored_path
                if not frontmatter.exact_scalar(actual, wanted):
                    break
            elif not frontmatter.where_matches(doc["metadata"], [(selector, wanted)]):
                break
        else:
            selected.append(doc)
    return selected


def fixture_cases(root):
    definitions = (
        ("same-basename", [("file:/name", "same.md")], ["one/same.md", "two/same.md"]),
        ("extension-required", [("file:/name", "same")], []),
        ("exact-path", [("file:/path", str(root / "one/same.md"))], ["one/same.md"]),
        ("relative-value-not-rooted", [("file:/path", "one/same.md")], []),
        ("path-value-not-canonicalized", [("file:/path", str(root / "one") + "/../one/same.md")], []),
        ("uppercase-name", [("file:/name", "Guide.md")], ["upper/Guide.md"]),
        ("lowercase-name", [("file:/name", "guide.md")], ["lower/guide.md"]),
        ("fullwidth-name", [("file:/name", "Ｇuide.md")], ["width/Ｇuide.md"]),
        ("unicode-name", [("file:/name", "案内.md")], ["unicode/案内.md"]),
        ("space-name", [("file:/name", "with space.md")], ["space/with space.md"]),
        ("equals-name", [("file:/name", "a=b.md")], ["equals/a=b.md"]),
        ("fm-name-does-not-alias", [("file:/name", "fake.md")], []),
        ("fm-path-does-not-alias", [("file:/path", "fake/path.md")], []),
        ("null-is-type-mismatch", [("file:/name", None)], []),
        ("integer-is-type-mismatch", [("file:/name", 1)], []),
        ("float-is-type-mismatch", [("file:/name", 1.0)], []),
        ("boolean-is-type-mismatch", [("file:/path", True)], []),
    )
    cases = [{"id": case_id, "filters": filters, "expected": expected, "terms": ["BodyNeedle"], "frontmatter": False, "all": False}
             for case_id, filters, expected in definitions]
    cases.extend([
        {"id": "fm-pointer-name", "filters": [("/file/name", "fake.md")], "expected": ["one/same.md"], "terms": ["BodyNeedle"], "frontmatter": True, "all": False},
        {"id": "fm-pointer-path", "filters": [("/file/path", "fake/path.md")], "expected": ["one/same.md"], "terms": ["BodyNeedle"], "frontmatter": True, "all": False},
        {"id": "builtin-and-fm", "filters": [("file:/name", "same.md"), ("/status", "draft")], "expected": ["one/same.md"], "terms": ["BodyNeedle"], "frontmatter": True, "all": False},
        {"id": "builtin-and-terms", "filters": [("file:/name", "same.md")], "expected": ["one/same.md"], "terms": ["Zig", "検索"], "frontmatter": False, "all": True},
        {"id": "raw-includes-header", "filters": [("file:/name", "same.md")], "expected": ["one/same.md"], "terms": ["fake.md"], "frontmatter": False, "all": False},
        {"id": "explicit-fm-excludes-header", "filters": [("file:/name", "same.md")], "expected": [], "terms": ["fake.md"], "frontmatter": True, "all": False},
        {"id": "option-looking-term", "filters": [("file:/name", "plain.md")], "expected": ["plain.md"], "terms": ["--where"], "frontmatter": False, "all": False},
        {"id": "normalized-original-position", "filters": [("file:/name", "案内.md")], "expected": {"literal": [], "normalized": ["unicode/案内.md"]}, "terms": ["ガ"], "frontmatter": True, "all": False},
    ])
    return cases


def run(args):
    root = args.output_root.resolve() / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir()
    indices = root / "indices"
    indices.mkdir()
    binary = args.binary.resolve()
    baseline = args.baseline.resolve() if args.baseline else None
    raw = {"schema_version": 1, "status": "running", "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "binary_sha256": benchmark.file_hash(binary), "baseline_sha256": benchmark.file_hash(baseline) if baseline else None,
           "environment": benchmark.environment(binary), "provenance": json.loads(args.provenance.read_text()) if args.provenance else None,
           "cases": [], "errors": [], "snapshot_cases": 0, "regression_cases": 0}
    order = 0

    def invoke(command, error=None):
        nonlocal order
        result = subprocess.run(list(map(os.fspath, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        (diagnostics / f"{order:05d}.stdout").write_bytes(result.stdout)
        (diagnostics / f"{order:05d}.stderr").write_bytes(result.stderr)
        order += 1
        if error:
            if result.returncode == 0 or error.encode() not in result.stderr or result.stdout:
                raise ValueError(f"expected {error} with empty stdout at diagnostic {order - 1}")
        elif result.returncode != 0:
            raise ValueError(f"expected success at diagnostic {order - 1}")
        return result.stdout

    def write(directory, documents):
        directory.mkdir()
        for doc in documents:
            path = directory / doc["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(doc["raw"].encode())

    def index(directory, name, mode="literal", executable=binary):
        target = indices / (name + ".zgi")
        invoke([executable, "index", "--mode", mode, directory, target])
        return target

    try:
        documents = fixture_documents()
        directory = root / "documents"
        write(directory, documents)
        paths = {mode: index(directory, mode, mode) for mode in ("literal", "normalized")}
        wanted_schema = frontmatter.expected_schema(documents)
        for target in paths.values():
            if json.loads(invoke([binary, "schema", target])) != wanted_schema:
                raise ValueError("schema separates builtin and frontmatter incorrectly")
        raw["schema"] = wanted_schema
        for configuration in frontmatter.CONFIGURATIONS:
            mode = configuration["query_mode"]
            for case in fixture_cases(directory):
                selected = select_documents(documents, case["filters"], directory)
                wanted, actual_paths = frontmatter.original_output(selected, case["terms"], [], mode, directory, frontmatter=case["frontmatter"])
                explicit = case["expected"][mode] if type(case["expected"]) is dict else case["expected"]
                if actual_paths != explicit:
                    raise ValueError("handwritten paths disagree with typed oracle: " + case["id"])
                for method, target in (("search", paths[configuration["index_mode"]]), ("scan", directory)):
                    stdout = invoke(frontmatter.search_command(binary, method, mode, target, case["terms"], frontmatter=case["frontmatter"], filters=case["filters"], all_terms=case["all"]))
                    if stdout != wanted:
                        raise ValueError("builtin fixture differs: " + case["id"])
                raw["cases"].append({"id": case["id"], "configuration_id": configuration["id"], "passed": True})
        for method, target in (("search", paths["literal"]), ("scan", directory)):
            for selector in ("file:/extension", "file:/Name", "file:name"):
                invoke([binary, method, "--where", selector, '"same.md"', target, "BodyNeedle"], error="InvalidFilter")
                raw["errors"].append({"id": "unknown-selector", "method": method, "passed": True})
            for selector in ("/file/name", "/status"):
                invoke([binary, method, "--where", "file:/name", '"absent.md"', "--where", selector, '"draft"', target, "BodyNeedle"], error="InvalidArguments")
                raw["errors"].append({"id": "fm-requires-flag", "method": method, "passed": True})
            invoke([binary, method, "--where", "file:/name", "[]", target, "BodyNeedle"], error="InvalidFilter")
            raw["errors"].append({"id": "non-scalar-filter", "method": method, "passed": True})
        empty = root / "empty"
        write(empty, [])
        empty_index = index(empty, "empty")
        if json.loads(invoke([binary, "schema", empty_index])) != frontmatter.expected_schema([]):
            raise ValueError("empty schema lost builtin definitions")
        invoke([binary, "search", "--where", "file:/unknown", '"value"', empty_index, "BodyNeedle"], error="InvalidFilter")
        raw["errors"].append({"id": "unknown-selector-empty-corpus", "passed": True})
        bad = root / "invalid-yaml"
        bad_docs = [frontmatter.document("good.md", "status: ready\n", "BodyNeedle\n", {"status": "ready"}),
                    frontmatter.document("broken.md", None, "---\nx: [unterminated\n---\nBrokenBody\n", None)]
        write(bad, bad_docs)
        bad_index = index(bad, "invalid-yaml")
        for name, term, expected_path in (("good.md", "BodyNeedle", "good.md"), ("broken.md", "BrokenBody", "broken.md")):
            for method, target in (("search", bad_index), ("scan", bad)):
                selected = [doc for doc in bad_docs if doc["path"] == expected_path]
                wanted, _ = frontmatter.original_output(selected, [term], [], "literal", bad, frontmatter=False)
                if invoke(frontmatter.search_command(binary, method, "literal", target, [term], frontmatter=False, filters=[("file:/name", name)])) != wanted:
                    raise ValueError("file-only filter parsed invalid YAML")
                raw["cases"].append({"id": "file-only-invalid-yaml", "configuration_id": method, "passed": True})
        for method, target in (("search", bad_index), ("scan", bad)):
            for name in ("good.md", "absent.md"):
                invoke(frontmatter.search_command(binary, method, "literal", target, ["BodyNeedle"], filters=[("file:/name", name)]), error="InvalidFrontmatter")
                raw["errors"].append({"id": "explicit-fm-validates-all", "method": method, "passed": True})
        invoke([binary, "schema", bad_index], error="InvalidFrontmatter")
        raw["errors"].append({"id": "schema-invalid-yaml", "passed": True})
        snapshot_root = root / "snapshot"
        old = frontmatter.document("snapshot.md", "status: draft\n", "OldBody\n", {"status": "draft"})
        write(snapshot_root, [old])
        saved = index(snapshot_root, "snapshot")
        changed = frontmatter.document("snapshot.md", "status: [published]\n", "NewBody\n", {"status": ["published"]})
        (snapshot_root / "snapshot.md").write_bytes(changed["raw"].encode())
        added = frontmatter.document("added.md", None, "NewBody\n", None)
        (snapshot_root / "added.md").write_bytes(added["raw"].encode())
        if json.loads(invoke([binary, "schema", saved])) != frontmatter.expected_schema([old]):
            raise ValueError("schema reread the live file")
        snapshot_cases = (
            ("search", saved, ["OldBody"], [("file:/path", str(snapshot_root / "snapshot.md"))], [old]),
            ("search", saved, ["NewBody"], [("file:/name", "snapshot.md")], []),
            ("search", saved, ["NewBody"], [("file:/name", "added.md")], []),
            ("scan", snapshot_root, ["NewBody"], [("file:/name", "snapshot.md")], [changed]),
            ("scan", snapshot_root, ["NewBody"], [("file:/name", "added.md")], [added]),
        )
        for method, target, terms, filters, expected_docs in snapshot_cases:
            wanted, _ = frontmatter.original_output(expected_docs, terms, [], "literal", snapshot_root, frontmatter=False)
            if invoke(frontmatter.search_command(binary, method, "literal", target, terms, frontmatter=False, filters=filters)) != wanted:
                raise ValueError("stored file metadata or body snapshot changed")
            raw["snapshot_cases"] += 1
        raw["index_identity"] = True
        if baseline:
            for mode in ("literal", "normalized"):
                old_index = index(directory, "baseline-" + mode, mode, baseline)
                if benchmark.file_hash(old_index) != benchmark.file_hash(paths[mode]):
                    raise ValueError("builtin metadata changed v2 bytes")
                old_schema = json.loads(invoke([baseline, "schema", old_index]))
                if old_schema != {key: value for key, value in wanted_schema.items() if key != "builtin_fields"}:
                    raise ValueError("existing frontmatter schema changed")
                for terms, all_terms, fm, filters in ((["BodyNeedle"], False, False, []), (["fake.md"], False, False, []),
                                                     (["Zig", "検索"], True, False, []), (["BodyNeedle"], False, True, [("/status", "draft")])):
                    reference = None
                    for executable in (binary, baseline):
                        for target in (old_index, paths[mode]):
                            stdout = invoke(frontmatter.search_command(executable, "search", mode, target, terms, frontmatter=fm, filters=filters, all_terms=all_terms))
                            if reference is not None and stdout != reference:
                                raise ValueError("existing raw/AND/frontmatter search changed")
                            reference = stdout
                    raw["regression_cases"] += 1
        raw["binary_unchanged"] = benchmark.file_hash(binary) == raw["binary_sha256"] and (baseline is None or benchmark.file_hash(baseline) == raw["baseline_sha256"])
        if not raw["binary_unchanged"]:
            raise ValueError("binary changed during evaluation")
        raw["status"] = "complete"
        raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    except BaseException as error:
        raw["status"] = "failed"
        raw["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        if "finished_at_utc" not in raw:
            raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result_path = root / "results.json"
        result_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(result_path, flush=True)
    summary = {
        "schema_version": 1, "status": "complete", "scope": "synthetic-correctness-no-performance-measurement",
        "binary_sha256": raw["binary_sha256"], "baseline_sha256": raw["baseline_sha256"],
        "document_count": len(documents), "frontmatter_count": wanted_schema["frontmatter_count"],
        "builtin_fields": frontmatter.expected_builtin_fields(len(documents)),
        "frontmatter_fields_unchanged": True, "schema_empty_passed": True, "search_cases": len(raw["cases"]),
        "error_cases": len(raw["errors"]), "snapshot_cases": raw["snapshot_cases"], "regression_cases": raw["regression_cases"],
        "index_identity": raw["index_identity"], "binary_unchanged": raw["binary_unchanged"],
        "raw_result_sha256": benchmark.file_hash(result_path), "runner_sha256": benchmark.file_hash(Path(__file__)),
    }
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if any(marker in serialized for marker in ("/Users/", "/home/", "/private/", "hostname", "cpu_model", "MacBook", "architecture")):
        raise ValueError("private marker in summary")
    (root / "summary.json").write_text(serialized, encoding="utf-8")
    return result_path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / ".local/benchmarks")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.run_id):
        parser.error("safe run-id required")
    return args


if __name__ == "__main__":
    run(arguments())
