"""Prefix-count input and deterministic rational-score ordering."""
from dataclasses import dataclass
from bisect import insort
from fractions import Fraction
import math
import numpy as np

@dataclass
class Cube:
    counts: np.ndarray  # observation, grid boundary, event type; strictly before boundary
    score: np.ndarray  # goal difference sign at boundary
    opponent_shots: np.ndarray
    rows: list
    events: list
    step_ms: int

    def take(self, indices):
        return Cube(self.counts[indices], self.score[indices], self.opponent_shots[indices],
                    [self.rows[i] for i in indices], self.events, self.step_ms)

class TopK:
    def __init__(self, k):
        if k < 1:
            raise ValueError('K must be positive')
        self.k, self.items = k, []

    def rejects_bound(self, pair):
        if len(self.items) < self.k:
            return False
        f = self.items[-1][0]
        return pair[0] * f.denominator > f.numerator * pair[1]

    def offer(self, pair, key, references):
        if self.rejects_bound(pair):
            return
        entry = (Fraction(*pair), tuple(key), tuple(references))
        insort(self.items, entry)
        if len(self.items) > self.k:
            self.items.pop()

    def export(self):
        return [{'pattern': list(key), 'tail_fraction': [v.numerator, v.denominator],
                 'robust_tail': float(v), 'quality': -math.log(float(v)),
                 'reference_counts': [{'n': n, 'tail_count': k} for n, k in refs]}
                for v, key, refs in self.items]


def validate(h, x, qi, end, config):
    assert h.events == x.events == config['events']
    assert h.step_ms == x.step_ms == config['window_grid_minutes'] * 60000
    assert 0 <= qi < len(x.rows) and 1 < end < h.counts.shape[1] and end < x.counts.shape[1]
    assert config['minimum_reference_size'] >= 1 and config['K'] >= 1
    assert config['minimum_window_minutes'] % config['window_grid_minutes'] == 0
    assert config['minimum_window_minutes'] >= config['window_grid_minutes']
    for caps in config['thresholds'].values():
        assert caps == sorted(set(caps)) and caps and caps[0] >= 0, 'Thresholds must be unique and increasing'
    return config['minimum_window_minutes'] // config['window_grid_minutes']


def key_for(h, a, b, end, ai, bi, c):
    unit = h.step_ms // 60000
    return (a * unit, b * unit, end * unit, h.events[ai], h.events[bi], int(c))

def export_ids(ids, meta, ns, ks, h, end, config):
    top = TopK(config['K'])
    for i in ids:
        a, b, ai, bi, cap = map(int, meta[i])
        refs = [(int(n), int(t)) for n, t in zip(ns[i], ks[i])]
        worst = max(Fraction(t+1, n+1) for n, t in refs)
        top.offer((worst.numerator, worst.denominator),
                  key_for(h, a, b, end, ai, bi, cap), refs)
    return top.export()
