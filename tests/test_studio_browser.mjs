// Real atelier, gallery, and GPU interaction checks, with no npm dependencies (Node 22+).
// Start serve_tune.py and a dedicated headless Chrome with remote debugging, then:
// node tests/test_studio_browser.mjs http://localhost:9231 http://localhost:8141
// Uses an isolated browser context and disposes it without touching existing tabs.
import assert from 'node:assert/strict';

const endpoint = process.argv[2] || 'http://localhost:9231';
const pageURL = process.argv[3] || 'http://localhost:8141';
const { webSocketDebuggerUrl } = await (await fetch(endpoint + '/json/version')).json();
const socket = new WebSocket(webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});
let sequence = 0;
const pending = new Map();
socket.addEventListener('message', e => {
  const reply = JSON.parse(e.data), job = pending.get(reply.id);
  if (!job) return;
  pending.delete(reply.id); clearTimeout(job.timer);
  if (reply.error) job.reject(new Error(JSON.stringify(reply.error)));
  else job.resolve(reply.result);
});
function call(method, params = {}, sessionId) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Timed out: ' + method)); }, 45000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });
}
const { browserContextId } = await call('Target.createBrowserContext');
try {
  const { targetId } = await call('Target.createTarget', { url: 'about:blank', browserContextId });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const page = (method, params) => call(method, params, sessionId);
  const evaluate = async expression => {
    const result = await page('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  const wait = async predicate => {
    const end = Date.now() + 30000;
    while (Date.now() < end) {
      try { if (await evaluate(`Boolean(${predicate})`)) return; }
      catch (e) { if (!/context|navigat/i.test(e.message)) throw e; }
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error('Page state timed out: ' + predicate);
  };
  const click = id => evaluate(`document.getElementById('${id}').click()`);
  const ok = (value, name) => { assert.ok(value, name); console.log('ok  ' + name); };
  await page('Page.enable');
  await page('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await page('Page.navigate', { url: pageURL });
  await wait(`document.querySelector('#studioBtn') && !document.querySelector('#studioBtn').disabled`);
  ok(await evaluate(`!performance.getEntriesByType('resource').some(r=>r.name.includes('/vendor/three.'))`), 'Three.js is lazy-loaded');
  await evaluate(`window.paletteControl=document.querySelector('#ctl-palette');document.querySelector('#tab-color').focus()`);
  await page('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowRight', code: 'ArrowRight' });
  ok(await evaluate(`document.activeElement.id==='tab-strokes' && !document.querySelector('#panel-strokes').hidden && document.querySelector('#panel-color').hidden`), 'arrow keys switch and focus inspector tabs');
  await page('Input.dispatchKeyEvent', { type: 'keyDown', key: 'End', code: 'End' });
  ok(await evaluate(`document.activeElement.id==='tab-light' && !document.querySelector('#panel-light').hidden`), 'End reaches the Light inspector');
  await click('tab-color');
  ok(await evaluate(`window.paletteControl===document.querySelector('#ctl-palette') && new Set([...document.querySelectorAll('[id]')].map(e=>e.id)).size===document.querySelectorAll('[id]').length`), 'tab changes keep the original controls without duplicate IDs');
  await evaluate(`document.querySelector('.inspector-scroll').scrollTop=200;window.colorScroll=document.querySelector('.inspector-scroll').scrollTop`);
  await click('tab-flow');
  ok(await evaluate(`document.querySelector('.inspector-scroll').scrollTop===0`), 'a new inspector opens at its main controls');
  await click('tab-color');
  ok(await evaluate(`document.querySelector('.inspector-scroll').scrollTop===window.colorScroll`), 'returning to an inspector restores its scroll position');
  for (const [width,height] of [[1440,1000],[1000,640],[768,1024],[390,844],[320,740],[844,390]]) {
    await page('Emulation.setDeviceMetricsOverride', { width,height,deviceScaleFactor:1,mobile:false });
    await evaluate(`new Promise(r=>setTimeout(r,500))`);
    await wait(`!document.querySelector('#studioBtn').disabled`);
    await click('tab-flow');
    await evaluate(`window.galleryPainting=document.querySelector('#out').src;window.galleryControl=document.querySelector('#ctl-flow').value`);
    ok(await evaluate(`document.documentElement.scrollWidth<=innerWidth`), `editor has no horizontal overflow at ${width} x ${height}`);
    await click('galleryBtn');
    await evaluate(`new Promise(r=>setTimeout(r,500))`);
    ok(await evaluate(`(()=>{
      const wipe=document.querySelector('#wipe'),r=wipe.getBoundingClientRect();
      return document.body.classList.contains('gallery') && getComputedStyle(document.querySelector('.rail')).display==='none'
        && getComputedStyle(document.querySelector('.toolrail')).display==='none'
        && getComputedStyle(document.querySelector('#top')).clipPath==='none'
        && wipe.getAttribute('role')==='img' && document.activeElement.id==='galleryExit'
        && r.width>100 && r.height>100 && r.left>=10 && r.right<=innerWidth-10 && r.top>=60 && r.bottom<=innerHeight-10;
    })()`), `gallery shows a complete framed painting at ${width} x ${height}`);
    await page('Input.dispatchKeyEvent', { type:'keyDown',key:'Escape',code:'Escape' });
    ok(await evaluate(`!document.body.classList.contains('gallery') && !document.querySelector('#panel-flow').hidden
      && document.activeElement.id==='galleryBtn' && document.querySelector('#out').src===window.galleryPainting
      && document.querySelector('#ctl-flow').value===window.galleryControl`), 'Escape returns to the same inspector and painting');
  }
  await page('Emulation.setDeviceMetricsOverride', { width:1440,height:1000,deviceScaleFactor:1,mobile:false });
  await evaluate(`new Promise(r=>setTimeout(r,500))`);
  await wait(`!document.querySelector('#studioBtn').disabled`);
  await click('rgnBtn');
  await evaluate(`document.querySelector('#rgnLayers [data-id="1"]').click();window.layerScope=document.querySelector('#rgnEditing').textContent`);
  await click('galleryBtn');
  await click('galleryExit');
  ok(await evaluate(`!document.querySelector('#rgnBar').hidden && document.querySelector('#rgnEditing').textContent===window.layerScope`), 'gallery preserves the active layer and painting tool');
  await click('toolNone');
  await evaluate(`window.beforeStudio=document.querySelector('#out').src`);
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  ok(await evaluate(`document.querySelector('#dl').disabled && document.querySelector('#galleryBtn').disabled && document.querySelector('#studioBtn').getAttribute('aria-pressed')==='true'`), 'preview has explicit mode and export guard');
  ok(await evaluate(`document.querySelector('#studioCanvas').dataset.presentation==='room' && !document.querySelector('#studioRoomOptions').hidden`), 'Studio opens as a 3D room');
  await click('studioMotion');
  ok(await evaluate(`!document.querySelector('#studioMotion').checked && document.querySelector('#out').src===window.beforeStudio`), 'clockwork can be paused without changing the painting');
  const expandPoint=await evaluate(`(()=>{const r=document.querySelector('#studioExpand').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  await page('Input.dispatchMouseEvent',{type:'mousePressed',...expandPoint,button:'left',buttons:1,clickCount:1});
  await page('Input.dispatchMouseEvent',{type:'mouseReleased',...expandPoint,button:'left',buttons:0,clickCount:1});
  await wait(`document.fullscreenElement===document.querySelector('#studioView')`);
  await evaluate(`new Promise(r=>setTimeout(r,250))`);
  ok(await evaluate(`document.querySelector('#studioCanvas').clientWidth===innerWidth&&document.querySelector('#studioExpand').getAttribute('aria-pressed')==='true'`),'fullscreen sizes the museum to the viewport');
  await click('studioDetail');
  ok(await evaluate(`!document.querySelector('#studioExpand').hidden&&document.fullscreenElement===document.querySelector('#studioView')`),'fullscreen exit remains available in Close-up');
  await click('studioExpand');
  await wait(`!document.fullscreenElement`);
  await click('studioRoom');
  ok(await evaluate(`document.querySelector('#out').src===window.beforeStudio&&!document.querySelector('#studioView').hidden`),'leaving fullscreen preserves the painting and Studio');
  await click('frameOak'); await click('roomEvening');
  ok(await evaluate(`document.querySelector('#frameOak').getAttribute('aria-pressed')==='true' && document.querySelector('#roomEvening').getAttribute('aria-pressed')==='true' && document.querySelector('#out').src===window.beforeStudio`), 'frame and room choices leave the painting unchanged');
  await click('studioDetail');
  ok(await evaluate(`document.querySelector('#studioCanvas').dataset.presentation==='detail' && document.querySelector('#studioRoomOptions').hidden && document.querySelector('#studioViewTools').hidden`), 'close-up exposes painting light without room controls');
  await click('studioRoom');
  await evaluate(`new Promise(r=>setTimeout(r,800))`);
  const sceneBox = await evaluate(`(()=>{const r=document.querySelector('#studioCanvas').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scale:1}})()`);
  const beforeOrbit = (await page('Page.captureScreenshot', { clip:sceneBox })).data;
  const cx=sceneBox.x+sceneBox.width*.5, cy=sceneBox.y+sceneBox.height*.5;
  await page('Input.dispatchMouseEvent', {type:'mousePressed',x:cx,y:cy,button:'left',buttons:1,clickCount:1});
  await page('Input.dispatchMouseEvent', {type:'mouseMoved',x:cx-100,y:cy+20,button:'left',buttons:1});
  await page('Input.dispatchMouseEvent', {type:'mouseReleased',x:cx-100,y:cy+20,button:'left',buttons:0,clickCount:1});
  await evaluate(`new Promise(r=>setTimeout(r,800))`);
  ok((await page('Page.captureScreenshot', {clip:sceneBox})).data!==beforeOrbit &&
    await evaluate(`document.querySelector('#studioAz').value==='135' && !document.querySelector('#studioCanvas').classList.contains('dragging')`), 'dragging orbits the room and releases the pointer without changing paint light');
  await evaluate(`document.querySelector('#studioCanvas').focus()`);
  await page('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowRight', code: 'ArrowRight' });
  ok(await evaluate(`Number(document.querySelector('#studioAz').value)===140`), 'keyboard moves the light');
  await click('studioClose');
  ok(await evaluate(`document.querySelector('#out').src===window.beforeStudio && !document.querySelector('#dl').disabled`), 'Cancel preserves the painting');
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  ok(await evaluate(`!document.querySelector('#studioMotion').checked`), 'motion preference survives reopening Studio');
  await click('lightRake');
  await click('studioApply');
  await wait(`!document.querySelector('#studioBtn').disabled && !document.querySelector('#dl').disabled`);
  ok(await evaluate(`document.querySelector('#studioView').hidden && document.querySelector('#ctl-light_deg').value==='180' && document.querySelector('#ctl-light_elev_deg').value==='15' && document.querySelector('#out').src!==window.beforeStudio`), 'Apply changes canonical pixels and controls');

  // Compare the actual shader against the existing CPU oracle, including the canvas
  // weave and cavity shading. A one-level difference is expected from byte textures.
  const parity = await evaluate(`(async()=>{
    const [{createStudio},{studioSurface},{finish,DEFAULTS}]=await Promise.all([
      import('./studio.js'),import('./oilpaint/studio-surface.js'),import('./oilpaint/pipeline.js')]);
    const w=64,h=48,n=w*h,out=new Float32Array(n*3),height=new Float32Array(n),cover=new Float32Array(n).fill(.7);
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){
      const p=y*w+x;height[p]=1+Math.sin(x*.42)*Math.cos(y*.3)*.8;
      out.set([.2+x/w*.5,.15+y/h*.5,.4],p*3);
    }
    let max=0;
    for(const linear of [false,true])for(const [az,el] of [[135,35],[180,15],[90,75]]){
      const cfg={...DEFAULTS,linear,occlusion:.7,canvas_weave:.25,impasto_depth:.7,light_deg:az,light_elev_deg:el};
      const data=studioSurface({out,height,cover,w,h},cfg);
      const canvas=document.createElement('canvas'),gpu=await createStudio(canvas,()=>{},()=>{});
      gpu.presentation('detail');gpu.resize(w,h);gpu.setSurface(data,cfg);
      const copy=document.createElement('canvas');copy.width=w;copy.height=h;
      const ctx=copy.getContext('2d');ctx.drawImage(canvas,0,0);const pixels=ctx.getImageData(0,0,w,h).data;
      const cpu=finish(out,cover,cover,{w,h},cfg,height);
      for(let p=0;p<n;p++)for(let c=0;c<3;c++)max=Math.max(max,Math.abs(pixels[p*4+c]-Math.round(cpu[p*3+c]*255)));
      if(pixels[3]!==255)throw new Error('GPU surface is blank');
      gpu.dispose();
    }
    return max;
  })()`);
  ok(parity <= 2, `GPU matches CPU lighting within two color levels (observed ${parity})`);

  // Exercise the real room renderer: projected frame, material changes, camera movement,
  // idle drawing, and repeated surface replacement. These cannot be checked by a DOM shim.
  await page('Emulation.setEmulatedMedia', { features: [{ name:'prefers-reduced-motion', value:'reduce' }] });
  const roomChecks = await evaluate(`(async()=>{
    const {createStudio}=await import('./studio.js');
    const {DEFAULTS}=await import('./oilpaint/pipeline.js');
    const w=64,h=48,color=new Uint8Array(w*h*4),surface=new Float32Array(w*h*4);
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){
      const p=(y*w+x)*4;color.set([70+x*2,50+y*3,150,255],p);surface[p+2]=1;
    }
    const data={w,h,color:color.buffer,surface:surface.buffer,ambient:.5};
    let lightEdits=0;
    const canvas=document.createElement('canvas'),gpu=await createStudio(canvas,()=>lightEdits++,()=>{});
    gpu.resize(900,650);gpu.setSurface(data,DEFAULTS);
    const sample=document.createElement('canvas');sample.width=160;sample.height=120;
    const ctx=sample.getContext('2d');
    const pixels=()=>{ctx.drawImage(canvas,0,0,160,120);const a=ctx.getImageData(0,0,160,120).data;let hash=0;for(let i=0;i<a.length;i++)hash=(Math.imul(hash,31)+a[i])|0;return hash;};
    const nextPixels=()=>new Promise(r=>requestAnimationFrame(()=>r(pixels())));
    const initial=pixels(),start=gpu.inspect();
    const initialPixels=ctx.getImageData(0,0,160,120).data;
    let black=0;for(let i=0;i<initialPixels.length;i+=4)if(initialPixels[i]+initialPixels[i+1]+initialPixels[i+2]<6)black++;
    gpu.ambience('evening');const dark=await nextPixels();
    gpu.frame('oak');const oak=await nextPixels();
    canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',shiftKey:true,cancelable:true}));
    const turned=await nextPixels(),afterTurn=gpu.inspect();
    gpu.zoom(-.1);await nextPixels();const near=gpu.inspect();
    gpu.resetView();await nextPixels();const reset=gpu.inspect();
    const fits=[];
    for(const [cw,ch,pw,ph] of [[900,650,64,48],[360,480,32,64],[1000,380,96,32]]){
      gpu.resize(cw,ch);gpu.setSurface({...data,w:pw,h:ph},DEFAULTS);
      fits.push(gpu.inspect().corners.every(([x,y])=>Math.abs(x)<.95&&Math.abs(y)<.95));
    }
    const viewpoints=[];
    for(const [cw,ch] of [[900,650],[360,900],[1800,500]])for(const sign of [-1,1]){
      gpu.resize(cw,ch);gpu.setSurface(data,DEFAULTS);gpu.resetView();
      for(let i=0;i<10;i++){
        canvas.dispatchEvent(new KeyboardEvent('keydown',{key:sign<0?'ArrowLeft':'ArrowRight',shiftKey:true,cancelable:true}));
        canvas.dispatchEvent(new KeyboardEvent('keydown',{key:sign<0?'ArrowDown':'ArrowUp',shiftKey:true,cancelable:true}));
      }
      gpu.zoom(10);await nextPixels();viewpoints.push(gpu.inspect());
      gpu.zoom(-20);await nextPixels();viewpoints.push(gpu.inspect());
    }
    const staysInside=viewpoints.every(p=>p.eye[1]<6.46&&Math.abs(p.eye[0])<10.42&&p.eye[2]>0);
    const artworkInView=viewpoints.every(p=>p.corners.every(([x,y,z])=>Math.abs(x)<.98&&Math.abs(y)<.98&&z>-1&&z<1));
    gpu.resetView();
    gpu.resize(900,650);gpu.setSurface(data,DEFAULTS);await nextPixels();
    const memory=gpu.inspect();
    for(let i=0;i<4;i++){gpu.setSurface(data,DEFAULTS);await nextPixels();}
    const memoryAfter=gpu.inspect();
    await new Promise(r=>setTimeout(r,150));const frame=gpu.inspect().frames;
    await new Promise(r=>setTimeout(r,150));const idle=gpu.inspect().frames===frame;
    const result={hasDepth:Math.max(...start.corners.map(p=>p[2]))-Math.min(...start.corners.map(p=>p[2]))>0.00001,
      materialChanges:initial!==dark&&dark!==oak,orbits:turned!==oak&&afterTurn.yaw!==start.yaw,
      cameraOnly:lightEdits===0,zooms:near.zoom<start.zoom,resets:Math.abs(reset.yaw-start.yaw)<.001&&reset.zoom===1,
      fits:fits.every(Boolean),staysInside,artworkInView,idle,reuses:memory.geometries===memoryAfter.geometries&&memory.textures===memoryAfter.textures,
      solidGears:start.gears.length===8&&start.gearLinks.length===6,reducedMotion:start.clockworkTime===0&&!start.motion,
      museum:start.museumEffects&&start.furnitureLoaded&&start.reflection,visibleWithHDR:black/(160*120)<.2};
    gpu.dispose();return result;
  })()`);
  for(const [key,value] of Object.entries(roomChecks)) ok(value, '3D room: ' + key);
  await page('Emulation.setEmulatedMedia', { features: [] });

  const motionChecks = await evaluate(`(async()=>{
    const {createStudio}=await import('./studio.js');
    const {DEFAULTS}=await import('./oilpaint/pipeline.js');
    const canvas=document.createElement('canvas'),gpu=await createStudio(canvas,()=>{},()=>{});
    const w=16,h=16,color=new Uint8Array(w*h*4).fill(160),surface=new Float32Array(w*h*4);
    for(let i=0;i<w*h;i++)surface[i*4+2]=1;
    gpu.resize(320,240);gpu.setSurface({w,h,color:color.buffer,surface:surface.buffer,ambient:.5},DEFAULTS);
    const delay=ms=>new Promise(r=>setTimeout(r,ms));
    await delay(300);const turning=gpu.inspect();
    gpu.motion(false);await delay(900);const paused=gpu.inspect();await delay(200);const still=gpu.inspect();
    gpu.motion(true);await delay(200);const resumed=gpu.inspect();
    gpu.presentation('detail');await delay(150);const detail=gpu.inspect();await delay(200);const detailEnd=gpu.inspect();
    gpu.presentation('room');await delay(200);const returned=gpu.inspect();
    const engaged=returned.gearLinks.every(([ai,bi])=>{
      const a=returned.gears[ai],b=returned.gears[bi],contact=Math.atan2(b.y-a.y,b.x-a.x);
      return Math.abs(Math.hypot(b.x-a.x,b.y-a.y)-a.radius-b.radius)<1e-9
        && Math.abs(Math.cos(a.teeth*(contact-a.angle)+b.teeth*(contact+Math.PI-b.angle))+1)<1e-9;
    });
    const coupled=returned.gearLinks.every(([ai,bi])=>{
      const a=returned.gears[ai],b=returned.gears[bi];
      const da=a.angle-paused.gears[ai].angle,db=b.angle-paused.gears[bi].angle;
      return da*db<0 && Math.abs(da*a.teeth+db*b.teeth)<1e-9;
    });
    const separated=returned.gears.every((a,ai)=>returned.gears.every((b,bi)=>{
      if(bi<=ai||returned.gearLinks.some(([x,y])=>(x===ai&&y===bi)||(x===bi&&y===ai)))return true;
      const tipA=a.radius*(1+2/a.teeth),tipB=b.radius*(1+2/b.teeth);
      return Math.hypot(b.x-a.x,b.y-a.y)>tipA+tipB;
    }));
    gpu.dispose();const disposed=gpu.inspect().clockworkTime;await delay(150);
    return {turns:turning.clockworkTime>0,engaged,coupled,separated,
      pauses:paused.clockworkTime===still.clockworkTime&&paused.frames===still.frames
        && paused.gears.every((g,i)=>g.angle===still.gears[i].angle),
      resumes:resumed.clockworkTime>still.clockworkTime,closeUpSleeps:detail.clockworkTime===detailEnd.clockworkTime&&detail.frames===detailEnd.frames,
      roomResumes:returned.clockworkTime>detailEnd.clockworkTime,disposalStopsMotion:gpu.inspect().clockworkTime===disposed};
  })()`);
  for(const [key,value] of Object.entries(motionChecks)) ok(value, 'Clockwork: ' + key);

  for (const [width, height] of [[1100,740],[1360,768],[390,844]]) {
    await page('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    await evaluate(`new Promise(r=>setTimeout(r,400))`);
    await wait(`!document.querySelector('#studioBtn').disabled`);
    await click('studioBtn');
    await wait(`!document.querySelector('#studioView').hidden`);
    await evaluate(`new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))`);
    ok(await evaluate(`(()=>{const c=document.querySelector('#studioCanvas').getBoundingClientRect(),s=document.querySelector('.stage').getBoundingClientRect();return document.documentElement.scrollWidth<=innerWidth && c.width>50 && c.height>50 && c.top>=s.top && c.bottom<=s.bottom})()`), `studio fits ${width} x ${height}`);
    await click('studioClose');
  }
  // Reduced motion must skip the brush animation entirely.
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  await click('lightRake');
  await page('Emulation.setDeviceMetricsOverride', { width: 844, height: 390, deviceScaleFactor: 1, mobile: false });
  await evaluate(`new Promise(r=>setTimeout(r,650))`);
  ok(await evaluate(`!document.querySelector('#studioView').hidden && document.querySelector('#studioAz').value==='180'`), 'rotation preserves uncommitted lighting');
  await click('studioClose');
  await page('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  ok(await evaluate(`(async()=>{const {createBrushReveal}=await import('./studio.js');const c=document.createElement('canvas');const reveal=createBrushReveal(c);await reveal.play(document.querySelector('#out').src,document.querySelector('#out').src);return c.hidden})()`), 'reduced motion skips brush reveal');
  await page('Emulation.setEmulatedMedia', { features: [] });
  ok(await evaluate(`(async()=>{const {createBrushReveal}=await import('./studio.js');const c=document.createElement('canvas');const reveal=createBrushReveal(c);await reveal.play(document.querySelector('#out').src,document.querySelector('#out').src);const started=!c.hidden;reveal.cancel();return started&&c.hidden})()`), 'brush reveal starts and can be cancelled');

  // A real context loss should release the mode and keep normal export available.
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  await evaluate(`document.querySelector('#studioCanvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()`);
  await wait(`document.querySelector('#studioView').hidden`);
  ok(await evaluate(`!document.querySelector('#dl').disabled && !document.querySelector('#studioNote').hidden`), 'WebGL loss falls back to the normal painter');
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  await click('fovBtn');
  ok(await evaluate(`document.querySelector('#studioView').hidden && !document.querySelector('#fovBar').hidden`), 'painting tools release Studio light');
  await click('toolNone');
  await click('auto');
  await evaluate(`const palette=document.querySelector('#ctl-palette');palette.value='zorn';palette.dispatchEvent(new Event('change',{bubbles:true}))`);
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  ok(await evaluate(`document.querySelector('#ctl-palette').value==='zorn' && document.querySelector('#out').src!==window.beforeStudio`), 'studio resolves pending manual settings before lighting');
  await click('studioClose');
  await click('ctl-impasto');
  await click('studioBtn');
  await wait(`!document.querySelector('#studioEnable').hidden`);
  await click('studioEnable');
  await wait(`!document.querySelector('#studioView').hidden`);
  ok(await evaluate(`document.querySelector('#ctl-impasto').checked`), 'flat paint offers a working Enable texture action');
  await click('studioClose');
  await click('auto');
  await wait(`!document.querySelector('#studioBtn').disabled`);
  await evaluate(`window.sawBrushReveal=false;window.revealObserver=new MutationObserver(()=>{if(!document.querySelector('#lookReveal').hidden)window.sawBrushReveal=true});window.revealObserver.observe(document.querySelector('#lookReveal'),{attributes:true,attributeFilter:['hidden']});document.querySelector('.lk[data-look="vangogh"]').click()`);
  await wait(`window.sawBrushReveal`);
  await wait(`document.querySelector('#lookReveal').hidden && !document.querySelector('#studioBtn').disabled`);
  await evaluate(`window.revealObserver.disconnect()`);
  ok(true, 'selecting a look runs a brush reveal and settles on the final render');
  ok(await evaluate(`document.querySelector('#err').textContent===''`), 'no page errors');
  // A missing bundled furniture asset should fail cleanly and allow another attempt.
  await page('Network.enable');
  await page('Network.setCacheDisabled', { cacheDisabled:true });
  await page('Network.setBlockedURLs', { urls:['*tea-furniture.glb'] });
  await click('studioBtn');
  await wait(`document.querySelector('#studioNote').textContent.includes('could not open')`);
  ok(await evaluate(`document.querySelector('#studioView').hidden&&!document.querySelector('#dl').disabled`), 'missing room asset keeps the painter usable');
  await page('Network.setBlockedURLs', { urls:[] });
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
  await click('studioClose');
  await page('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.studioNoWebglTest=true;
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...args){
      return type.includes('webgl') ? null : original.call(this,type,...args);
    };` });
  await page('Page.reload');
  await wait(`window.studioNoWebglTest && document.querySelector('#studioBtn') && !document.querySelector('#studioBtn').disabled`);
  await click('studioBtn');
  await wait(`!document.querySelector('#studioNote').hidden && document.querySelector('#studioNote').textContent.includes('could not open')`);
  ok(await evaluate(`document.querySelector('#studioView').hidden && !document.querySelector('#dl').disabled && document.querySelector('#err').textContent===''`), 'a browser without WebGL keeps the painter and PNG export');
  console.log('All studio browser checks passed.');
} finally {
  await call('Target.disposeBrowserContext', { browserContextId });
  socket.close();
}
