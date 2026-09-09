// Optional presentation tools. Three.js is requested only by createStudio(), so
// the ordinary painter works even when WebGL is unavailable.
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const direction = (az, el) => {
  const a = az * Math.PI / 180, e = el * Math.PI / 180;
  return [Math.cos(e) * Math.cos(a), -Math.cos(e) * Math.sin(a), Math.sin(e)];
};

export async function createStudio(canvas, onMove, onLost) {
  const THREE = await import('./vendor/three.module.min.js');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const uniforms = {
    colorMap: { value: null }, surfaceMap: { value: null },
    lightDir: { value: new THREE.Vector3() }, viewDir: { value: new THREE.Vector3() },
    gloss: { value: 0.3 }, ambient: { value: 0.5 },
  };
  // This is render.light's Phong expression, operating on its real surface normals
  // and pre-light sRGB colors. No additional tone mapping or color-space conversion.
  const material = new THREE.ShaderMaterial({
    uniforms, depthTest: false, depthWrite: false, toneMapped: false,
    vertexShader: `varying vec2 vUv;
      void main(){ vUv=vec2(uv.x,1.0-uv.y); gl_Position=vec4(position.xy,0.0,1.0); }`,
    fragmentShader: `precision highp float;
      varying vec2 vUv;
      uniform sampler2D colorMap, surfaceMap;
      uniform vec3 lightDir, viewDir;
      uniform float gloss, ambient;
      void main(){
        vec4 surface=texture2D(surfaceMap,vUv);
        vec3 normal=surface.xyz;
        float ndl=max(dot(normal,lightDir),0.0);
        vec3 reflection=2.0*ndl*normal-lightDir;
        float specular=0.5*gloss*pow(max(dot(reflection,viewDir),0.0),8.0+56.0*gloss);
        float shade=ambient*(1.0-surface.a)+(1.0-ambient)*ndl/max(lightDir.z,0.000001);
        vec3 rgb=texture2D(colorMap,vUv).rgb*shade+specular;
        gl_FragColor=vec4(clamp(rgb,0.0,1.0),1.0);
      }`,
  });
  const geometry = new THREE.PlaneGeometry(2, 2);
  const scene = new THREE.Scene();
  scene.add(new THREE.Mesh(geometry, material));
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  let frame = 0, dead = false, ready = false, azimuth = 135, elevation = 35;
  const draw = () => {
    if (dead || frame || !ready) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (!dead) renderer.render(scene, camera);
    });
  };
  const textures = [];
  function disposeTextures() { textures.splice(0).forEach(t => t.dispose()); }
  function light(az, el) {
    azimuth = az; elevation = el;
    uniforms.lightDir.value.set(...direction(az, el));
    draw();
  }
  function fromPointer(e) {
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width * 2 - 1;
    const y = (e.clientY - rect.top) / rect.height * 2 - 1;
    const az = (Math.atan2(-y, x) * 180 / Math.PI + 360) % 360;
    const el = 85 - clamp(Math.hypot(x, y), 0, 1) * 80;
    onMove(Math.round(az), Math.round(el));
  }
  const down = e => {
    if (!ready || e.button !== 0) return;
    canvas.focus({ preventScroll: true });
    canvas.setPointerCapture(e.pointerId); fromPointer(e); e.preventDefault();
  };
  const move = e => { if (canvas.hasPointerCapture(e.pointerId)) fromPointer(e); };
  const up = e => { if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId); };
  const key = e => {
    let az = azimuth, el = elevation;
    if (e.key === 'ArrowLeft') az = (az + 355) % 360;
    else if (e.key === 'ArrowRight') az = (az + 5) % 360;
    else if (e.key === 'ArrowUp') el = clamp(el + 2, 5, 85);
    else if (e.key === 'ArrowDown') el = clamp(el - 2, 5, 85);
    else return;
    e.preventDefault(); onMove(az, el);
  };
  const lost = e => { e.preventDefault(); onLost(); };
  const events = { pointerdown: down, pointermove: move, pointerup: up,
    pointercancel: up, keydown: key, webglcontextlost: lost };
  Object.entries(events).forEach(([name, fn]) => canvas.addEventListener(name, fn));
  return {
    light,
    setSurface(data, cfg) {
      disposeTextures();
      const color = new THREE.DataTexture(new Uint8Array(data.color), data.w, data.h);
      const surface = new THREE.DataTexture(new Float32Array(data.surface), data.w, data.h,
        THREE.RGBAFormat, THREE.FloatType);
      color.minFilter = color.magFilter = THREE.LinearFilter;
      if (renderer.extensions.has('OES_texture_float_linear')) {
        surface.minFilter = surface.magFilter = THREE.LinearFilter;
      }
      for (const tex of [color, surface]) { tex.needsUpdate = true; textures.push(tex); }
      uniforms.colorMap.value = color; uniforms.surfaceMap.value = surface;
      uniforms.ambient.value = data.ambient;
      uniforms.gloss.value = cfg.gloss;
      uniforms.viewDir.value.set(...direction(cfg.view_deg, cfg.view_elev_deg));
      ready = true;
      light(cfg.light_deg, cfg.light_elev_deg);
      // Compile now so a GPU/compiler failure returns to the normal image immediately.
      renderer.debug.onShaderError = () => { throw new Error('Lighting shader unavailable'); };
      renderer.render(scene, camera);
    },
    resize(width, height) {
      renderer.setSize(Math.max(1, width), Math.max(1, height), false); draw();
    },
    dispose() {
      dead = true; cancelAnimationFrame(frame);
      Object.entries(events).forEach(([name, fn]) => canvas.removeEventListener(name, fn));
      disposeTextures(); geometry.dispose(); material.dispose(); renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}

// A short, deterministic bristle reveal, confined to the painting layer. It is
// cancelled on a new render and never changes the image used by PNG export.
export function createBrushReveal(canvas) {
  let serial = 0, frame = 0;
  const cancel = () => { serial++; globalThis.cancelAnimationFrame?.(frame); canvas.hidden = true; };
  return {
    cancel,
    async play(previous, current) {
      cancel();
      if (!previous || typeof requestAnimationFrame !== 'function' ||
          window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
      const mine = serial;
      const old = new Image(), next = new Image();
      old.src = previous; next.src = current;
      try { await Promise.all([old.decode(), next.decode()]); } catch { return; }
      if (mine !== serial) return;
      const scale = Math.min(1, 1400 / Math.max(next.naturalWidth, next.naturalHeight));
      canvas.width = Math.max(1, Math.round(next.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(next.naturalHeight * scale));
      const ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
      let start;
      canvas.hidden = false;
      function tick(now) {
        if (mine !== serial) return;
        if (reduced.matches) { cancel(); return; }
        start ??= now;
        const t = Math.min(1, (now - start) / 440);
        const progress = 1 - (1 - t) ** 3;
        ctx.drawImage(old, 0, 0, w, h);
        ctx.save(); ctx.beginPath();
        const strip = Math.max(2, h / 150);
        for (let y = 0; y < h; y += strip) {
          const bristle = Math.sin(y * 1.91) * 0.025 + Math.sin(y * 0.17) * 0.055;
          const edge = clamp(progress * 1.22 - 0.11 + bristle, 0, 1) * w;
          ctx.rect(0, y, edge, strip + 1);
        }
        ctx.clip(); ctx.drawImage(next, 0, 0, w, h); ctx.restore();
        if (t < 1) frame = requestAnimationFrame(tick); else canvas.hidden = true;
      }
      frame = requestAnimationFrame(tick);
    },
  };
}
