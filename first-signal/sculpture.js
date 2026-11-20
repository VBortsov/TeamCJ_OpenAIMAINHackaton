// A projected ring of cross-sections, drawn locally at the display's pixel density.
(() => {
  const canvas = document.querySelector('#waveArt');
  const wrap = canvas.parentElement;
  function draw() {
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) return;
    const d = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = w*d; canvas.height=h*d;
    const ctx=canvas.getContext('2d');
    ctx.scale(d,d);
    const size=Math.min(w/3.28,h/2.67);
    const rings=[];
    const pi=Math.PI;
    const project=(x,y,z)=>{
      const kx=.94*x-.34*y, ky=.34*x+.94*y;
      return [w*.495+size*(kx+.12*z), h*.49+size*(ky*.55-z*.90),ky*.90+z*.55];
    };
    for(let i=0;i<82;i++){
      const u=i*2*pi/82;
      const r=.92+.105*Math.cos(2*u-.48);
      const z0=.28*Math.sin(2*u+.8);
      const points=[];
      for(let j=0;j<=80;j++){
        const v=2*pi*j/80;
        const tube=.34+.115*Math.cos(u-.6);
        const radial=tube*Math.cos(v);
        const zz=tube*1.18*Math.sin(v);
        points.push(project((r+radial)*Math.cos(u)*1.19,(r+radial)*Math.sin(u),z0+zz));
      }
      rings.push({depth:project(r*Math.cos(u),r*Math.sin(u),z0)[2],points});
    }
    rings.sort((a,b)=>a.depth-b.depth);
    ctx.lineWidth=Math.max(.8,size/75);
    for (const {depth,points} of rings){
      ctx.strokeStyle='rgba(30,29,24,'+(.78+.10*depth)+')';
      ctx.beginPath();points.forEach(([x,y],i)=>i===0?ctx.moveTo(x,y):ctx.lineTo(x,y));ctx.closePath();ctx.stroke();
    }
  }
  new ResizeObserver(draw).observe(wrap);
  draw();
})();
