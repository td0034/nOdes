import json, statistics as st
from collections import defaultdict, Counter
CAP="network_testing/captures/raw_topk_2026-06-30/topk_1782836712.jsonl"
OUT="data/calibration-2026/E1_runs"

def load(stride=3):
    frames=[]
    for i,line in enumerate(open(CAP)):
        if i%stride: continue
        d=json.loads(line); s2s={int(k):v for k,v in d['s2s'].items()}
        dirs=defaultdict(dict)
        for ser,o in d['orbs'].items():
            for e in o['rp']:
                nb=s2s.get(int(e['slot']))
                if nb: dirs[ser][nb]=int(e['str'])
        sers=sorted(d['orbs'].keys()); sym={}
        for ai,a in enumerate(sers):
            for b in sers[ai+1:]:
                v=[x for x in (dirs.get(a,{}).get(b), dirs.get(b,{}).get(a)) if x is not None]
                if v: sym[(a,b)]=sum(v)/len(v)
        frames.append((sers,sym))
    return frames

def to_sat(sym): return {k:min(255, v*3/2.55) for k,v in sym.items()}   # de-sat -> saturated (clamp@255)
def ari(a,b):
    ks=[k for k in a if k in b]
    if len(ks)<2: return 1.0
    from collections import Counter
    ca=[a[k] for k in ks]; cb=[b[k] for k in ks]
    cont=Counter(zip(ca,cb)); ai=Counter(ca); bi=Counter(cb); n=len(ks); c2=lambda x:x*(x-1)//2
    idx=sum(c2(v) for v in cont.values()); ea=sum(c2(v) for v in ai.values()); eb=sum(c2(v) for v in bi.values())
    exp=ea*eb/c2(n) if c2(n) else 0; mx=(ea+eb)/2
    return (idx-exp)/(mx-exp) if mx!=exp else 1.0
def flood(sers,sym,thr):
    adj=defaultdict(list)
    for (a,b),v in sym.items():
        if v>=thr: adj[a].append(b); adj[b].append(a)
    lab={}; cid=0
    for s in sers:
        if s in lab: continue
        stk=[s]
        while stk:
            x=stk.pop()
            if x in lab: continue
            lab[x]=cid
            for y in adj[x]:
                if y not in lab: stk.append(y)
        cid+=1
    return lab
def adaptive(strs, ema):
    s=sorted(strs); n=len(s); cut=230
    if n>=4:
        mid=n//2; bg=0
        for i in range(mid+1,n):
            g=s[i]-s[i-1]
            if g>bg: bg=g; cut=(s[i-1]+s[i])/2
        if bg<=5: cut=s[mid]
    return cut if ema is None else 0.9*ema+0.1*cut

def evaluate(frames, sat, mode, floor=None, fixedT=None):
    parts=[]; ema=None
    for sers,sym in frames:
        S=to_sat(sym) if sat else sym
        thr = (lambda: (max(round(adaptive(S.values(),ema)), floor)))() if mode=='adaptive' else fixedT
        if mode=='adaptive': ema=adaptive(S.values(),ema)
        parts.append(flood(sers,S,thr))
    instab=1-st.mean([ari(parts[i],parts[i+1]) for i in range(len(parts)-1)])
    ncl=[len(set(p.values())) for p in parts]
    sigs=Counter(tuple(sorted(Counter(p.values()).values(),reverse=True)) for p in parts)
    clean=100*sigs.get((8,7,5,3,2,1),0)//len(parts)
    return {'instab':round(instab,3),'ncl_mode':Counter(ncl).most_common(1)[0][0],'ncl_med':int(st.median(ncl)),'clean6_pct':clean}

frames=load(stride=3); print(f"loaded {len(frames)} frames (stride 3)\n")
print("=== ADAPTIVE-GAP clustering (deployed algo), floor=180 ===")
for sat,name in [(False,'DE-SAT (3.24, actual)'),(True,'SATURATED (simulated ×3 clamp)')]:
    r=evaluate(frames,sat,'adaptive',floor=180)
    print(f"  {name:32} instab {r['instab']}  clusters(med) {r['ncl_med']}  hits[8,7,5,3,2,1] {r['clean6_pct']}%")
print("\n=== FIXED-THRESHOLD sweep (no adaptive): best stability per mapping ===")
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
fig,ax=plt.subplots(1,2,figsize=(12,4.5))
res={}
for sat,name,col in [(False,'de-saturated',' #b03a2e'),(True,'saturated',' #1a7a3a')]:
    Ts=list(range(150,256,8)); ins=[]; cln=[]
    for T in Ts:
        r=evaluate(frames,sat,'fixed',fixedT=T); ins.append(r['instab']); cln.append(r['clean6_pct'])
    res[name]=(Ts,ins,cln)
    best=min(zip(Ts,ins),key=lambda x:x[1])
    print(f"  {name:14} best fixed thr={best[0]} -> instab {best[1]:.3f}  | max clean6 {max(cln)}%")
    ax[0].plot(Ts,ins,'o-',label=name,color=col.strip()); ax[1].plot(Ts,cln,'o-',label=name,color=col.strip())
ax[0].set_xlabel('fixed threshold'); ax[0].set_ylabel('instability (1-ARI)'); ax[0].set_title('Stability vs threshold'); ax[0].legend(); ax[0].grid(alpha=0.25)
ax[1].set_xlabel('fixed threshold'); ax[1].set_ylabel('% frames = [8,7,5,3,2,1]'); ax[1].set_title('Correct-resolution rate'); ax[1].legend(); ax[1].grid(alpha=0.25)
fig.suptitle('Saturation vs De-saturation — same spiral data, controlled comparison',weight='bold')
plt.tight_layout(); plt.savefig(OUT+"/sat_vs_desat.png",dpi=140)
json.dump({'n_frames':len(frames),'fixed_sweep':{k:{'thr':v[0],'instab':v[1],'clean6':v[2]} for k,v in res.items()}}, open(OUT+"/sat_vs_desat.json","w"),indent=1)
print("\nsaved sat_vs_desat.{json,png}")
