// turing_core.js — Gray–Scott reaction–diffusion on the sphere + SDF shell
// model + mesher for the Turing Orb Designer. Pure JS, no DOM: runs in the
// browser and in Node for headless tests. Lengths in mm.
"use strict";

// ---------- vector helpers --------------------------------------------------
function vnorm(p){const n=Math.hypot(p[0],p[1],p[2]);return[p[0]/n,p[1]/n,p[2]/n];}
function vsub(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]];}
function vcross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function vdot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
function vdist(a,b){return Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]);}

function icosa(){
  const phi=(1+Math.sqrt(5))/2;
  let raw=[];
  for(const s1 of[-1,1])for(const s2 of[-1,1])
    raw.push([0,s1,s2*phi],[s1,s2*phi,0],[s1*phi,0,s2]);
  const seen={},v=[];
  for(const q of raw){const u=vnorm(q),k=u.map(x=>x.toFixed(5)).join(",");
    if(!seen[k]){seen[k]=1;v.push(u);}}
  let e=1e9;
  for(let i=0;i<12;i++)for(let j=i+1;j<12;j++)e=Math.min(e,vdist(v[i],v[j]));
  let top=v[0];for(const q of v)if(q[2]>top[2])top=q;
  let adj=null;
  for(const q of v)if(Math.abs(vdist(q,top)-e)<1e-4&&(adj===null||q[0]>adj[0]))adj=q;
  const zx=top;
  const xr=vsub(adj,[zx[0]*vdot(adj,zx),zx[1]*vdot(adj,zx),zx[2]*vdot(adj,zx)]);
  const xx=vnorm(xr),yx=vcross(zx,xx);
  const vv=v.map(q=>[vdot(q,xx),vdot(q,yx),vdot(q,zx)]);
  const f=[];
  for(let i=0;i<12;i++)for(let j=i+1;j<12;j++){
    if(Math.abs(vdist(vv[i],vv[j])-e)>1e-4)continue;
    for(let k=j+1;k<12;k++)
      if(Math.abs(vdist(vv[i],vv[k])-e)<1e-4&&Math.abs(vdist(vv[j],vv[k])-e)<1e-4)
        f.push([i,j,k]);
  }
  return{v:vv,faces:f};
}

// icosphere: subdivision vertices + neighbour lists (graph Laplacian domain)
function icosphere(freq){
  const{v,faces}=icosa();
  const verts=[],key2idx={},tris=[];
  function idx(p){
    const q=vnorm(p),k=q.map(x=>x.toFixed(5)).join(",");
    if(!(k in key2idx)){key2idx[k]=verts.length;verts.push(q);}
    return key2idx[k];
  }
  for(const[a,b,c]of faces){
    const A=v[a],B=v[b],C=v[c],grid=[];
    for(let i=0;i<=freq;i++){
      const row=[];
      for(let j=0;j<=freq-i;j++){
        const k=freq-i-j;
        row.push(idx([i*A[0]+j*B[0]+k*C[0],i*A[1]+j*B[1]+k*C[1],i*A[2]+j*B[2]+k*C[2]]));
      }
      grid.push(row);
    }
    for(let i=0;i<freq;i++)for(let j=0;j<freq-i;j++){
      tris.push([grid[i][j],grid[i][j+1],grid[i+1][j]]);
      if(j<freq-i-1)tris.push([grid[i][j+1],grid[i+1][j+1],grid[i+1][j]]);
    }
  }
  const nb=verts.map(()=>new Set());
  for(const[a,b,c]of tris){nb[a].add(b).add(c);nb[b].add(a).add(c);nb[c].add(a).add(b);}
  const neigh=nb.map(s=>Int32Array.from(s));
  return{verts,neigh,tris};
}

// connector axis: icosahedron face centre (3-fold, equidistant from 3 vertices)
const BANDS=[
  {label:"Bottom ring",polar:142.6,az0:0},
  {label:"Lower equator",polar:100.8,az0:0},
  {label:"Upper equator",polar:79.2,az0:36},
  {label:"Top ring",polar:37.4,az0:36},
];
function connectorAxis(band,k){
  const{v,faces}=icosa();
  const th=BANDS[band].polar*Math.PI/180,ph=(BANDS[band].az0+72*k)*Math.PI/180;
  const want=[Math.sin(th)*Math.cos(ph),Math.sin(th)*Math.sin(ph),Math.cos(th)];
  let best=null,bang=-2;
  for(const[a,b,c]of faces){
    const ctr=vnorm([v[a][0]+v[b][0]+v[c][0],v[a][1]+v[b][1]+v[c][1],v[a][2]+v[b][2]+v[c][2]]);
    const d=vdot(ctr,want);
    if(d>bang){bang=d;best=ctr;}
  }
  return best;
}

// ---------- Gray–Scott simulation on the icosphere --------------------------
// Q: {res, Du, Dv, F, k, dt, steps, seeds, seedNoise, rngSeed}
// Classic Pearson formulation, graph Laplacian L(u) = mean(neighbours) − u
// (unit lattice spacing, so Du/Dv/F/k/dt carry their usual paper values).
function mulberry32(a){
  return function(){
    a|=0;a=a+0x6D2B79F5|0;
    let t=Math.imul(a^a>>>15,1|a);
    t=t+Math.imul(t^t>>>7,61|t)^t;
    return((t^t>>>14)>>>0)/4294967296;
  };
}

// Q.model: "gs" (Gray–Scott, Pearson) or "linear" (Miyazawa linear
// activator–inhibitor as used on the Bristol kilobots, Slavkov et al. 2018:
// syn_u = clamp(A·u + B·v + C, 0, synU), du/dt = R(syn_u − γu·u) + Du·Σ(u_j−u_i)
// syn_v = clamp(E·u − F, 0, synV),      dv/dt = R(syn_v − γv·v) + Dv·Σ(v_j−v_i))
function simulate(Q,progress){
  const{verts,neigh}=icosphere(Q.res);
  const n=verts.length;
  const rng=mulberry32(Q.rngSeed|0||1);
  const linear=Q.model==="linear";
  const u=new Float32Array(n).fill(linear?1:1),v=new Float32Array(n);
  if(linear){
    for(let i=0;i<n;i++){u[i]=1+(rng()-0.5)*0.2;v[i]=(rng())*0.2;}
  }
  // seed patches
  for(let s=0;s<Q.seeds;s++){
    const dir=vnorm([rng()*2-1,rng()*2-1,rng()*2-1]);
    const cosR=Math.cos(0.12);
    for(let i=0;i<n;i++)
      if(vdot(verts[i],dir)>cosR){
        if(linear){u[i]=4;}else{u[i]=0.50;v[i]=0.25;}
      }
  }
  if(Q.seedNoise>0)
    for(let i=0;i<n;i++){
      u[i]=Math.max(0,u[i]+(rng()-0.5)*Q.seedNoise*(linear?4:1));
      if(!linear)u[i]=Math.min(1,u[i]);
      v[i]=Math.max(0,v[i]+(rng()-0.5)*Q.seedNoise*0.5);
      if(!linear)v[i]=Math.min(1,v[i]);
    }
  const lu=new Float32Array(n),lv=new Float32Array(n);
  const{Du,Dv,F,k,dt}=Q;
  const clamp=(x,lo,hi)=>x<lo?lo:x>hi?hi:x;
  for(let step=0;step<Q.steps;step++){
    for(let i=0;i<n;i++){
      const nb=neigh[i];
      let su=0,sv=0;
      for(let j=0;j<nb.length;j++){su+=u[nb[j]];sv+=v[nb[j]];}
      if(linear){                       // kilobot diffusion: sum of differences
        lu[i]=su-nb.length*u[i];
        lv[i]=sv-nb.length*v[i];
      }else{                            // GS convention: mean − self
        lu[i]=su/nb.length-u[i];
        lv[i]=sv/nb.length-v[i];
      }
    }
    if(linear){
      const{A,B,C,gu,E,Fv,gv,synU,synV,R}=Q;
      for(let i=0;i<n;i++){
        const synu=clamp(A*u[i]+B*v[i]+C,0,synU);
        const synv=clamp(E*u[i]-Fv,0,synV);
        u[i]=Math.max(0,u[i]+dt*(R*(synu-gu*u[i])+Du*lu[i]));
        v[i]=Math.max(0,v[i]+dt*(R*(synv-gv*v[i])+Dv*lv[i]));
      }
    }else{
      for(let i=0;i<n;i++){
        const uvv=u[i]*v[i]*v[i];
        u[i]+=dt*(Du*lu[i]-uvv+F*(1-u[i]));
        v[i]+=dt*(Dv*lv[i]+uvv-(F+k)*v[i]);
      }
    }
    if(progress&&step%200===0)progress(step/Q.steps);
  }
  // pattern channel: GS shows v, the kilobot LEDs show u
  return{verts,u,v,pat:linear?u:v,n};
}

// resample the vertex field onto a wrap-around lat-lon texture (bilinear-
// sampleable), value = v channel normalised to [0,1]
function fieldTexture(sim,W,H){
  const{verts,pat,n}=sim;
  const v=pat;
  let vmax=1e-9;for(let i=0;i<n;i++)if(v[i]>vmax)vmax=v[i];
  // bucket verts by direction for nearest lookup
  const G=64,buckets=new Array(G*G*G);
  const bi=x=>Math.min(G-1,Math.max(0,Math.floor((x+1)/2*G)));
  for(let i=0;i<n;i++){
    const p=verts[i],j=(bi(p[0])*G+bi(p[1]))*G+bi(p[2]);
    (buckets[j]=buckets[j]||[]).push(i);
  }
  function nearest(d){
    const cx=bi(d[0]),cy=bi(d[1]),cz=bi(d[2]);
    let best=-1,bd=-2;
    for(let r=0;r<3;r++){
      for(let x=Math.max(0,cx-r);x<=Math.min(G-1,cx+r);x++)
      for(let y=Math.max(0,cy-r);y<=Math.min(G-1,cy+r);y++)
      for(let z=Math.max(0,cz-r);z<=Math.min(G-1,cz+r);z++){
        const b=buckets[(x*G+y)*G+z];
        if(b)for(const i of b){
          const dd=verts[i][0]*d[0]+verts[i][1]*d[1]+verts[i][2]*d[2];
          if(dd>bd){bd=dd;best=i;}
        }
      }
      if(best>=0&&r>=1)break;
    }
    return best;
  }
  const tex=new Float32Array(W*H);
  for(let y=0;y<H;y++){
    const th=(y+0.5)/H*Math.PI,sz=Math.sin(th),cz=Math.cos(th);
    for(let x=0;x<W;x++){
      const ph=(x+0.5)/W*2*Math.PI;
      const i=nearest([sz*Math.cos(ph),sz*Math.sin(ph),cz]);
      tex[y*W+x]=i>=0?v[i]/vmax:0;
    }
  }
  // two smoothing passes to soften the Voronoi facets
  const tmp=new Float32Array(W*H);
  for(let pass=0;pass<2;pass++){
    for(let y=0;y<H;y++)for(let x=0;x<W;x++){
      const xl=(x+W-1)%W,xr=(x+1)%W,yu=Math.max(0,y-1),yd=Math.min(H-1,y+1);
      tmp[y*W+x]=(tex[y*W+x]*2+tex[y*W+xl]+tex[y*W+xr]+tex[yu*W+x]+tex[yd*W+x])/6;
    }
    tex.set(tmp);
  }
  // median gradient magnitude (per radian) for distance normalisation
  const gs=[];
  for(let y=1;y<H-1;y+=3)for(let x=0;x<W;x+=3){
    const xl=(x+W-1)%W,xr=(x+1)%W;
    const sth=Math.max(0.2,Math.sin((y+0.5)/H*Math.PI));
    const gx=(tex[y*W+xr]-tex[y*W+xl])/(2*(2*Math.PI/W)*sth);
    const gy=(tex[(y+1)*W+x]-tex[(y-1)*W+x])/(2*(Math.PI/H));
    const g=Math.hypot(gx,gy);
    if(g>1e-4)gs.push(g);
  }
  gs.sort((a,b)=>a-b);
  const gmed=gs.length?gs[Math.floor(gs.length*0.5)]:1;
  return{tex,W,H,gmed};
}

function sampleTex(T,d){
  const th=Math.acos(Math.max(-1,Math.min(1,d[2]))),ph=Math.atan2(d[1],d[0]);
  let fx=(ph/(2*Math.PI)+1)%1*T.W-0.5,fy=th/Math.PI*T.H-0.5;
  const x0=Math.floor(fx),y0=Math.floor(fy);
  const ax=fx-x0,ay=fy-y0;
  const X0=(x0+T.W)%T.W,X1=(x0+1+T.W)%T.W;
  const Y0=Math.max(0,Math.min(T.H-1,y0)),Y1=Math.max(0,Math.min(T.H-1,y0+1));
  return T.tex[Y0*T.W+X0]*(1-ax)*(1-ay)+T.tex[Y0*T.W+X1]*ax*(1-ay)
        +T.tex[Y1*T.W+X0]*(1-ax)*ay+T.tex[Y1*T.W+X1]*ax*ay;
}

// coverage fraction of the pattern at a threshold (area-weighted)
function coverage(T,thr,invert){
  let a=0,w=0;
  for(let y=0;y<T.H;y++){
    const s=Math.sin((y+0.5)/T.H*Math.PI);
    for(let x=0;x<T.W;x++){
      const on=invert?T.tex[y*T.W+x]<thr:T.tex[y*T.W+x]>thr;
      if(on)a+=s;
      w+=s;
    }
  }
  return a/w;
}

// ---------- SDF model -------------------------------------------------------
function smin(a,b,k){
  if(k<=0)return Math.min(a,b);
  const h=Math.max(k-Math.abs(a-b),0)/k;
  return Math.min(a,b)-h*h*k*0.25;
}
function smax(a,b,k){return -smin(-a,-b,k);}

// P: {innerD, depth, thr, invert, memMode, memT, memFillet, edgeFillet,
//     connD, connT, holeD, cbD, neck, connBand, connK,
//     rimP, pcbD, pcbT, rimT, rimF, barb, split, gap}
function buildModel(P,T){
  const Rin=P.innerD/2,Rout=Rin+P.depth,rMid=(Rin+Rout)/2;
  const H=Rout+3;
  const ax=P.connD>0?connectorAxis(P.connBand,P.connK):null;
  const maxK=Math.max(P.edgeFillet,P.memFillet,0.5);
  const rimOn=(P.rimP||0)>0;
  let inThresh=maxK+2.0;
  if(rimOn)inThresh=Math.max(inThresh,P.rimP+2.5);
  if(ax)inThresh=Math.max(inThresh,(P.connT-P.depth)+2.5);
  const dscale=rMid/T.gmed;                // field units -> tangential mm

  function fSolid(px,py,pz){
    const rlen=Math.hypot(px,py,pz);
    const fOut=rlen-Rout,fIn=Rin-rlen;
    const fBand=Math.max(fOut,fIn);
    if(fOut>maxK+2.0)return fOut;
    if(fIn>inThresh)return fIn;
    const ir=rlen>1e-9?1/rlen:0;
    const val=sampleTex(T,[px*ir,py*ir,pz*ir]);
    let d=(P.invert?(val-P.thr):(P.thr-val))*dscale;   // <0 inside walls
    let lat=smax(d,fOut,P.edgeFillet);
    lat=Math.max(lat,fIn);
    let solid=lat;
    if(P.memMode===1){
      const mem=Math.max(rlen-(Rin+P.memT),fIn);
      solid=smin(solid,mem,P.memFillet);
    }else if(P.memMode===2){
      const mem=Math.max(fOut,(Rout-P.memT)-rlen);
      solid=smin(solid,mem,P.memFillet);
    }
    solid=Math.max(solid,fBand);
    if(ax){
      const s=px*ax[0]+py*ax[1]+pz*ax[2];
      const rho=Math.sqrt(Math.max(rlen*rlen-s*s,0));
      const boss=Math.max(rho-P.connD/2,Math.max((Rout-P.connT)-s,fOut));
      solid=smin(solid,boss,0.6);
      const shoulder=Rout-P.neck;
      const hole=Math.max(rho-P.holeD/2,shoulder-s);
      const cb=Math.max(rho-P.cbD/2,
                        Math.max(s-shoulder,(Rout-P.connT-2.0)-s));
      solid=Math.max(solid,-Math.min(hole,cb));
    }
    return solid;
  }

  // plain equator cut (no lattice teeth in the turing variant)
  function fCut(px,py,pz){return -pz;}

  // PCB harpoon rims, identical to the silicone designer
  const bh=1.5*(P.barb||0)+0.5;
  const bslope=(P.barb||0)/bh,bnorm=Math.sqrt(1+bslope*bslope);
  const rimRlim=P.memMode===1?Rin+0.4*P.memT:Rin+1.2;
  function fRim(px,py,pz,mode){
    if(!rimOn)return 1e6;
    const rho=Math.hypot(px,py),z0=P.pcbT/2;
    const wall=Math.hypot(px,py,pz)-rimRlim;
    function deep(z){
      return Math.max(Math.max(z0-z,z-(z0+P.rimT)),
                      Math.max((Rin-P.rimP)-rho,wall));
    }
    function barb(z){
      return Math.max(Math.max(z0-z,((Rin-P.barb)+bslope*(z-z0)-rho)/bnorm),
                      wall);
    }
    if(mode===0)return Math.min(deep(pz),deep(-pz));
    if(mode>0)return Math.min(deep(pz),barb(-pz));
    return Math.min(deep(-pz),barb(pz));
  }

  return{fSolid,fCut,fRim,Rin,Rout,rMid,H};
}

// ---------- surface nets mesher (same as the silicone designer) -------------
function sampleField(f,H,N){
  const step=2*H/(N-1),o=-H;
  const nodes=new Float32Array(N*N*N);
  let i=0;
  for(let x=0;x<N;x++){const px=o+x*step;
    for(let y=0;y<N;y++){const py=o+y*step;
      for(let z=0;z<N;z++)nodes[i++]=f(px,py,o+z*step);
    }
  }
  return nodes;
}

function meshFromNodes(nodes,H,N){
  const step=2*H/(N-1),o=-H;
  let inside=0;
  for(let i=0;i<nodes.length;i++)if(nodes[i]<0)inside++;
  const nid=(x,y,z)=>(x*N+y)*N+z;
  const cellVert=new Int32Array((N-1)*(N-1)*(N-1)).fill(-1);
  const cid=(x,y,z)=>(x*(N-1)+y)*(N-1)+z;
  const positions=[];
  const EDGES=[[0,0,0,1,0,0],[0,1,0,1,1,0],[0,0,1,1,0,1],[0,1,1,1,1,1],
               [0,0,0,0,1,0],[1,0,0,1,1,0],[0,0,1,0,1,1],[1,0,1,1,1,1],
               [0,0,0,0,0,1],[1,0,0,1,0,1],[0,1,0,0,1,1],[1,1,0,1,1,1]];
  function makeVert(x,y,z){
    const c=cid(x,y,z);
    if(cellVert[c]>=0)return cellVert[c];
    let sx=0,sy=0,sz=0,n=0;
    for(const[ax,ay,az,bx,by,bz]of EDGES){
      const va=nodes[nid(x+ax,y+ay,z+az)],vb=nodes[nid(x+bx,y+by,z+bz)];
      if((va<0)!==(vb<0)){
        const t=va/(va-vb);
        sx+=x+ax+(bx-ax)*t;sy+=y+ay+(by-ay)*t;sz+=z+az+(bz-az)*t;n++;
      }
    }
    if(n===0){sx=x+0.5;sy=y+0.5;sz=z+0.5;n=1;}
    const idx=positions.length/3;
    positions.push(o+sx/n*step,o+sy/n*step,o+sz/n*step);
    cellVert[c]=idx;
    return idx;
  }
  const indices=[];
  for(let x=0;x<N-1;x++)for(let y=0;y<N-1;y++)for(let z=0;z<N-1;z++){
    const v0=nodes[nid(x,y,z)];
    if(y>0&&z>0){
      const v1=nodes[nid(x+1,y,z)];
      if((v0<0)!==(v1<0)){
        const q=[makeVert(x,y,z),makeVert(x,y-1,z),makeVert(x,y-1,z-1),makeVert(x,y,z-1)];
        if(v0<0)q.reverse();
        indices.push(q[0],q[1],q[2],q[0],q[2],q[3]);
      }
    }
    if(x>0&&z>0){
      const v1=nodes[nid(x,y+1,z)];
      if((v0<0)!==(v1<0)){
        const q=[makeVert(x,y,z),makeVert(x,y,z-1),makeVert(x-1,y,z-1),makeVert(x-1,y,z)];
        if(v0<0)q.reverse();
        indices.push(q[0],q[1],q[2],q[0],q[2],q[3]);
      }
    }
    if(x>0&&y>0){
      const v1=nodes[nid(x,y,z+1)];
      if((v0<0)!==(v1<0)){
        const q=[makeVert(x,y,z),makeVert(x-1,y,z),makeVert(x-1,y-1,z),makeVert(x,y-1,z)];
        if(v0<0)q.reverse();
        indices.push(q[0],q[1],q[2],q[0],q[2],q[3]);
      }
    }
  }
  return{positions:new Float32Array(positions),
         indices:new Uint32Array(indices),
         volume:inside*step*step*step,
         nVerts:positions.length/3,nTris:indices.length/3};
}

if(typeof module!=="undefined")module.exports={
  icosphere,simulate,fieldTexture,sampleTex,coverage,connectorAxis,
  buildModel,sampleField,meshFromNodes,smin,smax,BANDS,icosa};
