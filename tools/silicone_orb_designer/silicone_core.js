// silicone_core.js — geometry + SDF + mesher for the Silicone Orb Designer.
// Pure JS, no DOM: runs in the browser (inlined into the HTML app) and in
// Node for headless smoke tests. All lengths in mm.
"use strict";

// ---------- vector helpers --------------------------------------------------
function vnorm(p){const n=Math.hypot(p[0],p[1],p[2]);return[p[0]/n,p[1]/n,p[2]/n];}
function vsub(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]];}
function vcross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
function vdot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
function vdist(a,b){return Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]);}

// ---------- Goldberg net (same construction as the Fusion scripts) ----------
function rotZ(p,deg){
  if(!deg)return p;
  const c=Math.cos(deg*Math.PI/180),s=Math.sin(deg*Math.PI/180);
  return[c*p[0]-s*p[1],s*p[0]+c*p[1],p[2]];
}

function icosa(rotDeg){
  const phi=(1+Math.sqrt(5))/2;
  let raw=[];
  for(const s1 of[-1,1])for(const s2 of[-1,1])
    raw.push([0,s1,s2*phi],[s1,s2*phi,0],[s1*phi,0,s2]);
  const seen={},v=[];
  for(const q of raw){const u=vnorm(q),k=u.map(x=>x.toFixed(5)).join(",");
    if(!seen[k]){seen[k]=1;v.push(u);}}
  let e=1e9;
  for(let i=0;i<12;i++)for(let j=i+1;j<12;j++)e=Math.min(e,vdist(v[i],v[j]));
  // orient: vertex on +Z, one neighbour at azimuth 0, then spin about Z
  let top=v[0];for(const q of v)if(q[2]>top[2])top=q;
  let adj=null;
  for(const q of v)if(Math.abs(vdist(q,top)-e)<1e-4&&(adj===null||q[0]>adj[0]))adj=q;
  const zx=top;
  const xr=vsub(adj,[zx[0]*vdot(adj,zx),zx[1]*vdot(adj,zx),zx[2]*vdot(adj,zx)]);
  const xx=vnorm(xr),yx=vcross(zx,xx);
  const vv=v.map(q=>rotZ([vdot(q,xx),vdot(q,yx),vdot(q,zx)],rotDeg));
  const f=[];
  for(let i=0;i<12;i++)for(let j=i+1;j<12;j++){
    if(Math.abs(vdist(vv[i],vv[j])-e)>1e-4)continue;
    for(let k=j+1;k<12;k++)
      if(Math.abs(vdist(vv[i],vv[k])-e)<1e-4&&Math.abs(vdist(vv[j],vv[k])-e)<1e-4)
        f.push([i,j,k]);
  }
  return{v:vv,faces:f};
}

// cells: unit cell-centre vectors (subdivision vertices; 12 pentagons)
// ribs: {a,b,p,q} rib from node a to node b, separating cells p and q
function goldbergNet(freq,rotDeg){
  const{v,faces}=icosa(rotDeg||0);
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
  const cc=tris.map(([a,b,c])=>{
    const A=verts[a],B=verts[b],C=verts[c];
    let n=vnorm(vcross(vsub(B,A),vsub(C,A)));
    if(vdot(n,A)<0)n=[-n[0],-n[1],-n[2]];
    return n;
  });
  const emap={},ribs=[];
  tris.forEach(([a,b,c],t)=>{
    for(const[p,q]of[[a,b],[b,c],[c,a]]){
      const k=Math.min(p,q)+"_"+Math.max(p,q);
      if(k in emap)ribs.push({a:cc[emap[k]],b:cc[t],p,q});
      else emap[k]=t;
    }
  });
  return{cells:verts,ribs};
}

// alternate the equatorial cell ring as interlocking teeth (even freq)
function equatorSplit(cells,ribs){
  const side=cells.map(c=>c[2]>0);
  const ring=[];
  cells.forEach((c,i)=>{if(Math.abs(c[2])<1e-6)ring.push(i);});
  ring.sort((i,j)=>Math.atan2(cells[i][1],cells[i][0])-Math.atan2(cells[j][1],cells[j][0]));
  ring.forEach((ci,n)=>{side[ci]=n%2===0;});
  const seam=[];
  ribs.forEach((r,k)=>{if(side[r.p]!==side[r.q])seam.push(k);});
  return{side,seam,ring};
}

// icosahedron face centre nearest (polarDeg, azDeg): the connector axis
const BANDS=[
  {label:"Bottom ring",polar:142.6,az0:0},
  {label:"Lower equator",polar:100.8,az0:0},
  {label:"Upper equator",polar:79.2,az0:36},
  {label:"Top ring",polar:37.4,az0:36},
];
function connectorAxis(band,k,rotDeg){
  const{v,faces}=icosa(rotDeg||0);
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

// ---------- SDF model -------------------------------------------------------
// P: {freq, rib, depth, innerD, rot, memMode(0none/1internal/2external),
//     memT, memFillet, edgeFillet, apFillet, connD, connT, connBand, connK,
//     split, seamRib, gap}
function smin(a,b,k){
  if(k<=0)return Math.min(a,b);
  const h=Math.max(k-Math.abs(a-b),0)/k;
  return Math.min(a,b)-h*h*k*0.25;
}
function smax(a,b,k){return -smin(-a,-b,k);}

function buildModel(P){
  const Rin=P.innerD/2,Rout=Rin+P.depth,rMid=(Rin+Rout)/2;
  const net=goldbergNet(P.freq,P.rot);
  const{cells,ribs}=net;
  let side=null,seamSet=null,ring=[],teeth=null;
  if(P.split){
    const sp=equatorSplit(cells,ribs);
    side=sp.side;seamSet=new Set(sp.seam);ring=sp.ring;
    // tooth pyramids: plane normals per straddling ring cell
    const cellRibs={};
    ribs.forEach((r,k)=>{(cellRibs[r.p]=cellRibs[r.p]||[]).push(k);
                         (cellRibs[r.q]=cellRibs[r.q]||[]).push(k);});
    teeth={add:[],sub:[]};
    for(const ci of ring){
      const planes=cellRibs[ci].map(k=>{
        let n=vnorm(vcross(ribs[k].a,ribs[k].b));
        if(vdot(n,cells[ci])<0)n=[-n[0],-n[1],-n[2]];
        return n;
      });
      (side[ci]?teeth.add:teeth.sub).push(planes);
    }
  }
  // rib segments at mid radius, with per-rib half-width
  const seamHalf=P.split?P.seamRib/2:0;
  const segs=ribs.map((r,k)=>({
    A:[r.a[0]*rMid,r.a[1]*rMid,r.a[2]*rMid],
    B:[r.b[0]*rMid,r.b[1]*rMid,r.b[2]*rMid],
    hw:(seamSet&&seamSet.has(k))?seamHalf:P.rib/2,
  }));
  // spatial hash over the bounding cube
  const H=Rout+3,inflate=Math.max(P.rib,P.seamRib||0)/2+P.apFillet+2.5;
  let maxLen=0;for(const s of segs)maxLen=Math.max(maxLen,vdist(s.A,s.B));
  const cs=Math.max(maxLen+2*inflate,6),ng=Math.max(2,Math.ceil(2*H/cs));
  const buckets=new Array(ng*ng*ng);
  const bi=x=>Math.min(ng-1,Math.max(0,Math.floor((x+H)/(2*H)*ng)));
  segs.forEach((s,k)=>{
    const lo=[0,1,2].map(i=>bi(Math.min(s.A[i],s.B[i])-inflate));
    const hi=[0,1,2].map(i=>bi(Math.max(s.A[i],s.B[i])+inflate));
    for(let x=lo[0];x<=hi[0];x++)for(let y=lo[1];y<=hi[1];y++)for(let z=lo[2];z<=hi[2];z++){
      const j=(x*ng+y)*ng+z;
      (buckets[j]=buckets[j]||[]).push(k);
    }
  });
  // connector entry (boss + Ø-hole + counterbore), on a 3-fold axis
  const ax=P.connD>0?connectorAxis(P.connBand,P.connK,P.rot):null;

  const maxK=Math.max(P.edgeFillet,P.apFillet,P.memFillet,0.5);
  const rimOn=(P.rimP||0)>0;
  let inThresh=maxK+2.0;
  if(rimOn)inThresh=Math.max(inThresh,P.rimP+2.5);
  if(ax)inThresh=Math.max(inThresh,(P.connT-P.depth)+2.5);

  // signed distance of the (whole, un-split) solid at point p
  function fSolid(px,py,pz){
    const rlen=Math.hypot(px,py,pz);
    const fOut=rlen-Rout,fIn=Rin-rlen;
    const fBand=Math.max(fOut,fIn);
    if(fOut>maxK+2.0)return fOut;         // far outside the sphere
    if(fIn>inThresh)return fIn;           // deep inside, beyond any rim
    // nearest ribs
    const j=(bi(px)*ng+bi(py))*ng+bi(pz);
    const list=buckets[j];
    let d=1e6;
    const ir=rlen>1e-9?1/rlen:0,rx=px*ir,ry=py*ir,rz=pz*ir;
    if(list)for(const k of list){
      const s=segs[k];
      const abx=s.B[0]-s.A[0],aby=s.B[1]-s.A[1],abz=s.B[2]-s.A[2];
      const apx=px-s.A[0],apy=py-s.A[1],apz=pz-s.A[2];
      let t=(apx*abx+apy*aby+apz*abz)/(abx*abx+aby*aby+abz*abz);
      t=t<0?0:t>1?1:t;
      let ex=apx-t*abx,ey=apy-t*aby,ez=apz-t*abz;
      const er=ex*rx+ey*ry+ez*rz;         // strip: ignore the radial component
      ex-=er*rx;ey-=er*ry;ez-=er*rz;
      const dk=Math.hypot(ex,ey,ez)-s.hw;
      d=smin(d,dk,P.apFillet);
    }
    // lattice = ribs ∩ shell band; outer edge rounded, inner edge sharp
    let lat=smax(d,fOut,P.edgeFillet);
    lat=Math.max(lat,fIn);
    let solid=lat;
    // membrane
    if(P.memMode===1){
      const mem=Math.max(rlen-(Rin+P.memT),fIn);
      solid=smin(solid,mem,P.memFillet);
    }else if(P.memMode===2){
      const mem=Math.max(fOut,(Rout-P.memT)-rlen);
      solid=smin(solid,mem,P.memFillet);
    }
    // final trim to the exact spherical shell (as in the Fusion build):
    // smooth unions dip past the spheres by fillet/4, so clamp last
    solid=Math.max(solid,fBand);
    // connector: boss (may protrude inside the inner sphere), then the
    // Ø-hole to the outside and the counterbore from the inside -- the
    // shoulder between them is what the flange pulls against
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

  // teeth only influence a band around the equator
  let zTeeth=0;
  if(teeth){
    for(const ci of ring)for(const r of ribs)
      if(r.p===ci||r.q===ci)zTeeth=Math.max(zTeeth,Math.abs(r.a[2]),Math.abs(r.b[2]));
    zTeeth=zTeeth*Rout*1.2+1;
  }

  // "top region" field: negative above the castellated cut surface
  function fCut(px,py,pz){
    let T=-pz;
    if(teeth&&Math.abs(pz)<=zTeeth){
      for(const planes of teeth.add){
        let pd=-1e6;
        for(const n of planes){const v=-(px*n[0]+py*n[1]+pz*n[2]);if(v>pd)pd=v;}
        if(pd<T)T=pd;
      }
      for(const planes of teeth.sub){
        let pd=-1e6;
        for(const n of planes){const v=-(px*n[0]+py*n[1]+pz*n[2]);if(v>pd)pd=v;}
        if(-pd>T)T=-pd;
      }
    }
    return T;
  }

  // PCB retention, half-aware. Per half: a full-depth support ring on its
  // own side of the board, and a short ramped barb on the other side (flat
  // locking face toward the PCB) so the board snaps in -- a harpoon clip.
  // mode: 0 = whole orb (both deep rings, display only), +1 top half,
  // -1 bottom half. Large positive when rims are off.
  const bh=1.5*(P.barb||0)+0.5;                  // barb ramp height
  const bslope=(P.barb||0)/bh,bnorm=Math.sqrt(1+bslope*bslope);
  // rims reach outward only to the membrane's outer surface (or 1.2 mm into
  // the ribs when there is no internal membrane) so nothing pokes through
  const rimRlim=P.memMode===1?Rin+0.4*P.memT:Rin+1.2;
  function fRim(px,py,pz,mode){
    if(!rimOn)return 1e6;
    const rho=Math.hypot(px,py),z0=P.pcbT/2;
    const wall=Math.hypot(px,py,pz)-rimRlim;
    function deep(z){                            // support ring on the +z side
      return Math.max(Math.max(z0-z,z-(z0+P.rimT)),
                      Math.max((Rin-P.rimP)-rho,wall));
    }
    function barb(z){                            // barb on the +z side
      return Math.max(Math.max(z0-z,((Rin-P.barb)+bslope*(z-z0)-rho)/bnorm),
                      wall);
    }
    if(mode===0)return Math.min(deep(pz),deep(-pz));
    if(mode>0)return Math.min(deep(pz),barb(-pz));
    return Math.min(deep(-pz),barb(pz));
  }

  return{fSolid,fCut,fRim,Rin,Rout,rMid,cells,ribs,ring,H,
         nCells:cells.length,nRibs:ribs.length,
         teethCount:ring.length};
}

// ---------- surface nets mesher --------------------------------------------
// field: f(x,y,z) -> signed distance. Returns {positions:Float32Array,
// indices:Uint32Array, volume:mm^3(from voxel count), nVerts, nTris}
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
  // for each grid edge with a sign change, quad over the 4 adjacent cells
  for(let x=0;x<N-1;x++)for(let y=0;y<N-1;y++)for(let z=0;z<N-1;z++){
    const v0=nodes[nid(x,y,z)];
    // +x edge
    if(y>0&&z>0){
      const v1=nodes[nid(x+1,y,z)];
      if((v0<0)!==(v1<0)){
        const q=[makeVert(x,y,z),makeVert(x,y-1,z),makeVert(x,y-1,z-1),makeVert(x,y,z-1)];
        if(v0<0)q.reverse();
        indices.push(q[0],q[1],q[2],q[0],q[2],q[3]);
      }
    }
    // +y edge
    if(x>0&&z>0){
      const v1=nodes[nid(x,y+1,z)];
      if((v0<0)!==(v1<0)){
        const q=[makeVert(x,y,z),makeVert(x,y,z-1),makeVert(x-1,y,z-1),makeVert(x-1,y,z)];
        if(v0<0)q.reverse();
        indices.push(q[0],q[1],q[2],q[0],q[2],q[3]);
      }
    }
    // +z edge
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

// mesh signed volume from triangles (sanity check / readout refinement)
function meshField(f,H,N){
  return meshFromNodes(sampleField(f,H,N),H,N);
}

function meshVolume(positions,indices){
  let v=0;
  for(let i=0;i<indices.length;i+=3){
    const a=indices[i]*3,b=indices[i+1]*3,c=indices[i+2]*3;
    v+=(positions[a]*(positions[b+1]*positions[c+2]-positions[b+2]*positions[c+1])
       -positions[a+1]*(positions[b]*positions[c+2]-positions[b+2]*positions[c])
       +positions[a+2]*(positions[b]*positions[c+1]-positions[b+1]*positions[c]))/6;
  }
  return Math.abs(v);
}

if(typeof module!=="undefined")module.exports={
  goldbergNet,equatorSplit,connectorAxis,buildModel,meshField,meshVolume,
  sampleField,meshFromNodes,
  smin,smax,BANDS,icosa};
