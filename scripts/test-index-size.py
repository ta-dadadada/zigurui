"""独立に組み立てたv2データで容量集計と不正入力の拒否を確認する。"""
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('index_size', Path(__file__).with_name('evaluate-index-size.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    u64 = lambda n: struct.pack('<Q', n)
    string = lambda data: u64(len(data)) + data
    payload = u64(2) + u64(1) + struct.pack('<III', 0, 0, 0)
    payload += string(b'/a') + string(b'ab') + string(b'/b') + string(b'ab')
    payload += string(b'ab') + u64(2) + u64(0) + u64(1)
    checksum = 14695981039346656037
    for byte in payload:
        checksum = ((checksum ^ byte) * 1099511628211) % (1 << 64)
    return b'ZIGURUI\0' + struct.pack('<IQQ', 2, len(payload), checksum) + payload


class IndexSizeTests(unittest.TestCase):
    def test_known_wire_size(self):
        data = fixture()
        self.assertEqual(len(data), 130)
        report = module.inspect(data)
        self.assertEqual(report['sections_bytes'], dict(header=56, document_lengths=32,
                         paths=4, original_text=4, source_root_length=0, source_root=0, posting_lengths=16, grams=2, document_ids=16))
        self.assertEqual(report['candidates']['u32']['modeled_index_bytes'], 122)
        self.assertEqual(report['candidates']['delta_varint']['modeled_index_bytes'], 116)

    def test_v3_actual_size_and_private_root_not_exported(self):
        import json
        data = fixture()
        root = b'/private/synthetic-root'
        payload = data[28:56] + struct.pack('<Q', len(root)) + root + data[56:-16] + b'\x00\x01'
        checksum = 14695981039346656037
        for byte in payload:
            checksum = ((checksum ^ byte) * 1099511628211) % (1 << 64)
        current = b'ZIGURUI\0' + struct.pack('<IQQ', 3, len(payload), checksum) + payload
        report = module.inspect(current)
        self.assertEqual(report['sections_bytes']['source_root'], len(root))
        self.assertEqual(report['candidates']['delta_varint']['modeled_index_bytes'], len(current))
        self.assertNotIn(root.decode(), json.dumps(report))

    def test_varint_boundaries(self):
        self.assertEqual(module.varint(0), b'\x00')
        self.assertEqual(module.varint(127), b'\x7f')
        self.assertEqual(module.varint(128), b'\x80\x01')
        self.assertEqual(module.varint(16384), b'\x80\x80\x01')

    def test_truncation_corruption_and_unsupported_version(self):
        data = fixture()
        for end in range(len(data)):
            with self.assertRaises((ValueError, struct.error)):
                module.inspect(data[:end])
        for position in (0, 8, 12, 20, len(data) - 1):
            corrupted = bytearray(data)
            corrupted[position] ^= 1
            with self.assertRaises(ValueError):
                module.inspect(bytes(corrupted))


if __name__ == '__main__':
    unittest.main()
