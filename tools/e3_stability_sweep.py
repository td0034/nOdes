import sys, json, time, statistics as st
_RIG_ARG = sys.argv[1] if len(sys.argv) > 1 else ""   # capture BEFORE argv is cleared
# Frames per grid cell. The old value (28 frames @ 0.6 s = 17 s) is too short to
# block-bootstrap at long tau: a block must span one EWMA time constant, so
# tau=12 s would give barely one block. 100 frames = 60 s gives >=5 blocks at
# tau=12 and ~10 at tau=6.
_NFR = int(sys.argv[2]) if len(sys.argv) > 2 else 100
sys.argv=['x']
exec(open('tools/e2_threshold_sweep.py').read().split('if __name__')[0])  # read(), ari()
CSVF="server/prompt_settings.csv"; OUT="data/calibration-2026/E1_runs"
def set_param(key,val):
    lines=open(CSVF).read().splitlines(); out=[]; found=False
    for ln in lines:
        if ln.startswith(f"GLOBAL,{key},"): out.append(f"GLOBAL,{key},{val}"); found=True
        else: out.append(ln)
    if not found: out.append(f"GLOBAL,{key},{val}")
    open(CSVF,"w").write("\n".join(out)+"\n")
def partition(): 
    o=read(); return {s:o[s]['cluster'] for s in o}
TAUS=[1.5,3.0,6.0,12.0]; SWS=[100,400,800]
grid={}
RIG=_RIG_ARG
# Log every at-rest partition, not just the per-cell mean: instability CIs are
# impossible from means alone, which is why E3 has none in the paper.
# Same hazard E2 had: an interrupted grid used to leave the fleet on whatever
# tau/debounce cell it died in. Restore the deployed defaults on ANY exit.
import atexit
atexit.register(lambda: (set_param("rssi_decay_tau",6.0),
                         set_param("cluster_switch_ms",400),
                         print("\n[atexit] tau/debounce restored to 6.0/400")))
f,rawpath=open_raw("E3", RIG, f"grid taus={TAUS} sws={SWS} nfr={_NFR}")
print(f"{_NFR} frames/cell (~{_NFR*0.6:.0f}s), {len(TAUS)*len(SWS)} cells "
      f"-> ~{len(TAUS)*len(SWS)*(9+_NFR*0.6)/60:.0f} min total")
print(f"{'tau':>5} {'sw_ms':>6} {'instab':>7} {'#clu(mean)':>10}")
for tau in TAUS:
    for sw in SWS:
        set_param("rssi_decay_tau",tau); set_param("cluster_switch_ms",sw)
        # Settle must scale with tau. A flat 9 s is under ONE time constant at
        # tau=12, so sampling began while the EWMA was still equilibrating and
        # the residual transient scored as instability -- which is what produced
        # the apparent tau=12 "inversion" (it vanishes entirely once the first
        # 25 s are discarded). Three time constants is ~95% settled.
        settle = max(9.0, 3.0 * tau)
        print(f"  settling {settle:.0f}s (3 x tau)...", flush=True)
        time.sleep(settle)
        parts=sample(f,_NFR,0.6,tau=tau,sw_ms=sw)  # at-rest partitions, all logged
        aris=[ari(parts[i],parts[i+1]) for i in range(len(parts)-1)]
        aris=[a for a in aris if a==a]             # drop nan
        instab=1-st.mean(aris) if aris else float('nan')
        nclu=st.mean(len(set(p.values())) for p in parts)
        grid[(tau,sw)]=(instab,nclu)
        print(f"{tau:>5} {sw:>6} {instab:>7.3f} {nclu:>10.2f}")
set_param("rssi_decay_tau",6.0); set_param("cluster_switch_ms",400)  # restore defaults
f.close(); print(f"raw closed: {rawpath}")
json.dump({"raw":_os.path.basename(rawpath),"taus":TAUS,"sws":SWS,
           "instability":{f"{t},{s}":round(grid[(t,s)][0],3) for t in TAUS for s in SWS},
           "nclusters":{f"{t},{s}":round(grid[(t,s)][1],2) for t in TAUS for s in SWS},
           "metric":"1-mean(ARI(consecutive at-rest partitions)); lower=more stable",
           "rig":RIG or "spiral 1/2/3/5/8/7, cluster_min_thresh=220"}, open(OUT+"/E3_stability.json","w"),indent=1)
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt; import numpy as np
M=np.array([[grid[(t,s)][0] for s in SWS] for t in TAUS])
fig,ax=plt.subplots(figsize=(6,4.6))
im=ax.imshow(M,cmap="RdYlGn_r",aspect="auto",origin="lower")
ax.set_xticks(range(len(SWS))); ax.set_xticklabels([f"{s}ms" for s in SWS])
ax.set_yticks(range(len(TAUS))); ax.set_yticklabels([f"{t}s" for t in TAUS])
ax.set_xlabel("cluster_switch_ms (debounce)"); ax.set_ylabel("rssi_decay_tau")
ax.set_title("E3 — at-rest cluster instability\n(1-ARI between consecutive partitions; green=stable)")
for i,t in enumerate(TAUS):
    for j,s in enumerate(SWS): ax.text(j,i,f"{M[i,j]:.2f}",ha="center",va="center",fontsize=9)
# mark current default 6.0/400
ax.add_patch(plt.Rectangle((SWS.index(400)-0.5,TAUS.index(6.0)-0.5),1,1,fill=False,edgecolor="k",lw=2))
ax.text(SWS.index(400),TAUS.index(6.0)+0.34,"default",ha="center",fontsize=7)
fig.colorbar(im,label="instability (lower=better)"); plt.tight_layout(); plt.savefig(OUT+"/E3_stability.png",dpi=140)
print("saved E3_stability.{json,png}")
