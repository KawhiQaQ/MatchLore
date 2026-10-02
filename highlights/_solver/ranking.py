"""Bounded Top-K insertion with deterministic tie breaking."""
import numpy as np
from numba import njit

@njit(cache=True)
def _offer(meta,ns,ks,nums,dens,size,a,b,ai,bi,cap,state,width,num,den,ranks):
    pos=size
    for t in range(size):
        left,right=num*dens[t],nums[t]*den
        less=left<right
        if left==right:
            vals=(a,b,ai,bi,cap)
            for col in range(5):
                v,w=vals[col],meta[t,col]
                if col==2 or col==3:v,w=ranks[v],ranks[w]
                if v!=w:
                    less=v<w
                    break
        if less:
            pos=t
            break
    if pos>=len(meta):return size,False
    for t in range(min(size,len(meta)-1),pos,-1):
        meta[t]=meta[t-1];ns[t]=ns[t-1];ks[t]=ks[t-1]
        nums[t]=nums[t-1];dens[t]=dens[t-1]
    meta[pos,0]=a;meta[pos,1]=b;meta[pos,2]=ai;meta[pos,3]=bi;meta[pos,4]=cap
    for j in range(3):
        ns[pos,j]=state[j*width];ks[pos,j]=state[j*width+bi+1]
    nums[pos]=num;dens[pos]=den
    return min(size+1,len(meta)),True
