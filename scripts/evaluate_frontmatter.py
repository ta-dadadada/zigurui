#!/usr/bin/env python3
"""手書きの型付きmetadata対応表でfrontmatterのschemaと検索を独立評価する。"""
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
import subprocess
import unicodedata

import benchmark
import benchmark_corpus
import benchmark_normalized as normalized

REPOSITORY = Path(__file__).resolve().parent.parent
CONFIGURATIONS = normalized.CONFIGURATIONS
MISSING = object()


def scalar_type(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float and math.isfinite(value):
        return "float"
    if type(value) is str:
        return "string"
    raise ValueError("not a finite scalar")


def escape(part):
    return part.replace("~", "~0").replace("/", "~1")


def resolve(metadata, pointer):
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("invalid field pointer")
    current = metadata
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if type(current) is not dict or part not in current:
            return MISSING
        current = current[part]
    return current


def exact_scalar(actual, wanted):
    if actual is MISSING or type(actual) in (dict, list):
        return False
    return scalar_type(actual) == scalar_type(wanted) and actual == wanted


def where_matches(metadata, filters):
    for pointer, wanted in filters:
        actual = resolve(metadata, pointer)
        if type(actual) is list:
            if not any(exact_scalar(item, wanted) for item in actual):
                return False
        elif not exact_scalar(actual, wanted):
            return False
    return True


def expected_builtin_fields(document_count):
    return [{"selector": selector, "types": ["string"], "present": document_count, "missing": 0,
             "null_count": 0, "element_types": []} for selector in ("file:/name", "file:/path")]


def expected_schema(documents):
    """YAMLを再解析せず、明示された型付き対応表だけからschemaを集計する。"""
    fields = {}

    def visit(mapping, prefix):
        for key, value in mapping.items():
            pointer = prefix + "/" + escape(key)
            item = fields.setdefault(pointer, {"types": set(), "present": 0, "null_count": 0, "element_types": set()})
            item["present"] += 1
            kind = "object" if type(value) is dict else "array" if type(value) is list else scalar_type(value)
            item["types"].add(kind)
            item["null_count"] += value is None
            if type(value) is list:
                item["element_types"].update(map(scalar_type, value))
            elif type(value) is dict:
                visit(value, pointer)

    for document in documents:
        if document["metadata"] is not None:
            visit(document["metadata"], "")
    return {
        "document_count": len(documents), "frontmatter_count": sum(doc["metadata"] is not None for doc in documents),
        "builtin_fields": expected_builtin_fields(len(documents)),
        "fields": [{"pointer": pointer, "types": sorted(item["types"]), "present": item["present"],
                    "missing": len(documents) - item["present"], "null_count": item["null_count"],
                    "element_types": sorted(item["element_types"])}
                   for pointer, item in sorted(fields.items(), key=lambda pair: pair[0].encode("utf-8"))],
    }


def document(path, payload, body, metadata, closing="---", crlf=False, bom=False):
    if payload is None:
        return {"path": path, "raw": body, "body": body, "metadata": None, "body_offset": 0}
    newline = "\r\n" if crlf else "\n"
    header = payload.replace("\n", newline)
    prefix = ("\ufeff" if bom else "") + "---" + newline + header + closing + newline
    return {"path": path, "raw": prefix + body, "body": body, "metadata": metadata, "body_offset": len(prefix.encode())}


def fixture_documents():
    payload_a = (
        'title: "HeaderNeedle"\nstatus: draft\ncount: 1\nratio: 1.0\nflag: true\nnullable:\n'
        'tags: [zig, 1, null, false, 1.0]\nnested:\n  owner: "Alice"\n'
        '"a/b":\n  "~key": literal\n"=": equal\n"": empty-key\ndate: 2026-11-14\n'
        'quoted: "true"\nquoted_empty: ""\nlegacy: yes\ncase: True\nleading: 01\nlarge: 9223372036854775807\nzero: 0.0e-999\nnonjsonfloat: .nan\n'
        'empty_list: []\nempty_map: {}\nblock: |\n  block line\n'
    )
    meta_a = {"title": "HeaderNeedle", "status": "draft", "count": 1, "ratio": 1.0, "flag": True, "nullable": None,
              "tags": ["zig", 1, None, False, 1.0], "nested": {"owner": "Alice"}, "a/b": {"~key": "literal"},
              "=": "equal", "": "empty-key", "date": "2026-11-14", "quoted": "true", "quoted_empty": "", "legacy": "yes", "case": "True",
              "leading": "01", "large": 9223372036854775807, "zero": 0.0, "nonjsonfloat": ".nan", "empty_list": [], "empty_map": {}, "block": "block line\n"}
    payload_b = 'title: B\nstatus: null\ncount: 1.0\nratio: 1\nflag: "true"\nnullable: ~\ntags: ["1", "null", "true"]\nnested:\n  owner:\ndate: "2026-11-14"\nlarge: -9223372036854775808\n'
    meta_b = {"title": "B", "status": None, "count": 1.0, "ratio": 1, "flag": "true", "nullable": None,
              "tags": ["1", "null", "true"], "nested": {"owner": None}, "date": "2026-11-14", "large": -9223372036854775808}
    return [
        document("a.md", payload_a, "前ｶﾞ後 BodyNeedle\nZigで検索\n", meta_a),
        document("b.md", payload_b, "BodyNeedle 前㌔後\r\n検索 Zig\r\n", meta_b, crlf=True, bom=True),
        document("c.md", None, "# plain\nHeaderNeedle BodyNeedle\n", None),
        document("d.md", "", "EmptyBody\n", {}),
        document("e.md", "status: published\ncount: 2\nflag: false\ntags: [zig, note]\nnum: 1e0\nsigned: -1\nlegacy: on\n", "BodyNeedle Zig\n検索のメモ\n",
                 {"status": "published", "count": 2, "flag": False, "tags": ["zig", "note"], "num": 1.0, "signed": -1, "legacy": "on"}, closing="..."),
        document("f.md", 'status: "null"\ncount: "1"\nflag: ~\ntags: zig\nnullable: "null"\n', "BodyNeedle\n", {"status": "null", "count": "1", "flag": None, "tags": "zig", "nullable": "null"}),
        document("g.md", "tags: [1]\n", "BodyNeedle\n", {"tags": [1]}),
        document("h.md", "tags: [1.0]\n", "BodyNeedle\n", {"tags": [1.0]}),
        document("i.md", "tags: [true]\n", "BodyNeedle\n", {"tags": [True]}),
        document("j.md", "tags: [null]\n", "BodyNeedle\n", {"tags": [None]}),
        document("k.md", None, "--- not a header\nBodyNeedle\n", None),
        document("l.md", "# comment-only header\n", "BodyNeedle\n", {}),
    ]


def fixture_cases():
    definitions = (
        ("body-only", [], ["HeaderNeedle"], ["c.md"]),
        ("draft", [("/status", "draft")], ["BodyNeedle"], ["a.md"]),
        ("null-not-missing", [("/nullable", None)], ["BodyNeedle"], ["a.md", "b.md"]),
        ("missing-not-null", [("/absent", None)], ["BodyNeedle"], []),
        ("empty-array-not-null", [("/empty_list", None)], ["BodyNeedle"], []),
        ("object-not-null", [("/empty_map", None)], ["BodyNeedle"], []),
        ("status-null", [("/status", None)], ["BodyNeedle"], ["b.md"]),
        ("status-string-null", [("/status", "null")], ["BodyNeedle"], ["f.md"]),
        ("integer", [("/count", 1)], ["BodyNeedle"], ["a.md"]),
        ("float", [("/count", 1.0)], ["BodyNeedle"], ["b.md"]),
        ("string-number", [("/count", "1")], ["BodyNeedle"], ["f.md"]),
        ("boolean", [("/flag", True)], ["BodyNeedle"], ["a.md"]),
        ("quoted-boolean", [("/flag", "true")], ["BodyNeedle"], ["b.md"]),
        ("false", [("/flag", False)], ["BodyNeedle"], ["e.md"]),
        ("array-integer", [("/tags", 1)], ["BodyNeedle"], ["a.md", "g.md"]),
        ("array-float", [("/tags", 1.0)], ["BodyNeedle"], ["a.md", "h.md"]),
        ("array-boolean", [("/tags", True)], ["BodyNeedle"], ["i.md"]),
        ("array-null", [("/tags", None)], ["BodyNeedle"], ["a.md", "j.md"]),
        ("array-string-number", [("/tags", "1")], ["BodyNeedle"], ["b.md"]),
        ("array-or-scalar", [("/tags", "zig")], ["BodyNeedle"], ["a.md", "e.md", "f.md"]),
        ("nested", [("/nested/owner", "Alice")], ["BodyNeedle"], ["a.md"]),
        ("nested-null", [("/nested/owner", None)], ["BodyNeedle"], ["b.md"]),
        ("escaped-key", [("/a~1b/~0key", "literal")], ["BodyNeedle"], ["a.md"]),
        ("equals-key", [("/=", "equal")], ["BodyNeedle"], ["a.md"]),
        ("empty-key", [("/", "empty-key")], ["BodyNeedle"], ["a.md"]),
        ("date-string", [("/date", "2026-11-14")], ["BodyNeedle"], ["a.md", "b.md"]),
        ("block-string", [("/block", "block line\n")], ["BodyNeedle"], ["a.md"]),
        ("quoted-empty-string", [("/quoted_empty", "")], ["BodyNeedle"], ["a.md"]),
        ("legacy-string", [("/legacy", "yes")], ["BodyNeedle"], ["a.md"]),
        ("case-string", [("/case", "True")], ["BodyNeedle"], ["a.md"]),
        ("leading-zero-string", [("/leading", "01")], ["BodyNeedle"], ["a.md"]),
        ("integer-maximum", [("/large", 9223372036854775807)], ["BodyNeedle"], ["a.md"]),
        ("integer-minimum", [("/large", -9223372036854775808)], ["BodyNeedle"], ["b.md"]),
        ("float-zero-exponent", [("/zero", 0.0)], ["BodyNeedle"], ["a.md"]),
        ("non-json-number-string", [("/nonjsonfloat", ".nan")], ["BodyNeedle"], ["a.md"]),
        ("filter-and", [("/status", "draft"), ("/tags", "zig")], ["BodyNeedle"], ["a.md"]),
        ("metadata-exact-case", [("/status", "DRAFT")], ["BodyNeedle"], []),
        ("option-looking-body-term", [], ["--where"], []),
    )
    cases = [{"id": case_id, "filters": filters, "terms": terms, "expected": paths, "all": False} for case_id, filters, terms, paths in definitions]
    cases.extend([
        {"id": "body-and", "filters": [], "terms": ["検索", "Zig"], "all": True, "expected": ["a.md", "b.md", "e.md"]},
        {"id": "normalized-body-origin", "filters": [], "terms": ["ガ"], "all": False, "expected": {"literal": [], "normalized": ["a.md"]}},
        {"id": "normalized-expansion-origin", "filters": [("/count", 1.0)], "terms": ["ロ"], "all": False, "expected": {"literal": [], "normalized": ["b.md"]}},
    ])
    return cases


def original_output(documents, terms, filters, mode, root, frontmatter=True):
    """小さいfixture用。各候補行は96CP以内、語開始は行頭32CP以内に限定する。"""
    needles = list(dict.fromkeys(normalized.transform(term, mode) for term in terms))
    result = []
    paths = []
    for doc in sorted(documents, key=lambda item: item["path"]):
        if not where_matches(doc["metadata"], filters):
            continue
        text = doc["body"] if frontmatter else doc["raw"]
        starts = []
        for needle in needles:
            for number, line in enumerate(text.split("\n"), 1):
                if needle in normalized.transform(line, mode):
                    starts.append(number)
                    break
            else:
                break
        else:
            line_number = min(starts)
            line = text.split("\n")[line_number - 1]
            if len(line) > 96:
                raise ValueError("fixture line exceeds simple excerpt oracle scope")
            prefix_lines = doc["raw"].encode()[:doc["body_offset"]].count(b"\n") if frontmatter else 0
            excerpt = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in line)
            result.append(f"{root / doc['path']}:{prefix_lines + line_number}: {excerpt}\n")
            paths.append(doc["path"])
    return "".join(result).encode(), paths


def search_command(binary, method, mode, target, terms, frontmatter=True, filters=(), all_terms=False):
    arguments = [binary, method, "--mode", mode]
    if frontmatter:
        arguments.append("--frontmatter")
    if all_terms:
        arguments.append("--all")
    for pointer, value in filters:
        arguments.extend(["--where", pointer, json.dumps(value, ensure_ascii=False, allow_nan=False)])
    return [*arguments, target, *terms]


def performance_documents(root, size, seed):
    manifest, texts = normalized.corpus(root, size, seed)
    documents = []
    digest = hashlib.sha256()
    for number, entry in enumerate(manifest["documents"]):
        group = "even" if number % 2 == 0 else "odd"
        payload = f"group: {group}\nid: {number}\ntags: [synthetic, {group}]\nheader: HeaderOnlyNeedle\n"
        doc = document(entry["path"], payload, texts[entry["path"]], {"group": group, "id": number, "tags": ["synthetic", group], "header": "HeaderOnlyNeedle"})
        data = doc["raw"].encode()
        (root / "documents" / entry["path"]).write_bytes(data)
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        digest.update(entry["path"].encode() + b"\x00" + data + b"\x00")
        documents.append(doc)
    manifest.update(generator="zigurui-frontmatter-synthetic-v1", total_bytes=sum(item["bytes"] for item in manifest["documents"]), corpus_sha256=digest.hexdigest())
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest, documents


PERFORMANCE_CASES = (
    {"id": "raw", "terms": ["ガラス"], "filters": [], "frontmatter": False, "all": False},
    {"id": "body-only", "terms": ["ガラス"], "filters": [], "frontmatter": True, "all": False},
    {"id": "half", "terms": ["ガラス"], "filters": [("/group", "even")], "frontmatter": True, "all": False},
    {"id": "rare", "terms": ["ガラス"], "filters": [("/id", 0)], "frontmatter": True, "all": False},
    {"id": "none", "terms": ["ガラス"], "filters": [("/id", -1)], "frontmatter": True, "all": False},
    {"id": "typed-float-none", "terms": ["ガラス"], "filters": [("/id", 0.0)], "frontmatter": True, "all": False},
    {"id": "and-half", "terms": ["ガラス", "東京", "駅前"], "filters": [("/group", "even")], "frontmatter": True, "all": True},
    {"id": "all-one-half", "terms": ["ガ", "希"], "filters": [("/group", "even")], "frontmatter": True, "all": True},
    {"id": "header-only", "terms": ["HeaderOnlyNeedle"], "filters": [], "frontmatter": True, "all": False},
)


def expected_performance(documents, case, configuration, method):
    mode = configuration["query_mode"]
    needles = list(dict.fromkeys(normalized.transform(term, mode) for term in case["terms"]))
    raw_texts = {doc["path"]: normalized.transform(doc["raw"], mode) for doc in documents}
    grams = {needle[i:i + 2] for needle in needles for i in range(len(needle) - 1)}
    scan = method == "scan" or configuration["index_mode"] != mode or not grams
    if scan:
        shortest, candidate_count, candidates = 0, 0, set(raw_texts)
    else:
        postings = [{path for path, text in raw_texts.items() if gram in text} for gram in grams]
        shortest, candidates = min(map(len, postings)), set.intersection(*postings)
        candidate_count = len(candidates)
    verified = [doc for doc in documents if doc["path"] in candidates and where_matches(doc["metadata"], case["filters"])]
    hits = []
    for doc in documents:
        if not where_matches(doc["metadata"], case["filters"]):
            continue
        text = doc["body"] if case["frontmatter"] else doc["raw"]
        lines = []
        for needle in needles:
            for number, line in enumerate(text.split("\n"), 1):
                if needle in normalized.transform(line, mode):
                    lines.append(number)
                    break
            else:
                break
        else:
            prefix = doc["raw"].encode()[:doc["body_offset"]].count(b"\n") if case["frontmatter"] else 0
            hits.append((doc["path"], prefix + min(lines)))
    texts = [(doc["body"] if case["frontmatter"] else doc["raw"]) for doc in verified]
    return sorted(hits), {
        "shortest_posting_count": shortest, "candidate_count": candidate_count, "documents_verified": len(verified), "hit_count": len(hits),
        "normalized_document_count": len(verified) if mode == "normalized" else 0,
        "normalized_input_bytes": sum(len(text.encode()) for text in texts) if mode == "normalized" else 0,
    }


def invalid_documents():
    definitions = (
        ("unclosed", "---\nx: 1\n", "InvalidFrontmatter"),
        ("nonexact-close", "---\nx: 1\n--- \n", "InvalidFrontmatter"),
        ("malformed", "---\nstatus: archived\nx: [1, 2\n---\n", "InvalidFrontmatter"),
        ("duplicate", "---\nx: 1\n\"x\": 2\n---\n", "InvalidFrontmatter"),
        ("duplicate-nested", "---\nx:\n  y: 1\n  y: 2\n---\n", "InvalidFrontmatter"),
        ("root-sequence", "---\n- one\n- two\n---\n", "UnsupportedFrontmatter"),
        ("root-scalar", "---\nhello\n---\n", "UnsupportedFrontmatter"),
        ("anchor", "---\nx: &a 1\n---\n", "UnsupportedFrontmatter"),
        ("alias", "---\nx: *a\n---\n", "UnsupportedFrontmatter"),
        ("tag", "---\nx: !!str 1\n---\n", "UnsupportedFrontmatter"),
        ("directive", "---\n%YAML 1.1\n--- # yaml-document\nx: 1\n---\n", "UnsupportedFrontmatter"),
        ("merge", "---\n<<: value\n---\n", "UnsupportedFrontmatter"),
        ("array-object", "---\nx: [{a: 1}]\n---\n", "UnsupportedFrontmatter"),
        ("array-array", "---\nx: [[1]]\n---\n", "UnsupportedFrontmatter"),
        ("integer-overflow", "---\nx: 9223372036854775808\n---\n", "FrontmatterNumberOutOfRange"),
        ("integer-underflow", "---\nx: -9223372036854775809\n---\n", "FrontmatterNumberOutOfRange"),
        ("float-overflow", "---\nx: 1e309\n---\n", "FrontmatterNumberOutOfRange"),
        ("float-underflow", "---\nx: 1e-999\n---\n", "FrontmatterNumberOutOfRange"),
    )
    return [{"id": case_id, "raw": raw + "NoQueryHere\n", "error": error} for case_id, raw, error in definitions]


def resource_cases():
    cases = []
    limit = 256 * 1024
    for extra in (0, 1):
        payload = 'pad: "' + "x" * (limit + extra - len('pad: ""\n')) + '"\n'
        assert len(payload.encode()) == limit + extra
        cases.append({"id": f"header-{extra}", "documents": [document("a.md", payload, "BodyNeedle\n", {"pad": "x" * (limit + extra - len('pad: ""\n'))})],
                      "error": "FrontmatterTooLarge" if extra else None})
    for depth in (16, 17):
        # root=1、14個の子mapと末尾scalarで最大深さ16。
        pairs = depth - 1
        payload = "".join("  " * number + f"k{number}:" + (" value\n" if number == pairs - 1 else "\n") for number in range(pairs))
        metadata = "value"
        for number in reversed(range(pairs)):
            metadata = {f"k{number}": metadata}
        cases.append({"id": f"depth-{depth}", "documents": [document("a.md", payload, "BodyNeedle\n", metadata)], "error": "FrontmatterTooLarge" if depth == 17 else None})
    for extra in (0, 1):
        metadata = {f"k{number}": (["x"] * (1 + extra) if number == 0 else "x") for number in range(2047)}
        payload = "".join(f"{key}: " + ("[" + ", ".join(value) + "]" if type(value) is list else value) + "\n" for key, value in metadata.items())
        # root1 + keys2047 + scalar values2046 + sequence1 + elements(1+extra)。
        cases.append({"id": f"nodes-{4096 + extra}", "documents": [document("a.md", payload, "BodyNeedle\n", metadata)], "error": "FrontmatterTooLarge" if extra else None})
    for extra in (0, 1):
        key = "/" + "a" * (4093 + extra)
        assert len(("/" + escape(key)).encode()) == 4096 + extra
        # 長いimplicit keyの構文上限を避け、製品のencoded pointer上限だけを通す。
        cases.append({"id": f"pointer-{4096 + extra}", "documents": [document("a.md", "? " + json.dumps(key) + "\n: value\n", "BodyNeedle\n", {key: "value"})], "error": "FrontmatterTooLarge" if extra else None})
    for extra in (0, 1):
        documents = []
        for shard, (begin, end) in enumerate(((0, 2047), (2047, 4094), (4094, 4096 + extra))):
            metadata = {f"k{number}": "x" for number in range(begin, end)}
            payload = "".join(f"{key}: x\n" for key in metadata)
            documents.append(document(f"{shard}.md", payload, "BodyNeedle\n", metadata))
        cases.append({"id": f"schema-paths-{4096 + extra}", "documents": documents,
                      "error": "FrontmatterTooLarge" if extra else None, "schema_only_limit": True})
    return cases


def project(raw):
    if raw["status"] != "complete":
        raise ValueError("incomplete evaluation")
    output = {
        "schema_version": 1, "status": "complete", "scope": "synthetic-smoke",
        "limitations": ["Host-wide load was not controlled.", "Byte spans are not exposed by the CLI; original line/excerpt is checked here and byte spans are covered by Zig tests."],
        "configuration": {key: raw["configuration"][key] for key in ("size", "seed", "index_repeats", "warmups", "repeats")},
        "binary_sha256": {key: raw["binary_sha256"][key] for key in ("plain", "profile", "baseline")},
        "quality": {key: raw["quality"][key] for key in ("passed", "schema_passed", "search_cases", "invalid_document_cases", "invalid_filter_cases", "resource_cases", "raw_compatibility_cases", "index_identity")},
        "schema": {"document_count": raw["quality"]["schema"]["document_count"], "frontmatter_count": raw["quality"]["schema"]["frontmatter_count"], "fields": [
            {key: field[key] for key in ("pointer", "types", "present", "missing", "null_count", "element_types")} for field in raw["quality"]["schema"]["fields"]]},
        "corpus_sha256": raw["corpus"]["corpus_sha256"], "corpus_bytes": raw["corpus"]["total_bytes"],
        "binary_unchanged": raw["binary_unchanged"], "cases": [
            {key: case[key] for key in ("id", "terms", "filters", "frontmatter", "all")} for case in PERFORMANCE_CASES],
        "results": [], "indices": [], "schema_cli": benchmark.summarize(raw["schema_cli"]["samples"]),
    }
    if "builtin_fields" in raw["quality"]["schema"]:
        output["schema"]["builtin_fields"] = [{key: field[key] for key in ("selector", "types", "present", "missing", "null_count", "element_types")}
                                               for field in raw["quality"]["schema"]["builtin_fields"]]
    for index in raw["indices"]:
        output["indices"].append({**{key: index[key] for key in ("mode", "instrumentation", "format_version", "index_bytes", "index_sha256")},
                                  "summary": {key: index["summary"][key] for key in normalized.SUMMARY_FIELDS}})
    for record in raw["results"]:
        item = {key: record[key] for key in ("configuration_id", "case_id", "method")}
        item["expected_counts"] = {key: record["expected_counts"][key] for key in (*normalized.EXPECTED_COUNTS, "normalized_document_count", "normalized_input_bytes")}
        for instrumentation in ("plain", "profile"):
            item[instrumentation] = benchmark.summarize(record[instrumentation]["samples"])
        item["profile_counts"] = {key: normalized.numeric_summary([sample["profile"]["counts"][key] for sample in record["profile"]["samples"]]) for key in normalized.COUNTS}
        item["profile_timings_ns"] = {key: normalized.numeric_summary([sample["profile"]["timings_ns"][key] for sample in record["profile"]["samples"]]) for key in normalized.PHASES}
        output["results"].append(item)
    text = json.dumps(output, ensure_ascii=False)
    if any(marker in text for marker in ("/Users/", "/home/", "/private/", "hostname", "cpu_model", "MacBook", "architecture")):
        raise ValueError("private marker in public projection")
    return output


def run(args):
    root = args.output_root.resolve() / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir()
    indices = root / "indices"
    indices.mkdir()
    binaries = {"plain": args.plain.resolve(), "profile": args.profile.resolve()}
    baseline = args.baseline.resolve() if args.baseline else None
    raw = {
        "schema_version": 1, "status": "running", "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "configuration": {key: getattr(args, key) for key in ("size", "seed", "index_repeats", "warmups", "repeats")},
        "binary_sha256": {**{key: benchmark.file_hash(path) for key, path in binaries.items()}, "baseline": benchmark.file_hash(baseline) if baseline else None},
        "environment": {"checkout": benchmark.environment(binaries["plain"]), "python_unicode_version": unicodedata.unidata_version},
        "provenance": json.loads(args.provenance.read_text()) if args.provenance else None,
        "results": [], "indices": [],
    }
    source_paths = [*sorted((REPOSITORY / "src").glob("*.zig")), *sorted((REPOSITORY / "src").glob("*.c")),
                    *sorted((REPOSITORY / "vendor").rglob("*.c")), *sorted((REPOSITORY / "vendor").rglob("*.h")),
                    REPOSITORY / "build.zig", REPOSITORY / "build.zig.zon", REPOSITORY / "mise.toml"]
    raw["environment"]["source_sha256"] = {str(path.relative_to(REPOSITORY)): benchmark.file_hash(path) for path in source_paths}
    raw["environment"]["runner_sha256"] = {str(path.relative_to(REPOSITORY)): benchmark.file_hash(path) for path in (Path(__file__), Path(normalized.__file__), Path(benchmark.__file__), Path(benchmark_corpus.__file__))}
    order = 0

    def correctness(arguments, success=True, error=None):
        nonlocal order
        result = subprocess.run(list(map(os.fspath, arguments)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout)
        (diagnostics / f"{order:06d}.stdout").write_bytes(result.stdout)
        (diagnostics / f"{order:06d}.stderr").write_bytes(result.stderr)
        order += 1
        if (result.returncode == 0) != success or (error and error.encode() not in result.stderr):
            raise ValueError(f"unexpected command result, expected {error or 'success'} at diagnostic {order - 1}")
        if not success and result.stdout:
            raise ValueError(f"failed command emitted partial stdout at diagnostic {order - 1}")
        return result.stdout, result.stderr

    def measured(arguments):
        nonlocal order
        sample, diagnostic, _ = normalized.execute(arguments, diagnostics / f"{order:06d}.stderr", args.timeout)
        sample["order"] = order
        order += 1
        return sample, diagnostic

    def write_documents(directory, documents):
        directory.mkdir()
        for doc in documents:
            (directory / doc["path"]).write_bytes(doc["raw"].encode())

    def build(directory, name, binary=binaries["plain"], mode="literal"):
        index = indices / f"{name}.zgi"
        correctness([binary, "index", "--mode", mode, directory, index])
        return index

    result_path = root / "results.json"

    def save():
        result_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        documents = fixture_documents()
        quality_root = root / "quality"
        write_documents(quality_root, documents)
        wanted_schema = expected_schema(documents)
        raw["quality_expected_schema"] = wanted_schema
        quality_indices = {}
        for mode in ("literal", "normalized"):
            for instrumentation, binary in binaries.items():
                index = build(quality_root, f"quality-{mode}-{instrumentation}", binary, mode)
                quality_indices[mode, instrumentation] = index
                raw["current_check"] = f"schema/{mode}/{instrumentation}"
                stdout, _ = correctness([binary, "schema", index])
                if json.loads(stdout) != wanted_schema:
                    raise ValueError(f"schema differs from handwritten metadata: {mode}/{instrumentation}")
        search_cases = 0
        for configuration in CONFIGURATIONS:
            mode = configuration["query_mode"]
            for case in fixture_cases():
                raw["current_check"] = f"quality/{configuration['id']}/{case['id']}"
                wanted, wanted_paths = original_output(documents, case["terms"], case["filters"], mode, quality_root)
                explicit = case["expected"][mode] if type(case["expected"]) is dict else case["expected"]
                if wanted_paths != explicit:
                    raise ValueError(f"handwritten fixture and independent typed oracle disagree: {case['id']}")
                for instrumentation, binary in binaries.items():
                    for method in ("search", "scan"):
                        target = quality_indices[configuration["index_mode"], instrumentation] if method == "search" else quality_root
                        stdout, _ = correctness(search_command(binary, method, mode, target, case["terms"], filters=case["filters"], all_terms=case["all"]))
                        if stdout != wanted:
                            raise ValueError(f"frontmatter CLI fixture differs: {configuration['id']}/{case['id']}/{method}/{instrumentation}")
                search_cases += 1
        # --mode以外のinput前オプションは並べ替えても同じ条件になる。
        wanted, _ = original_output(documents, ["BodyNeedle", "ガ"], [("/status", "draft")], "normalized", quality_root)
        for instrumentation, binary in binaries.items():
            for method in ("search", "scan"):
                target = quality_indices["normalized", instrumentation] if method == "search" else quality_root
                stdout, _ = correctness([binary, method, "--mode", "normalized", "--where", "/status", '"draft"',
                                         "--all", "--frontmatter", target, "BodyNeedle", "ガ"])
                if stdout != wanted:
                    raise ValueError("frontmatter option order changes the search contract")
        search_cases += 1
        invalid_document_cases = 0
        for invalid in invalid_documents():
            raw["current_check"] = "invalid/" + invalid["id"]
            directory = root / ("invalid-" + invalid["id"])
            write_documents(directory, [document("good.md", None, "BodyNeedle\n", None),
                                        {"path": "bad.md", "raw": invalid["raw"]}])
            index = build(directory, "invalid-" + invalid["id"])
            for binary in binaries.values():
                correctness([binary, "schema", index], success=False, error=invalid["error"])
                for method, target in (("search", index), ("scan", directory)):
                    # 不正文書は検索語のposting候補に入らないが、解析エラーは隠さない。
                    for filters in ([], [("/status", "never")]):
                        correctness(search_command(binary, method, "literal", target, ["BodyNeedle"], filters=filters), success=False, error=invalid["error"])
                        invalid_document_cases += 1
            correctness(search_command(binaries["plain"], "search", "literal", index, ["BodyNeedle"], frontmatter=False))
        invalid_filter_cases = 0
        bad_filters = (("", "1", "InvalidFilter"), ("no-leading-slash", "1", "InvalidFilter"), ("/a~2b", "1", "InvalidFilter"),
                       ("/status", "{}", "InvalidFilter"), ("/status", "[]", "InvalidFilter"), ("/status", "not-json", "InvalidFilter"),
                       ("/status", "9223372036854775808", "FrontmatterNumberOutOfRange"), ("/status", "1e309", "FrontmatterNumberOutOfRange"),
                       ("/status", "1e-999", "FrontmatterNumberOutOfRange"))
        for binary in binaries.values():
            for method in ("search", "scan"):
                target = quality_indices["literal", "plain"] if method == "search" else quality_root
                for pointer, value, error in bad_filters:
                    correctness([binary, method, "--frontmatter", "--where", pointer, value, target, "BodyNeedle"], success=False, error=error)
                    invalid_filter_cases += 1
                correctness([binary, method, "--where", "/status", '"draft"', target, "BodyNeedle"], success=False, error="InvalidArguments")
                invalid_filter_cases += 1
                sixty_four = [("/status", "draft")] * 64
                wanted, _ = original_output(documents, ["BodyNeedle"], sixty_four, "literal", quality_root)
                stdout, _ = correctness(search_command(binary, method, "literal", target, ["BodyNeedle"], filters=sixty_four))
                if stdout != wanted:
                    raise ValueError("64 where conditions should succeed")
                correctness(search_command(binary, method, "literal", target, ["BodyNeedle"], filters=[*sixty_four, ("/status", "draft")]), success=False, error="InvalidFilter")
                invalid_filter_cases += 1
        resource_results = []
        for case in resource_cases():
            raw["current_check"] = "resource/" + case["id"]
            directory = root / case["id"]
            write_documents(directory, case["documents"])
            index = build(directory, case["id"])
            stdout, _ = correctness([binaries["plain"], "schema", index], success=case["error"] is None, error=case["error"])
            if case["error"] is None and json.loads(stdout) != expected_schema(case["documents"]):
                raise ValueError(f"resource-boundary schema differs: {case['id']}")
            # unique pathsはschema全体だけの上限。文書単位のsearchではunionを保持しない。
            search_error = case["error"] if not case.get("schema_only_limit") else None
            for method, target in (("search", index), ("scan", directory)):
                correctness(search_command(binaries["plain"], method, "literal", target, ["BodyNeedle"]), success=search_error is None, error=search_error)
            resource_results.append({"id": case["id"], "passed": True, "expected_error": case["error"], "schema_only_limit": case.get("schema_only_limit", False)})
        manifest, performance_docs = performance_documents(root / "corpus", args.size, args.seed)
        raw["current_check"] = "performance-corpus"
        raw["corpus"] = manifest
        performance_root = root / "corpus" / "documents"
        index_paths = {}
        for mode in ("literal", "normalized"):
            for instrumentation, binary in binaries.items():
                samples = []
                for repeat in range(args.index_repeats):
                    index = indices / f"performance-{mode}-{instrumentation}-{repeat}.zgi"
                    sample, diagnostic = measured([binary, "index", "--mode", mode, performance_root, index])
                    if instrumentation == "profile":
                        sample["profile"] = normalized.parse_profile(diagnostic, "index", mode)
                    samples.append(sample)
                header = index.read_bytes()[:12]
                if header[:8] != b"ZIGURUI\x00":
                    raise ValueError("unexpected index header")
                raw["indices"].append({"mode": mode, "instrumentation": instrumentation, "index_bytes": index.stat().st_size,
                    "index_sha256": benchmark.file_hash(index), "format_version": int.from_bytes(header[8:12], "little"),
                    "samples": samples, "summary": benchmark.summarize(samples)})
                index_paths[mode, instrumentation] = index
        identity = all(len({index["index_sha256"] for index in raw["indices"] if index["mode"] == mode}) == 1 for mode in ("literal", "normalized"))
        if not identity:
            raise ValueError("instrumentation changes raw v2 index")
        compatibility_cases = 0
        if baseline:
            for mode in ("literal", "normalized"):
                old_index = build(performance_root, "baseline-" + mode, baseline, mode)
                if benchmark.file_hash(old_index) != benchmark.file_hash(index_paths[mode, "plain"]):
                    raise ValueError("raw v2 index differs from previous AND binary")
                for terms, all_terms in ((["ガラス"], False), (["HeaderOnlyNeedle"], False), (["ガ", "希"], True)):
                    reference = None
                    for binary in (binaries["plain"], baseline):
                        for index in (old_index, index_paths[mode, "plain"]):
                            stdout, _ = correctness(search_command(binary, "search", mode, index, terms, frontmatter=False, all_terms=all_terms))
                            if reference is not None and stdout != reference:
                                raise ValueError("raw search/AND v2 cross-read differs")
                            reference = stdout
                    compatibility_cases += 1
        raw["quality"] = {"passed": True, "schema_passed": True, "schema": wanted_schema, "search_cases": search_cases,
            "invalid_document_cases": invalid_document_cases, "invalid_filter_cases": invalid_filter_cases, "resource_cases": resource_results,
            "raw_compatibility_cases": compatibility_cases, "index_identity": identity}
        schema_stdout, _ = correctness([binaries["plain"], "schema", index_paths["literal", "plain"]])
        if json.loads(schema_stdout) != expected_schema(performance_docs):
            raise ValueError("performance corpus schema differs")
        raw["schema_cli"] = {"warmups": [], "samples": []}
        for phase, repeats in (("warmups", args.warmups), ("samples", args.repeats)):
            for repeat in range(repeats):
                sample, _ = measured([binaries["plain"], "schema", index_paths["literal", "plain"]])
                sample["repeat"] = repeat
                raw["schema_cli"][phase].append(sample)
        for configuration in CONFIGURATIONS:
            mode = configuration["query_mode"]
            for case in PERFORMANCE_CASES:
                wanted, _ = expected_performance(performance_docs, case, configuration, "scan")
                reference = None
                for instrumentation, binary in binaries.items():
                    for method in ("search", "scan"):
                        target = index_paths[configuration["index_mode"], instrumentation] if method == "search" else performance_root
                        stdout, diagnostic = correctness(search_command(binary, method, mode, target, case["terms"], frontmatter=case["frontmatter"], filters=case["filters"], all_terms=case["all"]))
                        _, counts = expected_performance(performance_docs, case, configuration, method)
                        if normalized.parse_hits(stdout, performance_root) != wanted or (reference is not None and stdout != reference):
                            raise ValueError(f"performance corpus oracle differs: {configuration['id']}/{case['id']}")
                        reference = stdout
                        if instrumentation == "profile":
                            normalized.validate_counts(normalized.parse_profile(diagnostic, method, mode), counts)
                for method in ("search", "scan"):
                    _, counts = expected_performance(performance_docs, case, configuration, method)
                    raw["results"].append({"configuration_id": configuration["id"], "case_id": case["id"], "method": method,
                        "expected_counts": counts, "plain": {"warmups": [], "samples": []}, "profile": {"warmups": [], "samples": []}})
        records = {(record["configuration_id"], record["case_id"], record["method"]): record for record in raw["results"]}
        rng = random.Random(args.seed)
        for phase, repeats in (("warmups", args.warmups), ("samples", args.repeats)):
            for repeat in range(repeats):
                conditions = [(configuration, case) for configuration in CONFIGURATIONS for case in PERFORMANCE_CASES]
                rng.shuffle(conditions)
                for position, (configuration, case) in enumerate(conditions):
                    mode = configuration["query_mode"]
                    methods = ("search", "scan") if (repeat + position) % 2 == 0 else ("scan", "search")
                    for method in methods:
                        record = records[configuration["id"], case["id"], method]
                        instruments = ("plain", "profile") if (repeat + position) % 2 == 0 else ("profile", "plain")
                        for instrumentation in instruments:
                            target = index_paths[configuration["index_mode"], instrumentation] if method == "search" else performance_root
                            sample, diagnostic = measured(search_command(binaries[instrumentation], method, mode, target, case["terms"], frontmatter=case["frontmatter"], filters=case["filters"], all_terms=case["all"]))
                            if instrumentation == "profile":
                                sample["profile"] = normalized.parse_profile(diagnostic, method, mode)
                                normalized.validate_counts(sample["profile"], record["expected_counts"])
                            sample["repeat"] = repeat
                            record[instrumentation][phase].append(sample)
                save()
        raw["binary_unchanged"] = all(benchmark.file_hash(path) == raw["binary_sha256"][key] for key, path in binaries.items()) and (baseline is None or benchmark.file_hash(baseline) == raw["binary_sha256"]["baseline"])
        if not raw["binary_unchanged"] or any(benchmark.file_hash(performance_root / item["path"]) != item["sha256"] for item in manifest["documents"]):
            raise ValueError("binary or corpus changed during evaluation")
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
    parser.add_argument("--baseline", type=Path)
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
        parser.error("safe run-id, size>=6, positive repeats/timeout and nonnegative warmups required")
    return args


if __name__ == "__main__":
    run(arguments())
