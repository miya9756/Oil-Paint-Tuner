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
  await page('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowRight', code: 'ArrowRight' });
  ok(await evaluate(`Number(document.querySelector('#studioAz').value)===140`), 'keyboard moves the light');
  await click('studioClose');
  ok(await evaluate(`document.querySelector('#out').src===window.beforeStudio && !document.querySelector('#dl').disabled`), 'Cancel preserves the painting');
  await click('studioBtn');
  await wait(`!document.querySelector('#studioView').hidden`);
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
      gpu.resize(w,h);gpu.setSurface(data,cfg);
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
