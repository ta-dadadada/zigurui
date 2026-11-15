"""短い合成入力専用の独立oracle。区間は全出現の直積から列挙し、効率化しない。"""
import itertools
import math
import unicodedata


def transform(text, mode):
    if mode == 'literal':
        return text
    return unicodedata.normalize('NFKC', unicodedata.normalize('NFKC', text).casefold())


def prepare(terms, mode):
    return list(dict.fromkeys(transform(t, mode) for t in terms))


def occurrences(text, term):
    return [(i, i + len(term)) for i in range(len(text)) if text.startswith(term, i)]


def windows(text, terms):
    best = ordered = None
    if len(terms) < 2:
        return best, ordered
    for choice in itertools.product(*(occurrences(text, t) for t in terms)):
        start, end = min(x[0] for x in choice), max(x[1] for x in choice)
        key = (end - start, start, end)
        best = min(best, key) if best is not None else key
        if all(a[0] <= b[0] for a, b in zip(choice, choice[1:])):
            ordered = min(ordered, key) if ordered is not None else key
    return best, ordered


def field(values, terms, mode, lines=False, proximity_weight=1, ordered_weight=1):
    terms = prepare(terms, mode)
    covered = 0
    coverage_value = None
    matched = []
    best = ordered = None
    for v, raw in enumerate(values):
        text = transform(raw, mode)
        count = sum(t in text for t in terms)
        if count > covered:
            covered, coverage_value = count, v
            matched = [i for i, term in enumerate(terms) if term in text]
        base = 0
        for section in text.split('\n') if lines else [text]:
            w, o = windows(section, terms)
            for window, target in [(w, 'best'), (o, 'ordered')]:
                if window is not None:
                    key = (window[0], v, base + window[1], base + window[2])
                    if target == 'best':
                        best = min(best, key) if best is not None else key
                    else:
                        ordered = min(ordered, key) if ordered is not None else key
            base += len(section) + 1
    if proximity_weight == 0:
        best = None
    if ordered_weight == 0:
        ordered = None
    length = sum(map(len, terms))
    signal = lambda w: 0 if w is None else 1 / (1 + max(0, w[0] - length))
    view = lambda w: None if w is None else dict(value=w[1], start=w[2], end=w[3])
    return dict(matched_terms=covered, matched_term_indices=matched, coverage_value=coverage_value, coverage=covered / len(terms),
                window=view(best), ordered_window=view(ordered), proximity=signal(best), ordered=signal(ordered))


def metrics(order, grades, k=10):
    """MRRは全順位、nDCG/Recallは先頭k。非候補の正解も分母に残す。"""
    gain = lambda g: 2 ** g - 1
    dcg = lambda values: sum(gain(g) / math.log2(i + 2) for i, g in enumerate(values))
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    relevant = {name for name, grade in grades.items() if grade > 0}
    return {'ndcg_at_10': dcg([grades.get(n, 0) for n in order[:k]]) / ideal if ideal else 0,
            'mrr': next((1 / (i + 1) for i, n in enumerate(order) if n in relevant), 0),
            'recall_at_10': len(set(order[:k]) & relevant) / len(relevant) if relevant else 0}


def signal_order(results, variant):
    """同じfield/重み/候補の説明寄与から、指定信号だけを残した正確な除去比較を作る。"""
    keys = {'field_only': ('coverage_score',),
            'proximity_only': ('proximity_score', 'ordered_score'),
            'combined': ('coverage_score', 'proximity_score', 'ordered_score')}[variant]
    score = lambda r: sum(sum(f[k] for k in keys) for f in r['fields'])
    return [r['path'] for r in sorted(results, key=lambda r: (-score(r), r['path'].encode('utf-8')))]
