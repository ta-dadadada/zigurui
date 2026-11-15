#!/usr/bin/env python3
"""事前に役割別の関連度を決めた合成ノートで、順位信号の除去比較をする。実KBを代表しない。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from relevance_oracle import metrics, signal_order

# tuningとfixedを分離。既定重みをこのデータへ最適化しない。
SCENARIOS = [
    ('tuning', 'materials', ['ガラス', '断熱'], '窓の改修案を探す'),
    ('fixed', 'journey', ['旅行', '準備'], '出発前の手順を探す'),
    ('fixed', 'retrieval', ['記録', '検索'], '記録を探す仕組みの要点を探す'),
]
# 見出し/指定fieldの主題に両語がある文書を強関連、本文の記述は弱関連、列挙は非関連。
GRADES = {'heading': 3, 'metadata': 3, 'ordered': 2, 'reverse': 1, 'distributed': 1, 'catalogue': 0, 'unretrievable': 2}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=Path('zig-out/bin/zigurui'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root, binary = args.output.resolve(), args.binary.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cfg = root / 'config' / 'zigurui' / 'config.json'
    cfg.parent.mkdir(parents=True)
    env = dict(os.environ, XDG_CONFIG_HOME=str(cfg.parent.parent), HOME=str(root / 'home'))
    rows = []
    for split, ident, terms, intent in SCENARIOS:
        notes = root / ident
        notes.mkdir()
        a, b = terms
        # 非関連の索引名を先にし、path順が常に良いfixtureにしない。
        docs = {'a-catalogue.md': ('catalogue', f'一覧: {a} 記号 {b}\n'),
                'z-heading.md': ('heading', f'# {a}{b}\n手順と要点のノート\n'),
                'y-metadata.md': ('metadata', f'---\nsubject: {a}{b}\n---\n{a}\n{b}\n主題のノート\n'),
                'x-ordered.md': ('ordered', f'{a}{b}\n詳しい説明\n'),
                'w-reverse.md': ('reverse', f'{b}{a}\n関連する別の話題\n'),
                'u-missing.md': ('unretrievable', '別表現で同じ主題を説明したノート\n')}
        if ident == 'journey':
            # 主題語が複数の見出し・配列要素へ分散する固定例。近接重視の弱点も測る。
            docs['z-heading.md'] = ('heading', f'# {a}\n## {b}\n出発前の詳しい手順\n')
            docs['y-metadata.md'] = ('metadata', f'---\nsubject: [{a}, {b}]\n---\n{a}\n別の段落\n{b}\n')
            docs['x-ordered.md'] = ('ordered', f'{b} 追記 {a} 過去の説明 {a}{b}\n')
        if ident == 'retrieval':
            # metadataだけに主題語がある候補と、近接の強い非関連一覧。
            docs['y-metadata.md'] = ('metadata', f'---\nsubject: {a}{b}\n---\n仕組みの要点を整理\n')
            docs['a-catalogue.md'] = ('catalogue', f'{a}{b}という項目名の一覧\n')
        for i in range(10):
            docs[f'b-distributed-{i:02}.md'] = ('distributed', f'{a}\n途中の説明\n{b}\n')
        grades = {n: GRADES[role] for n, (role, _) in docs.items()}
        for name, (_, text) in docs.items():
            (notes / name).write_text(text, encoding='utf-8')
        shared = {'rank': 'relevance', 'relevance': {'fields': [
            {'kind': 'body'}, {'kind': 'heading', 'weight': 2}, {'kind': 'file_name'},
            {'kind': 'metadata', 'pointer': '/subject', 'weight': 2}]}}
        cfg.write_text(json.dumps(shared), encoding='utf-8')
        raw = subprocess.run([str(binary), 'scan', '--all', '--explain', str(notes), *terms], env=env, capture_output=True, text=True, check=True)
        explained = json.loads(raw.stdout)['results']
        expected = {n for n, (_, t) in docs.items() if all(term in t for term in terms)}
        assert {Path(r['path']).name for r in explained} == expected
        assert signal_order(explained, 'combined') == [r['path'] for r in explained]
        orders = {}
        for variant in ['path', 'field_only', 'proximity_only', 'combined']:
            # 全条件でfield・重み・候補を固定し、説明済み寄与の指定成分だけを残す。
            paths = sorted((r['path'] for r in explained), key=lambda x: x.encode('utf-8')) if variant == 'path' else signal_order(explained, variant)
            order = [Path(path).name for path in paths]
            assert set(order) == expected
            orders[variant] = order
            rows.append({'split': split, 'scenario': ident, 'intent': intent, 'variant': variant, **metrics(order, grades)})
        (notes / 'judgments.json').write_text(json.dumps({'grades': grades, 'orders': orders}, ensure_ascii=False, indent=2) + '\n')
    report = {'fixture_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'oracle_sha256': hashlib.sha256(Path(__file__).with_name('relevance_oracle.py').read_bytes()).hexdigest(), 'schema_version': 1, 'synthetic_only': True, 'weights_tuned': False, 'quality_claim': '合成利用場面のみ。実KB/既存kb評価セットへの一般化なし', 'ablation': '同じfield/重み/候補の説明内訳からcoverage寄与・近接+順序寄与・全寄与を個別集計。製品CLIへ新しい採点設定は追加しない', 'mrr_scope': '全順位', 'recall_denominator': '非候補の正解を含む', 'results': rows}
    (root / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for row in rows:
        print(f"{row['split']:6} {row['scenario']:10} {row['variant']:14} nDCG@10={row['ndcg_at_10']:.4f} MRR={row['mrr']:.4f} Recall@10={row['recall_at_10']:.4f}")


if __name__ == '__main__':
    main()
