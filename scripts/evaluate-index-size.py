#!/usr/bin/env python3
"""v2/v3索引の容量内訳と、ID符号化を変えた場合の正確なバイト数を算出する。

候補は保存形式の採用や実装ではなく、同じID列を符号化した容量モデル。
入力の内容や絶対パスはレポートへ出力しない。入力索引は変更しない。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
from pathlib import Path


def varint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def inspect(data: bytes) -> dict:
    offset = 0

    def take(count: int) -> bytes:
        nonlocal offset
        if count < 0 or count > len(data) - offset:
            raise ValueError('truncated index')
        result = data[offset:offset + count]
        offset += count
        return result

    def integer() -> int:
        return struct.unpack('<Q', take(8))[0]

    def string() -> bytes:
        return take(integer())

    if take(8) != b'ZIGURUI\0':
        raise ValueError('invalid magic')
    version = struct.unpack('<I', take(4))[0]
    if version not in (2, 3):
        raise ValueError('only v2/v3 indexes are supported')
    size, checksum = integer(), integer()
    if size != len(data) - 28:
        raise ValueError('invalid payload size')
    actual = 14695981039346656037
    for byte in data[28:]:
        actual = ((actual ^ byte) * 1099511628211) & ((1 << 64) - 1)
    if checksum != actual:
        raise ValueError('invalid checksum')
    documents, grams = integer(), integer()
    mode, semantic, unicode = struct.unpack('<III', take(12))
    if mode not in (0, 1):
        raise ValueError('invalid mode')
    root = string() if version == 3 else b''
    sections = dict(header=56, source_root_length=8 if version == 3 else 0,
                    source_root=len(root), document_lengths=16 * documents,
                    paths=0, original_text=0, posting_lengths=16 * grams,
                    grams=0, document_ids=0)
    for _ in range(documents):
        path, text = string(), string()
        path.decode('utf-8'); text.decode('utf-8')
        sections['paths'] += len(path)
        sections['original_text'] += len(text)
    def read_varint():
        value = 0
        for i in range(10):
            byte = take(1)[0]
            if i == 9 and byte > 1:
                raise ValueError('varint overflow')
            value |= (byte & 127) << (7 * i)
            if not byte & 128:
                if i and byte == 0:
                    raise ValueError('overlong varint')
                return value
        raise ValueError('varint overflow')

    count = delta_bytes = direct_bytes = stored_id_bytes = 0
    previous_gram = None
    for _ in range(grams):
        gram = string()
        if len(gram.decode('utf-8')) != 2 or (previous_gram is not None and gram <= previous_gram):
            raise ValueError('invalid gram order')
        previous_gram = gram
        sections['grams'] += len(gram)
        n = integer()
        if not 0 < n <= documents or n > (len(data) - offset) // (8 if version == 2 else 1):
            raise ValueError('invalid posting length')
        previous = 0
        start = offset
        for i in range(n):
            if version == 3:
                delta = read_varint()
                if i and delta == 0:
                    raise ValueError('zero delta')
                value = previous + delta
            else:
                value = integer()
            if value >= documents or (i and value <= previous):
                raise ValueError('invalid document ID')
            direct_bytes += len(varint(value))
            delta_bytes += len(varint(value - previous))
            previous = value
        stored_id_bytes += offset - start
        count += n
    sections['document_ids'] = stored_id_bytes
    if offset != len(data) or sum(sections.values()) != len(data):
        raise ValueError('invalid trailing data or section accounting')
    base = len(data) - sections['document_ids']
    candidates = {'u64': 8 * count, 'varint': direct_bytes,
                  'delta_varint': delta_bytes}
    if documents <= (1 << 32):
        candidates['u32'] = count * 4
    return dict(format_version=version, sha256=hashlib.sha256(data).hexdigest(),
                document_count=documents, gram_count=grams, document_id_count=count,
                mode=('literal', 'normalized')[mode], sections_bytes=sections,
                index_bytes=len(data),
                candidates={name: dict(id_bytes=size, modeled_index_bytes=base + size,
                                       reduction_fraction=1 - (base + size) / len(data))
                            for name, size in candidates.items()},
                limitations=['ID部分以外は入力と同一と仮定。root等を追加する別形式のヘッダーは含めない。',
                             '容量のみ。復号後RAM、速度、実文書での効果は未評価。'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('indexes', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = [inspect(path.read_bytes()) for path in args.indexes]
    # 過去の評価結果を上書きしない。
    with args.output.open('x') as output:
        json.dump(dict(schema_version=1, reports=reports), output, ensure_ascii=False, indent=2)
        output.write('\n')


if __name__ == '__main__':
    main()
