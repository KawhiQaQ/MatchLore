"""Packed reference channels and bit-sliced prefix counts."""
import numpy as np
from numba import njit, types
from numba.extending import intrinsic

@intrinsic
def _popcount(typingctx, value):
    if value != types.uint64:
        raise TypeError('popcount requires uint64')
    def codegen(context,builder,signature,args):
        return builder.ctpop(args[0])
    return types.uint64(types.uint64),codegen


@njit(cache=True)
def _packed_channels(hc,hs,ho,xc,xs,xo,end,window,cutoff):
    n,_,e=hc.shape
    width=e+1;words=(n+63)//64
    channels=np.zeros((end,3*width,words),np.uint64)
    for b in range(window,end-window+1):
        for row in range(n):
            word=row//64;bit=np.uint64(1)<<np.uint64(row%64)
            refs=(True,hs[row,b]==xs[b],(ho[row,b]<=cutoff)==(xo[b]<=cutoff))
            for j in range(3):
                if refs[j]:
                    channels[b,j*width,word]|=bit
                    for bi in range(e):
                        if hc[row,end,bi]-hc[row,b,bi]>=xc[end,bi]-xc[b,bi]:
                            channels[b,j*width+bi+1,word]|=bit
    return channels


@njit(cache=True)
def _prefix_planes(hc,end,bits):
    n,_,e=hc.shape;words=(n+63)//64
    planes=np.zeros((end+1,e,bits,words),np.uint64)
    valid=np.zeros(words,np.uint64)
    for row in range(n):
        bit=np.uint64(1)<<np.uint64(row%64);w=row//64;valid[w]|=bit
        for t in range(end+1):
            for ai in range(e):
                v=hc[row,t,ai]
                for k in range(bits):
                    if (v>>k)&1:planes[t,ai,k,w]|=bit
    return planes,valid


@njit(cache=True)
def _subtract_planes(planes,b,a,ai):
    bits,words=planes.shape[2:]
    result=np.empty((bits,words),np.uint64)
    for w in range(words):
        borrow=np.uint64(0)
        for k in range(bits):
            u=planes[b,ai,k,w];v=planes[a,ai,k,w]
            result[k,w]=u^v^borrow
            borrow=((~u)&(v|borrow))|(v&borrow)
    return result


@njit(cache=True)
def _le_mask(planes,cap,valid):
    bits,words=planes.shape
    result=np.empty(words,np.uint64)
    for w in range(words):
        if (np.uint64(cap)>>np.uint64(bits))!=0:
            result[w]=valid[w];continue
        less=np.uint64(0);equal=valid[w]
        for k in range(bits-1,-1,-1):
            if (cap>>k)&1:
                less|=equal&~planes[k,w]
                equal&=planes[k,w]
            else:equal&=~planes[k,w]
        result[w]=less|equal
    return result

@njit(cache=True)
def _complement_counts(channels, end, window, e):
    """Count R_j minus target-tail set once per b, j, target."""
    out = np.zeros((end, 3, e), np.int64)
    width = e + 1
    for b in range(window, end-window+1):
        for j in range(3):
            for bi in range(e):
                for w in range(channels.shape[2]):
                    out[b,j,bi] += np.int64(_popcount(
                        channels[b,j*width,w] & ~channels[b,j*width+bi+1,w]))
    return out
