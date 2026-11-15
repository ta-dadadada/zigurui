#!/usr/bin/env python3
"""独立oracle自身の手計算例を検証する。"""
import unittest
from relevance_oracle import field, metrics, windows, signal_order


class OracleTest(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(windows('a_____b_ab', ['a', 'b']), ((2, 8, 10), (2, 8, 10)))
        self.assertEqual(windows('abcd', ['abcd', 'ab']), ((4, 0, 4), (4, 0, 4)))
        self.assertEqual(windows('ba', ['a', 'b']), ((2, 0, 2), None))
        self.assertEqual(windows('ababa', ['aba', 'bab']), ((4, 0, 4), (4, 0, 4)))

    def test_boundaries_and_unicode(self):
        f = field(['ガラス', '断熱'], ['ガラス', '断熱'], 'literal')
        self.assertEqual(f['coverage'], 0.5)
        self.assertIsNone(f['window'])
        f = field(['ｶﾞﾗｽ断熱'], ['ガラス', '断熱'], 'normalized')
        self.assertEqual(f['window'], dict(value=0, start=0, end=5))
        self.assertEqual(f['proximity'], 1)
        f = field(['a\nb'], ['a', 'b'], 'literal', lines=True)
        self.assertEqual(f['coverage'], 1)
        self.assertIsNone(f['window'])

    def test_ablation_removes_coverage_and_uses_all_fields(self):
        # coverage順とpath順を逆にする。近接0同士ならcoverageにかかわらずpath順。
        results = [dict(path='a', fields=[dict(coverage_score=0, proximity_score=0, ordered_score=0)]),
                   dict(path='b', fields=[dict(coverage_score=1, proximity_score=0, ordered_score=0)])]
        self.assertEqual(signal_order(results, 'field_only'), ['b', 'a'])
        self.assertEqual(signal_order(results, 'proximity_only'), ['a', 'b'])
        self.assertEqual(signal_order(results, 'combined'), ['b', 'a'])
        # 後続fieldの近接も同じ条件で残し、field自体を変えない。
        results[0]['fields'].append(dict(coverage_score=0, proximity_score=2, ordered_score=1))
        self.assertEqual(signal_order(results, 'proximity_only'), ['a', 'b'])
        self.assertEqual(signal_order(results, 'combined'), ['a', 'b'])

    def test_metrics_are_distinct(self):
        m = metrics(['no', 'weak', 'strong'], {'weak': 1, 'strong': 3, 'absent': 2}, k=2)
        self.assertEqual(m['mrr'], 0.5)
        self.assertEqual(m['recall_at_10'], 1 / 3)
        self.assertAlmostEqual(m['ndcg_at_10'], (1 / 1.584962500721156) / (7 + 3 / 1.584962500721156))
        self.assertEqual(metrics(['x'], {})['mrr'], 0)


if __name__ == '__main__':
    unittest.main()
