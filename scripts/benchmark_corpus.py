"""合成文書と利用意図fixtureを生成し、独立した文字列検索でCLIを検証する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import re
import subprocess

DEFAULT_SEED = 20261114

_HANDCRAFTED = (
    ("ja", "# 文書検索の設計\n技術メモを検索する。転置インデックスで文書を見つける。\n"),
    ("ja", "# 猫の通院記録\n猫の通院記録を家族と確認する。\n東京と京駅と駅前は別々の記録に現れる。\n"),
    ("en", "# search engine design\nAn inverted index helps search through technical notes.\nab xx bc yy cd zz de\n"),
    ("en", "# lunar backup protocol\nWeekly restore rehearsal stores two archive copies.\n"),
    ("mixed", "# Zig allocator設計\nZigで検索する。英語のsearchと日本語の検索を扱う。\nメモリの所有権を明記する。\n"),
    ("mixed", "# 作業メモ\n英語searchと日本語のメモを残す。\n"),
)


def _path(doc_id: int, language: str) -> str:
    return f"{doc_id:06d}-{language}.md"


def queries() -> list[dict]:
    """固定queryと、手作り文書に対する期待結果を返す。順位の評価は行わない。"""
    ja_search, ja_cat, en_search, en_backup, mixed_design, mixed_note = (
        _path(i, lang) for i, (lang, _) in enumerate(_HANDCRAFTED)
    )
    definitions = (
        ("ja-one", "検", "one-codepoint", "ja", "短い日本語でも検索設計を見つける", [ja_search, mixed_design]),
        ("en-one", "s", "one-codepoint", "en", "短い英字を含む文書を見つける", [en_search, en_backup, mixed_design, mixed_note]),
        ("ja-two", "検索", "two-codepoint", "ja", "検索についての設計を見つける", [ja_search, mixed_design]),
        ("en-two", "se", "two-codepoint", "en", "英語searchの一部から文書を見つける", [en_search, mixed_design, mixed_note]),
        ("ja-long", "転置インデックス", "long", "ja", "索引の設計メモを見つける", [ja_search]),
        ("en-long", "inverted index", "long", "en", "英語の索引設計を見つける", [en_search]),
        ("mixed-long", "Zigで検索", "long", "mixed", "Zigの実装方針を見つける", [mixed_design]),
        ("ja-frequent", "メモ", "frequent", "ja", "メモを広く拾う", [ja_search, mixed_design, mixed_note]),
        ("ja-rare", "猫の通院記録", "rare", "ja", "猫の受診記録を探す", [ja_cat]),
        ("en-rare", "lunar backup protocol", "rare", "en", "特定のバックアップ手順を探す", [en_backup]),
        ("mixed-rare", "Zig allocator設計", "rare", "mixed", "allocatorの所有権設計を探す", [mixed_design]),
        ("missing", "ZZZ存在しない98765", "no-hit", "mixed", "存在しない語で結果を返さない", []),
        ("ja-false-candidate", "東京駅前", "false-candidate", "ja", "全bigramが離れて存在しても連続しなければ除外する", []),
        ("en-false-candidate", "abcde", "false-candidate", "en", "離れた英語bigramだけの文書を除外する", []),
    )
    result = [
        {
            "id": query_id,
            "text": text,
            "category": category,
            "language": language,
            "intent": intent,
            "expected_documents": expected,
            "expected_scope": "handcrafted-documents-only",
        }
        for query_id, text, category, language, intent, expected in definitions
    ]
    for query in result:
        if query["category"] == "false-candidate":
            query["candidate_document"] = ja_cat if query["language"] == "ja" else en_search
    return result


def _generated_text(seed: int, doc_id: int, language: str) -> str:
    # 文書ごとに乱数列を分離し、文書数を変えても共通prefixの内容を保つ。
    rng = random.Random(f"zigurui-corpus-v1:{seed}:{doc_id}")
    sentences = {
        "ja": (
            "技術メモの検索では本文と索引を分けて管理する。",
            "転置インデックスを使って候補文書を見つける。",
            "所有権を明確にして確保したメモリを解放する。",
            "変更した設計を記録し、同じ条件で結果を比較する。",
            "日本語の検索語をコードポイント単位で扱う。",
        ),
        "en": (
            "Search notes describe memory ownership and release.",
            "An inverted index narrows search candidates before validation.",
            "The search engine preserves original documents as read-only input.",
            "Repeat the experiment with the same data and configuration.",
            "Document identifiers remain deterministic within this corpus.",
        ),
        "mixed": (
            "Zigで検索する技術メモにUTF-8の境界を記録する。",
            "The search engine compares 原文と候補の連続一致。",
            "メモリのownershipを決めてallocatorで解放する。",
            "PythonのoracleとZigの検索結果を比較する。",
            "文書snapshotは元のMarkdownを変更せず保存する。",
        ),
    }[language]
    pieces = [f"# generated {doc_id:06d} {language}\n"]
    byte_count = len(pieces[0].encode("utf-8"))
    while byte_count < 1024:
        line = f"{rng.choice(sentences)} [{rng.randrange(100000):05d}]\n"
        pieces.append(line)
        byte_count += len(line.encode("utf-8"))
    return "".join(pieces)


def prepare(root: Path, size: int, seed: int = DEFAULT_SEED) -> dict:
    """新規rootだけに合成文書とmanifestを作成する。既存rootは変更せず拒否する。"""
    if size < len(_HANDCRAFTED):
        raise ValueError(f"size must be at least {len(_HANDCRAFTED)}")
    root = Path(root)
    root.mkdir(exist_ok=False)
    documents_root = root / "documents"
    documents_root.mkdir()
    manifest_documents = []
    counts = {"ja": 0, "en": 0, "mixed": 0}
    corpus_hash = hashlib.sha256()
    for doc_id in range(size):
        handcrafted = doc_id < len(_HANDCRAFTED)
        if handcrafted:
            language, text = _HANDCRAFTED[doc_id]
        else:
            language = ("ja", "en", "mixed")[(doc_id - len(_HANDCRAFTED)) % 3]
            text = _generated_text(seed, doc_id, language)
        relative_path = _path(doc_id, language)
        data = text.encode("utf-8")
        (documents_root / relative_path).write_bytes(data)
        corpus_hash.update(relative_path.encode("utf-8") + b"\x00" + data + b"\x00")
        manifest_documents.append({
            "path": relative_path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "language": language,
            "handcrafted": handcrafted,
        })
        counts[language] += 1
    manifest = {
        "schema_version": 1,
        "generator": "zigurui-synthetic-v1",
        "seed": seed,
        "document_count": size,
        "handcrafted_count": len(_HANDCRAFTED),
        "total_bytes": sum(doc["bytes"] for doc in manifest_documents),
        "corpus_sha256": corpus_hash.hexdigest(),
        "hash_definition": "SHA256(relative UTF-8 path + NUL + document bytes + NUL), path order",
        "language_counts": counts,
        "documents": manifest_documents,
        "queries": queries(),
        "quality_scope": "手作り文書の利用意図に対応する連続部分一致。ランキングや同義語の評価は行わない。",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _parse_hits(stdout: bytes, documents_root: Path) -> list[tuple[str, int]]:
    hits = []
    for line in stdout.decode("utf-8").splitlines():
        match = re.match(r"^(.*?):([0-9]+): ", line)
        if match is None:
            raise ValueError(f"invalid result line: {line!r}")
        path = str(Path(match.group(1)).relative_to(documents_root))
        hits.append((path, int(match.group(2))))
    return hits


def verify(binary: Path, root: Path, index: Path, manifest: dict, *, env=None) -> dict:
    """正しさと利用意図を分けて検証し、失敗詳細を含むJSON化可能な結果を返す。"""
    binary, index = Path(binary).resolve(), Path(index).resolve()
    documents_root = (Path(root) / "documents").resolve()
    texts = {}
    integrity_errors = []
    for doc in manifest["documents"]:
        data = (documents_root / doc["path"]).read_bytes()
        if len(data) != doc["bytes"] or hashlib.sha256(data).hexdigest() != doc["sha256"]:
            integrity_errors.append(doc["path"])
        texts[doc["path"]] = data.decode("utf-8")
    handmade = {doc["path"] for doc in manifest["documents"] if doc["handcrafted"]}
    results = []
    for query in manifest["queries"]:
        expected = []
        for path, text in sorted(texts.items()):
            offset = text.find(query["text"])
            if offset >= 0:
                expected.append((path, text[:offset].count("\n") + 1))
        result = {"id": query["id"], "text": query["text"], "category": query["category"], "expected_hit_count": len(expected)}
        try:
            indexed = subprocess.run([str(binary), "search", str(index), query["text"]], capture_output=True, timeout=120, check=True, env=env).stdout
            scanned = subprocess.run([str(binary), "scan", str(documents_root), query["text"]], capture_output=True, timeout=120, check=True, env=env).stdout
            actual = _parse_hits(indexed, documents_root)
            scan_actual = _parse_hits(scanned, documents_root)
            actual_handmade = sorted(path for path, _ in actual if path in handmade)
            result.update({
                "stdout_equal": indexed == scanned,
                "oracle_equal": actual == expected and scan_actual == expected,
                "actual_hit_count": len(actual),
                "intent_passed": actual_handmade == sorted(query["expected_documents"]),
                "expected_documents": query["expected_documents"],
                "actual_handcrafted_documents": actual_handmade,
            })
            if actual != expected:
                result["oracle_difference"] = {"expected": expected, "actual": actual}
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            result.update({"stdout_equal": False, "oracle_equal": False, "intent_passed": False, "error": str(error)})
        result["passed"] = all(result[key] for key in ("stdout_equal", "oracle_equal", "intent_passed"))
        results.append(result)
    return {
        "passed": not integrity_errors and all(result["passed"] for result in results),
        "corpus_integrity_passed": not integrity_errors,
        "corpus_integrity_errors": integrity_errors,
        "query_results": results,
        "quality_scope": manifest["quality_scope"],
    }
