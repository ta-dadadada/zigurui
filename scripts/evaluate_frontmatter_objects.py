#!/usr/bin/env python3
"""配列内objectの手書き期待値とCLI出力を照合する。性能は測定しない。"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import benchmark
import evaluate_frontmatter as base
import evaluate_frontmatter_diagnostics as diagnostics

REPOSITORY = Path(__file__).resolve().parent.parent


def kind(value):
    return "object" if type(value) is dict else "array" if type(value) is list else base.scalar_type(value)


def values_at(metadata, pointer):
    """配列を透過し、キーpathの全観測値を返す。添字は解釈しない。"""
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("invalid field pointer")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]

    def visit(value, remaining):
        if not remaining:
            return [value]
        if type(value) is list:
            return [found for item in value for found in visit(item, remaining)]
        if type(value) is dict and remaining[0] in value:
            return visit(value[remaining[0]], remaining[1:])
        return []

    return visit(metadata, parts)


def where_matches(metadata, filters):
    def matches(value, wanted):
        if type(value) is list:
            return any(base.exact_scalar(item, wanted) for item in value)
        return base.exact_scalar(value, wanted)

    return all(any(matches(value, wanted) for value in values_at(metadata, pointer)) for pointer, wanted in filters)


def expected_schema(documents):
    """文書内の繰り返しを集約してから、文書数と型unionを集計する。"""
    fields = {}
    for doc in documents:
        observations = {}

        def walk(value, pointer):
            if pointer is not None:
                observations.setdefault(pointer, []).append(value)
            if type(value) is dict:
                for key, child in value.items():
                    walk(child, (pointer or "") + "/" + base.escape(key))
            elif type(value) is list:
                def descendants(children):
                    for child in children:
                        if type(child) is dict:
                            for key, nested in child.items():
                                walk(nested, pointer + "/" + base.escape(key))
                        elif type(child) is list:
                            descendants(child)
                descendants(value)

        if doc["metadata"] is not None:
            walk(doc["metadata"], None)
        for pointer, values in observations.items():
            field = fields.setdefault(pointer, dict(types=set(), present=0, null_count=0, element_types=set()))
            field["present"] += 1
            field["null_count"] += any(value is None for value in values)
            field["types"].update(map(kind, values))
            field["element_types"].update(kind(item) for value in values if type(value) is list for item in value)
    return dict(document_count=len(documents), frontmatter_count=sum(doc["metadata"] is not None for doc in documents),
                builtin_fields=base.expected_builtin_fields(len(documents)), fields=[
                    dict(pointer=pointer, types=sorted(field["types"]), present=field["present"],
                         missing=len(documents) - field["present"], null_count=field["null_count"],
                         element_types=sorted(field["element_types"]))
                    for pointer, field in sorted(fields.items(), key=lambda item: item[0].encode())])


def fixture_documents():
    return [
        base.document("a.md", 'items:\n- name: Alice\n  state: draft\n  score: 1\n  tags: [zig, null]\n- name: Bob\n  state: ready\n  score: 1.0\n  tags: [note]\n- name: Alice\n  score: null\n- name: null\n- name: null\n- {}\n',
                      "前ｶﾞ後 BodyNeedle\n検索 Zig\n", {"items": [{"name": "Alice", "state": "draft", "score": 1, "tags": ["zig", None]}, {"name": "Bob", "state": "ready", "score": 1.0, "tags": ["note"]}, {"name": "Alice", "score": None}, {"name": None}, {"name": None}, {}]}),
        base.document("b.md", 'items: [scalar, null, true, 1, 1.0, {name: Alice, state: ready, score: "1"}]\n',
                      "BodyNeedle 前㌔後\r\n検索 Zig\r\n", {"items": ["scalar", None, True, 1, 1.0, {"name": "Alice", "state": "ready", "score": "1"}]}, crlf=True, bom=True),
        base.document("c.md", 'items: []\n', "BodyNeedle\n", {"items": []}),
        base.document("d.md", 'items: [{}]\n', "BodyNeedle\n", {"items": [{}]}),
        base.document("e.md", 'items: {name: null}\n', "BodyNeedle\n", {"items": {"name": None}}),
        base.document("f.md", 'items: null\n', "BodyNeedle\n", {"items": None}),
        base.document("g.md", None, "HeaderOnlyNeedle BodyNeedle\n", None),
        base.document("h.md", 'items:\n- "a/b":\n    "~key": literal\n  "": empty\n  "0": digit-key\n  children: [{name: 子, tags: [技術, null]}]\nheader: HeaderOnlyNeedle\n',
                      "BodyNeedle\n", {"items": [{"a/b": {"~key": "literal"}, "": "empty", "0": "digit-key", "children": [{"name": "子", "tags": ["技術", None]}]}], "header": "HeaderOnlyNeedle"}),
        base.document("i.md", 'items: [[{name: Carol, state: ready}], [], [[{name: Carol, score: null}, {name: null}]], {children: [[{name: 孫}]]}]\n',
                      "BodyNeedle\n", {"items": [[{"name": "Carol", "state": "ready"}], [], [[{"name": "Carol", "score": None}, {"name": None}]], {"children": [[{"name": "孫"}]]}]}),
        base.document("j.md", 'items: [[1], [[null]], [{only: true}]]\n', "BodyNeedle\n", {"items": [[1], [[None]], [{"only": True}]]}),
    ]


def fixture_cases():
    definitions = [
        ("name-deduplicated", [("/items/name", "Alice")], ["a.md", "b.md"]),
        ("null-deduplicated", [("/items/name", None)], ["a.md", "e.md", "i.md"]),
        ("missing-not-null", [("/items/absent", None)], []),
        ("container-null", [("/items", None)], ["b.md", "f.md"]),
        ("mixed-scalar", [("/items", "scalar")], ["b.md"]),
        ("mixed-integer", [("/items", 1)], ["b.md"]),
        ("terminal-nested-scalar-not-flattened", [("/items", 1), ("/items/only", True)], []),
        ("terminal-nested-null-not-flattened", [("/items", None), ("/items/only", True)], []),
        ("mixed-float", [("/items", 1.0)], ["b.md"]),
        ("mixed-boolean", [("/items", True)], ["b.md"]),
        ("integer", [("/items/score", 1)], ["a.md"]),
        ("float", [("/items/score", 1.0)], ["a.md"]),
        ("string-number", [("/items/score", "1")], ["b.md"]),
        ("independent-and", [("/items/name", "Alice"), ("/items/state", "ready")], ["a.md", "b.md"]),
        ("child-scalar-array", [("/items/tags", "zig")], ["a.md"]),
        ("child-array-null", [("/items/tags", None)], ["a.md"]),
        ("escaped-keys", [("/items/a~1b/~0key", "literal")], ["h.md"]),
        ("empty-key", [("/items/", "empty")], ["h.md"]),
        ("digit-key-not-index", [("/items/0", "digit-key")], ["h.md"]),
        ("index-unsupported", [("/items/0/name", "Alice")], []),
        ("nested-object-array", [("/items/children/name", "子")], ["h.md"]),
        ("nested-array-values", [("/items/children/tags", "技術")], ["h.md"]),
        ("multiple-array-key-path", [("/items/name", "Carol")], ["i.md"]),
        ("multiple-array-nested-key-path", [("/items/children/name", "孫")], ["i.md"]),
        ("metadata-not-normalized", [("/items/name", "ALICE")], []),
    ]
    cases = [dict(id=case_id, filters=filters, terms=["BodyNeedle"], expected=expected, all=False) for case_id, filters, expected in definitions]
    cases.extend([
        dict(id="header-excluded", filters=[], terms=["HeaderOnlyNeedle"], expected=["g.md"], all=False),
        dict(id="one-character", filters=[("/items/name", "Alice")], terms=["検"], expected=["a.md", "b.md"], all=False),
        dict(id="body-and", filters=[("/items/name", "Alice")], terms=["検索", "Zig"], expected=["a.md", "b.md"], all=True),
        dict(id="no-hit", filters=[], terms=["ZZZ該当なし"], expected=[], all=False),
        dict(id="normalized-origin", filters=[("/items/name", "Alice")], terms=["ガ"], expected={"literal": [], "normalized": ["a.md"]}, all=False),
        dict(id="normalized-expansion", filters=[("/items/name", "Alice")], terms=["ロ"], expected={"literal": [], "normalized": ["b.md"]}, all=False),
    ])
    return cases


def expected_output(documents, case, mode, directory):
    selected = [doc for doc in documents if where_matches(doc["metadata"], case["filters"])]
    return base.original_output(selected, case["terms"], [], mode, directory)


def invalid_cases():
    cases = base.invalid_documents()
    for case_id, payload, reason in (
        ("duplicate-in-element", 'items: [{name: Alice, name: Bob}]\n', "InvalidFrontmatter"),
        ("escaped-duplicate-in-element", 'items: [{name: Alice, "na\\u006de": Bob}]\n', "InvalidFrontmatter"),
        ("nested-map-merge", 'items: [{"<<": value}]\n', "UnsupportedFrontmatter"),
        ("invalid-later-element", 'items: [{name: Alice}, {tagged: !!str value}]\n', "UnsupportedFrontmatter"),
    ):
        cases.append(dict(id=case_id, raw="---\n" + payload + "---\nNoQueryHere\n", error=reason))
    return cases


def resource_cases():
    cases = base.resource_cases()
    for maximum in (4096, 4097):
        metadata = {"items": [{} for _ in range(maximum - 3)]}
        payload = "items: [" + ", ".join("{}" for _ in metadata["items"]) + "]\n"
        cases.append(dict(id=f"object-nodes-{maximum}", documents=[base.document("a.md", payload, "BodyNeedle\n", metadata)], error="FrontmatterTooLarge" if maximum == 4097 else None))
    for depth in (16, 17):
        # root=1、sequence=2、要素map=3。残りは子mapと末尾scalar。
        value = "leaf"
        for _ in range(depth - 3):
            value = {"k": value}
        metadata = {"items": [value]}
        # JSONはYAMLの部分集合。手書きの型付き対応表と同じ値を符号化する。
        cases.append(dict(id=f"object-depth-{depth}", documents=[base.document("a.md", json.dumps(metadata) + "\n", "BodyNeedle\n", metadata)], error="FrontmatterTooLarge" if depth == 17 else None))
        value = "leaf"
        for _ in range(depth - 2):
            value = [value]
        metadata = {"items": value}
        cases.append(dict(id=f"array-depth-{depth}", documents=[base.document("a.md", json.dumps(metadata) + "\n", "BodyNeedle\n", metadata)], error="FrontmatterTooLarge" if depth == 17 else None))
    for maximum in (4096, 4097):
        key = "/" + "a" * (maximum - len("/items/") - 2)
        payload = "items:\n- ? " + json.dumps(key) + "\n  : value\n"
        cases.append(dict(id=f"object-pointer-{maximum}", documents=[base.document("a.md", payload, "BodyNeedle\n", {"items": [{key: "value"}]})], error="FrontmatterTooLarge" if maximum == 4097 else None))
    return cases


def run(args):
    root = args.output_root.resolve() / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    env = dict(os.environ, XDG_CONFIG_HOME=os.fspath(root / "empty-config"))
    records = root / "diagnostics"
    records.mkdir()
    raw = dict(schema_version=1, status="running", scope="synthetic-correctness-no-performance-measurement",
               started_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               git_head=benchmark.capture(["git", "rev-parse", "HEAD"]), git_status=benchmark.capture(["git", "status", "--short"]),
               git_diff_sha256=hashlib.sha256(json.dumps(benchmark.capture(["git", "diff", "--binary"]), sort_keys=True).encode()).hexdigest(),
               python_version=sys.version, binary_sha256=benchmark.file_hash(binary), runner_sha256=benchmark.file_hash(Path(__file__)),
               source_sha256={os.fspath(path.relative_to(REPOSITORY)): benchmark.file_hash(path) for path in
                              sorted([*REPOSITORY.glob("src/*.zig"), *REPOSITORY.glob("src/*.c"), REPOSITORY / "mise.toml", REPOSITORY / "build.zig"])},
               dependency_sha256={name: benchmark.file_hash(REPOSITORY / "scripts" / name) for name in
                                  ("evaluate_frontmatter.py", "evaluate_frontmatter_diagnostics.py", "benchmark_normalized.py")},
               inputs=[], checks=[])
    calls = 0

    def invoke(command):
        nonlocal calls
        process = subprocess.run(list(map(os.fspath, command)), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        (records / f"{calls:04d}.stdout").write_bytes(process.stdout)
        (records / f"{calls:04d}.stderr").write_bytes(process.stderr)
        calls += 1
        return process

    def success(command, expected=None):
        process = invoke(command)
        if process.returncode or process.stderr or (expected is not None and process.stdout != expected):
            raise ValueError(f"unexpected successful output at diagnostic {calls - 1}")
        return process.stdout

    def prepare(name, documents, mode="literal"):
        directory = root / name
        directory.mkdir()
        for doc in documents:
            (directory / doc["path"]).write_bytes(doc["raw"].encode())
        raw["inputs"].append(dict(suite=name, documents=[dict(path=doc["path"], bytes=len(doc["raw"].encode()),
                                                               sha256=hashlib.sha256(doc["raw"].encode()).hexdigest()) for doc in documents]))
        index = root / (name + ".zgi")
        success([binary, "index", "--mode", mode, directory, index])
        return directory, index

    try:
        for name, docs, cases, oracle in (("objects", fixture_documents(), fixture_cases(), expected_schema),
                                          ("scalar-regression", base.fixture_documents(), base.fixture_cases(), base.expected_schema)):
            for index_mode in ("literal", "normalized"):
                directory, index = prepare(name + "-" + index_mode, docs, index_mode)
                if json.loads(success([binary, "schema", index])) != oracle(docs):
                    raise ValueError(f"schema differs for {name}/{index_mode}")
                raw["checks"].append(dict(suite=name, check="schema", index_mode=index_mode, passed=True))
                for mode in ("literal", "normalized"):
                    if index_mode == "literal" and mode == "normalized":
                        process = invoke(base.search_command(binary, "search", mode, index, ["BodyNeedle"]))
                        if process.returncode != 1 or process.stdout or b"IndexModeMismatch" not in process.stderr:
                            raise ValueError("literal index must reject normalized query")
                        raw["checks"].append(dict(suite=name, check="index-mode-mismatch", index_mode=index_mode, mode=mode, passed=True))
                        continue
                    for case in cases:
                        wanted, paths = expected_output(docs, case, mode, directory)
                        expected = case["expected"][mode] if type(case["expected"]) is dict else case["expected"]
                        if paths != expected:
                            raise ValueError("manual expectation differs: " + case["id"])
                        for method, target in (("search", index), ("scan", directory)):
                            success(base.search_command(binary, method, mode, target, case["terms"], filters=case["filters"], all_terms=case["all"]), wanted)
                            raw["checks"].append(dict(suite=name, check=case["id"], index_mode=index_mode, mode=mode, method=method, passed=True))
        for case in invalid_cases():
            docs = [base.document("00-good.md", 'state: ready\n', "BodyNeedle\n", {"state": "ready"}),
                    dict(path="01-bad.md", raw=case["raw"], metadata=None),
                    base.document("02-good.md", 'state: ready\n', "BodyNeedle after\n", {"state": "ready"})]
            directory, index = prepare("invalid-" + case["id"], docs)
            for method, target in (("search", index), ("scan", directory)):
                for filters in ([], [("/state", "ready")], [("file:/name", "00-good.md")]):
                    process = invoke(base.search_command(binary, method, "literal", target, ["BodyNeedle"], filters=filters))
                    diagnostics.check_diagnostic(process.returncode, process.stdout, process.stderr, directory / "01-bad.md", case["error"])
                    raw["checks"].append(dict(suite="invalid", check=case["id"], method=method, passed=True))
            process = invoke([binary, "schema", index])
            diagnostics.check_diagnostic(process.returncode, process.stdout, process.stderr, directory / "01-bad.md", case["error"])
            raw["checks"].append(dict(suite="invalid", check=case["id"], method="schema", passed=True))
        for case in resource_cases():
            directory, index = prepare("resource-" + case["id"], case["documents"])
            process = invoke([binary, "schema", index])
            if case["error"]:
                if process.returncode != 1 or process.stdout or case["error"].encode() not in process.stderr:
                    raise ValueError("resource rejection differs: " + case["id"])
            elif process.returncode or process.stderr or json.loads(process.stdout) != expected_schema(case["documents"]):
                raise ValueError("resource schema differs: " + case["id"])
            search_error = case["error"] if not case.get("schema_only_limit") else None
            for method, target in (("search", index), ("scan", directory)):
                command = base.search_command(binary, method, "literal", target, ["BodyNeedle"])
                if search_error:
                    process = invoke(command)
                    if process.returncode != 1 or process.stdout or search_error.encode() not in process.stderr:
                        raise ValueError("resource search rejection differs: " + case["id"])
                else:
                    wanted, _ = base.original_output(case["documents"], ["BodyNeedle"], [], "literal", directory)
                    success(command, wanted)
            raw["checks"].append(dict(suite="resource", check=case["id"], passed=True))
        raw["binary_unchanged"] = benchmark.file_hash(binary) == raw["binary_sha256"]
        raw["inputs_unchanged"] = all(benchmark.file_hash(root / group["suite"] / item["path"]) == item["sha256"]
                                      for group in raw["inputs"] for item in group["documents"])
        if not raw["binary_unchanged"] or not raw["inputs_unchanged"]:
            raise ValueError("binary or inputs changed")
        raw["status"] = "complete"
    except BaseException as error:
        raw["status"] = "failed"
        raw["failure"] = dict(type=type(error).__name__, message=str(error))
        raise
    finally:
        raw["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        raw["cli_calls"] = calls
        (root / "results.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        print(root / "results.json")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / ".local/benchmarks")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.run_id):
        parser.error("safe run-id required")
    return args


if __name__ == "__main__":
    run(arguments())
