"""Witness-driven successor search for exact Top-K phase-change patterns."""
import time
import numpy as np
from numba import njit
from .core import validate, export_ids
from .bitsets import _packed_channels, _prefix_planes, _subtract_planes, _le_mask, _popcount, _complement_counts
from .ranking import _offer

@njit(cache=True)
def _corridor(p,q,d,k,minimum,limit):
    """Necessary support interval for a candidate with at least k tails."""
    lower=max(minimum,(q*(k+1)+p-1)//p-1)
    upper=limit if p==q else min(limit,q*d//(q-p)-1)
    return lower,upper


@njit(cache=True)
def _ensure(a,ci,b,ai,caps,planes,valid,channels,width,masks,support,counts):
    if support[a,ci,0]>=0:
        counts[8]+=1
        return
    diff=_subtract_planes(planes,b,a,ai)
    masks[a,ci]=_le_mask(diff,caps[ci],valid)
    for j in range(3):
        n=0
        for w in range(len(valid)):n+=np.int64(_popcount(masks[a,ci,w]&channels[b,j*width,w]))
        support[a,ci,j]=n
    counts[7]+=1
    counts[9]+=len(valid)*3


@njit(cache=True)
def _first_support(start,stop,ci,b,ai,caps,planes,valid,channels,width,masks,support,need,counts):
    """First start in [start,stop) with all support lower constraints satisfied."""
    while start<stop:
        mid=(start+stop)//2
        _ensure(mid,ci,b,ai,caps,planes,valid,channels,width,masks,support,counts)
        counts[10]+=1
        if support[mid,ci,0]>=need[0] and support[mid,ci,1]>=need[1] and support[mid,ci,2]>=need[2]:stop=mid
        else:start=mid+1
    return start


@njit(cache=True)
def _search(hc,xc,end,window,caps,lengths,minimum,channels,planes,valid,complements,k,ranks,mode):
    # Mode 2 enables witness transport and jumps; mode 3 is the no-jump control.
    e=hc.shape[2];width=e+1;words=len(valid)
    starts=max(0,end-2*window+1);grammar=starts*(starts+1)//2*np.sum(lengths)*e
    capacity=min(k,grammar)
    meta=np.empty((capacity,5),np.int64);ns=np.empty((capacity,3),np.int64);ks=np.empty((capacity,3),np.int64)
    nums=np.empty(capacity,np.int64);dens=np.empty(capacity,np.int64)
    # eligible, full, point-capacity, early-reference, rank-lower skips,
    # rank-upper skips, point-witness skips, masks built, cache hits,
    # support words, rank probes, tail words, legal-frontier probes, possible mask cells
    counts=np.zeros(14,np.int64);size=0
    state=np.zeros(3*width,np.int64);need=np.empty(3,np.int64);upper=np.empty(3,np.int64)
    transport=mode>=2;jumping=mode==1 or mode==2
    for b in range(window,end-window+1):
        aa=b-window+1
        for ai in range(e):
            cc=lengths[ai];c=caps[ai,:cc]
            masks=np.empty((aa,cc,words),np.uint64)
            support=np.full((aa,cc,3),-1,np.int64)
            first=np.empty(cc,np.int64)
            # Legal cells form an upper-right set. Trace its lower staircase
            # using at most aa+cc probes, charging all probes to the query.
            boundary=aa
            for ci in range(cc):
                while boundary>0:
                    a=boundary-1;counts[12]+=1
                    if xc[b,ai]-xc[a,ai]>c[ci]:break
                    _ensure(a,ci,b,ai,c,planes,valid,channels,width,masks,support,counts)
                    if min(support[a,ci])<minimum:break
                    boundary-=1
                first[ci]=boundary
                counts[0]+=(aa-boundary)*e
            counts[13]+=aa*cc
            for ci in range(cc):
                if first[ci]==aa:continue
                for bi in range(e):
                    # A chain-local witness never crosses a change of b,A,c,B.
                    witness=np.zeros(3,np.int64)
                    a=first[ci]
                    while a<aa:
                        if jumping and size==capacity:
                            impossible=False
                            for j in range(3):
                                w=witness[j] if transport else 0
                                need[j],upper[j]=_corridor(nums[size-1],dens[size-1],complements[b,j,bi],w,minimum,hc.shape[0])
                                if need[j]>upper[j]:impossible=True
                            if impossible:
                                counts[5]+=aa-a;break
                            # First probe current a: avoid a binary search if it already qualifies.
                            _ensure(a,ci,b,ai,c,planes,valid,channels,width,masks,support,counts)
                            if support[a,ci,0]<need[0] or support[a,ci,1]<need[1] or support[a,ci,2]<need[2]:
                                nxt=_first_support(a+1,aa,ci,b,ai,c,planes,valid,channels,width,masks,support,need,counts)
                                counts[4]+=nxt-a;a=nxt
                                if a==aa:break
                                _ensure(a,ci,b,ai,c,planes,valid,channels,width,masks,support,counts)
                            if support[a,ci,0]>upper[0] or support[a,ci,1]>upper[1] or support[a,ci,2]>upper[2]:
                                counts[5]+=aa-a;break
                        else:
                            _ensure(a,ci,b,ai,c,planes,valid,channels,width,masks,support,counts)
                        for j in range(3):state[j*width]=support[a,ci,j]
                        rejected=False
                        if size==capacity:
                            for j in range(3):
                                n=state[j*width];lower=max(0,n-complements[b,j,bi])+1
                                if lower*dens[size-1]>nums[size-1]*(n+1):counts[2]+=1;rejected=True;break
                            if not rejected and transport:
                                for j in range(3):
                                    if (witness[j]+1)*dens[size-1]>nums[size-1]*(state[j*width]+1):counts[6]+=1;rejected=True;break
                        if not rejected:
                            num,den=0,1
                            for j in range(3):
                                tail=0
                                for w in range(words):tail+=np.int64(_popcount(masks[a,ci,w]&channels[b,j*width+bi+1,w]))
                                counts[11]+=words;state[j*width+bi+1]=tail
                                if transport:witness[j]=tail
                                p,q=tail+1,state[j*width]+1
                                if p*den>num*q:num,den=p,q
                                if j<2 and size==capacity and num*dens[size-1]>nums[size-1]*den:
                                    counts[3]+=1;rejected=True;break
                            if not rejected:
                                counts[1]+=1
                                size,_=_offer(meta,ns,ks,nums,dens,size,a,b,ai,bi,c[ci],state,width,num,den,ranks)
                        a+=1
    return meta[:size].copy(),ns[:size].copy(),ks[:size].copy(),grammar,counts


def _solve(h,x,qi,end,c,mode):
    started=time.perf_counter();window=validate(h,x,qi,end,c)
    if len(h.rows)>=2**20:raise ValueError('Exact integer comparisons require N < 2^20')
    lengths=np.asarray([len(c['thresholds'][n]) for n in h.events],np.int64)
    caps=np.zeros((len(h.events),max(lengths)),np.int64)
    for ai,n in enumerate(h.events):caps[ai,:lengths[ai]]=c['thresholds'][n]
    channels=_packed_channels(h.counts,h.score,h.opponent_shots,x.counts[qi],x.score[qi],x.opponent_shots[qi],end,window,c['opponent_shots_cutoff'])
    maximum=int(h.counts[:,:end+1,:].max()) if len(h.rows) else 0
    planes,valid=_prefix_planes(h.counts,end,max(1,maximum.bit_length()))
    complements=_complement_counts(channels,end,window,len(h.events))
    ranks=np.asarray([sorted(h.events).index(e) for e in h.events],np.int64)
    prepared=time.perf_counter()
    meta,ns,ks,g,counts=_search(h.counts,x.counts[qi],end,window,caps,lengths,c['minimum_reference_size'],channels,planes,valid,complements,c['K'],ranks,mode)
    result=export_ids(np.arange(len(meta)),meta,ns,ks,h,end,c)
    keys=['eligible_candidates','fully_scored_candidates','complement_pruned_candidates','reference_pruned_candidates',
          'rank_lower_pruned_candidates','rank_upper_pruned_candidates','witness_pruned_candidates','condition_masks_built',
          'support_cache_hits','support_popcount_words','rank_search_probes','tail_popcount_words','legal_frontier_probes','possible_condition_masks']
    stats={key:int(v) for key,v in zip(keys,counts)}
    stats.update(grammar_candidates=int(g),certified_exact=True,materialized_candidates=len(meta),mechanism=mode,
        index_bytes=channels.nbytes+planes.nbytes+valid.nbytes+complements.nbytes,
        preparation_seconds=prepared-started,total_seconds=time.perf_counter()-started)
    return dict(top_k=result,stats=stats)


def solve(h, x, qi, end, config):
    return _solve(h, x, qi, end, config, 2)
