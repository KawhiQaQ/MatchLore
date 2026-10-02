"""Small exhaustive oracle independent of bitmap/search helper implementations."""
from fractions import Fraction
import unittest
import numpy as np
from highlights._solver.core import Cube
from highlights._solver.witness import solve


def exhaustive(h,x,end,c):
    found=[];minimum=c['minimum_reference_size'];window=c['minimum_window_minutes']
    for a in range(end):
        for b in range(a+window,end-window+1):
            for ai,event in enumerate(h.events):
                for bi,post in enumerate(h.events):
                    value=int(x.counts[0,end,bi]-x.counts[0,b,bi])
                    for cap in c['thresholds'][event]:
                        if x.counts[0,b,ai]-x.counts[0,a,ai]>cap:continue
                        counts=[[0,0] for _ in range(3)]
                        for row in range(len(h.rows)):
                            if h.counts[row,b,ai]-h.counts[row,a,ai]>cap:continue
                            included=(True,h.score[row,b]==x.score[0,b],
                                (h.opponent_shots[row,b]<=c['opponent_shots_cutoff'])==
                                (x.opponent_shots[0,b]<=c['opponent_shots_cutoff']))
                            tail=h.counts[row,end,bi]-h.counts[row,b,bi]>=value
                            for j,ok in enumerate(included):
                                if ok:counts[j][0]+=1;counts[j][1]+=int(tail)
                        if min(n for n,k in counts)<minimum:continue
                        score=max(Fraction(k+1,n+1) for n,k in counts)
                        found.append((score,(a,b,end,event,post,cap),counts))
    found.sort()
    return [(list(p),[q.numerator,q.denominator],[{'n':n,'tail_count':k} for n,k in counts]) for q,p,counts in found[:c['K']]]


class ExactSearch(unittest.TestCase):
    def test_random_patterns_ties_multiple_events_and_partial_words(self):
        for seed,n in enumerate([1,17,63,64,65,129]):
            rng=np.random.default_rng(seed)
            def cube(rows):
                counts=np.concatenate([np.zeros((rows,1,2),np.int32),rng.integers(0,3,(rows,6,2),dtype=np.int32).cumsum(axis=1)],axis=1)
                return Cube(counts.astype(np.int32),rng.integers(-1,2,(rows,7),dtype=np.int32),
                    rng.integers(0,5,(rows,7),dtype=np.int32),list(range(rows)),['Z','A'],60000)
            h,x=cube(n),cube(1)
            for k in (1,5,999):
                c=dict(events=h.events,window_grid_minutes=1,minimum_window_minutes=1,
                    minimum_reference_size=1,K=k,thresholds={'Z':[0,1,3],'A':[0,2,4]},opponent_shots_cutoff=2)
                with self.subTest(seed=seed,rows=n,K=k):
                    actual=solve(h,x,0,6,c)['top_k']
                    self.assertEqual([(v['pattern'],v['tail_fraction'],v['reference_counts']) for v in actual],exhaustive(h,x,6,c))

    def test_empty_eligible_reference(self):
        h=Cube(np.zeros((2,5,1),np.int32),np.zeros((2,5),np.int32),np.zeros((2,5),np.int32),[0,1],['A'],60000)
        x=h.take([0]);c=dict(events=['A'],window_grid_minutes=1,minimum_window_minutes=1,
            minimum_reference_size=3,K=5,thresholds={'A':[0]},opponent_shots_cutoff=1)
        self.assertEqual(solve(h,x,0,4,c)['top_k'],[])
