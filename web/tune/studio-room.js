// Geometry and room lighting for the optional gallery. The painting material is supplied
// by studio.js so its brush-light calculation stays identical to the close-up preview.
import { createClockwork } from './studio-clockwork.js';
import { createStudioTextures } from './studio-textures.js';

export function createRoom(T, paintingMaterial) {
  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(36, 1, 0.1, 60);
  const geometries = new Set(), materials = new Set(), textures = new Set();
  const instances = new Set();
  const own = (set, item) => { set.add(item); return item; };
  const standard = options => own(materials, new T.MeshStandardMaterial(options));
  const mesh = (geometry, material, x, y, z, parent = scene) => {
    own(geometries, geometry);
    const object = new T.Mesh(geometry, material);
    object.position.set(x, y, z); parent.add(object);
    return object;
  };
  const box = (w, h, d, material, x, y, z, parent) =>
    mesh(new T.BoxGeometry(w, h, d), material, x, y, z, parent);
  // Small deterministic textures, generated locally: no room models or image downloads.
  function grain(wood) {
    const n = 128, bytes = new Uint8Array(n * n * 4);
    let seed = 7123;
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const noise = (seed >>> 24) / 255;
      const value = wood ? 215 + 16 * Math.sin(x * .7 + Math.sin(y * .08) * 1.2) + noise * 12
        : 120 + noise * 22;
      const i = (y * n + x) * 4;
      bytes[i] = bytes[i + 1] = bytes[i + 2] = value; bytes[i + 3] = 255;
    }
    const texture = own(textures, new T.DataTexture(bytes, n, n));
    texture.wrapS = texture.wrapT = T.RepeatWrapping;
    texture.magFilter = T.LinearFilter;
    texture.minFilter = T.LinearMipmapLinearFilter; texture.generateMipmaps = true;
    texture.repeat.set(wood ? 3 : 14, wood ? 1 : 8); texture.needsUpdate = true;
    return texture;
  }
  const plaster = grain(false), timber = grain(true);
  const surfaces = createStudioTextures(T,own,textures);
  const wall = standard({ color: 0xfff0d8, roughness: .95, ...surfaces.plaster,normalScale:new T.Vector2(.22,.22) });
  const floor = standard({ color: 0xf0e6d2, roughness: .45, ...surfaces.stone,normalScale:new T.Vector2(.25,.25) });
  const wood = standard({ color: 0x392319, roughness: .42, map: timber, bumpMap: timber, bumpScale: .003 });
  const brass = standard({ color: 0xbda06b, metalness: .72, roughness: .32 });
  const black = standard({ color: 0x171916, roughness: .45, metalness: .4 });
  const joinery = standard({ color: 0x71543b, roughness: .72, ...surfaces.wood,
    normalScale: new T.Vector2(.18, .18) });
  const canopy = standard({ color: 0x292b25, roughness: .9,
    map: timber, bumpMap: timber, bumpScale: .001 });
  const linen = standard({ color: 0xddd4bd, roughness: 1, bumpMap: plaster, bumpScale: .012 });
  const seam = standard({ color: 0x49453c, roughness: 1 });
  // The artwork hangs on a plaster pier between two glazed clockwork exhibits.
  const wallMesh = box(4.45, 8, .4, wall, 0, 3.9, -.23);
  wallMesh.receiveShadow = true;
  for (const x of [-2.21, 2.21]) box(.028, 5.65, .045, brass, x, 2.83, -.006);
  const floorMesh = box(64, .12, 72, floor, 0, -.08, 18); floorMesh.receiveShadow = true;
  const fp=floorMesh.geometry.attributes.position,fu=floorMesh.geometry.attributes.uv;
  for(let i=0;i<fp.count;i++)fu.setXY(i,fp.getX(i)/3,fp.getZ(i)/3);
  // Finish the edges of the exhibit, including the views revealed on wide screens.
  box(64, .18, 72, canopy, 0, 6.55, 18).receiveShadow = true;
  box(64, 6.6, .12, canopy, 0, 3.2, -2.48);
  box(21, .74, 2.7, canopy, 0, 6.09, -1.1);
  box(21, .022, .06, brass, 0, 5.73, .28);
  box(21, .12, .07, brass, 0, .04, -.01);
  for (const sign of [-1, 1]) {
    box(.16, 6.6, 42, canopy, sign * 10.5, 3.2, 18.5);
    box(3.55, 5.7, .16, joinery, sign * 8.725, 2.85, .015).receiveShadow = true;
    box(.025, 5.7, .03, brass, sign * 6.96, 2.85, .115);
  }
  function repeat(geometry, material, count, place) {
    own(geometries, geometry);
    const object = new T.InstancedMesh(geometry, material, count), transform = new T.Object3D();
    for (let i = 0; i < count; i++) {
      place(transform, i); transform.updateMatrix(); object.setMatrixAt(i, transform.matrix);
    }
    object.receiveShadow = true; instances.add(object); scene.add(object);
  }
  // Real narrow ribs catch grazing light; their dark recesses keep the perimeter quiet.
  repeat(new T.BoxGeometry(.045, .075, 40), joinery, 85, (o, i) => o.position.set(-10.08 + i * .24, 6.415, 17));
  repeat(new T.BoxGeometry(.044, 5.66, .055), joinery, 50, (o, i) =>
    o.position.set((i < 25 ? -1 : 1) * (7.07 + i % 25 * .14), 2.85, .123));
  for (const z of [.55, 3.8]) box(21, .015, .018, brass, 0, 6.367, z);
  const clockwork = createClockwork(T, scene, { mesh, box, standard, own, materials, textures, geometries, surfaces });
  const ambient = new T.HemisphereLight(0xfff4df, 0x5e5b53, 1.65);
  scene.add(ambient);
  const key = new T.SpotLight(0xffe7bc, 65, 18, .63, .88, 2);
  key.position.set(-2.1, 6.22, 1.7); key.target.position.set(-.25, 2.6, 0);
  key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.near = .15; key.shadow.camera.far = 14;
  key.shadow.bias = -.00015; key.shadow.normalBias = .015;
  key.shadow.radius = 4;
  scene.add(key, key.target);
  const fill = new T.SpotLight(0xfff0d9, 34, 16, .58, .95, 2);
  fill.position.set(2.3, 6.22, 1.55); fill.target.position.set(1, 2.4, 0);
  scene.add(fill, fill.target);
  const track = box(7, .045, .085, black, 0, 6.47, 1.55);
  const bulb = own(materials, new T.MeshBasicMaterial({ color: 0xffebc2 }));
  function fixture(light) {
    const head = new T.Group(); scene.add(head);
    const stem = box(.034, .17, .034, black, 0, 6.39, 0);
    const arm = box(.034, .034, 1, black, 0, 6.46, 0);
    mesh(new T.CylinderGeometry(.08, .09, .28, 20), black, 0, 0, 0, head).rotation.x = Math.PI / 2;
    mesh(new T.CircleGeometry(.068, 20), bulb, 0, 0, .143, head);
    return () => {
      head.position.copy(light.position); head.lookAt(light.target.position);
      stem.position.set(light.position.x, 6.39, light.position.z);
      arm.position.set(light.position.x, 6.46, (light.position.z + track.position.z) / 2);
      arm.scale.z = Math.max(.034, Math.abs(light.position.z - track.position.z));
    };
  }
  const aimKey = fixture(key), aimFill = fixture(fill);
  aimKey(); aimFill();
  const artwork = new T.Group(); scene.add(artwork);
  const frameGeometries = new Set();
  let paintingWidth = 3.8, paintingHeight = 2.85, center = 2.6;
  function ring(innerW, innerH, border, depth, z, material) {
    const w = innerW / 2, h = innerH / 2;
    const shape = new T.Shape();
    shape.moveTo(-w-border, -h-border); shape.lineTo(w+border, -h-border);
    shape.lineTo(w+border, h+border); shape.lineTo(-w-border, h+border); shape.closePath();
    const hole = new T.Path();
    hole.moveTo(-w, -h); hole.lineTo(-w, h); hole.lineTo(w, h); hole.lineTo(w, -h); hole.closePath();
    shape.holes.push(hole);
    const geometry = new T.ExtrudeGeometry(shape, { depth, steps: 1, bevelEnabled: true,
      bevelSegments: 2, bevelSize: Math.min(.012, border / 3), bevelThickness: .008, curveSegments: 1 });
    frameGeometries.add(geometry);
    const object = mesh(geometry, material, 0, center, z, artwork);
    object.castShadow = true; object.receiveShadow = true;
  }
  const target = new T.Vector3();
  const home = { yaw: -.10, pitch: .11, zoom: 1 };
  const pose = { ...home };
  const wanted = { ...pose };
  let distance = 8, width = 1, height = 1;
  function fit() {
    camera.aspect = width / height;
    // Include the clockwork cases and the foreground tea corner in the initial view.
    const roomWidth = T.MathUtils.lerp(6.2, 9.2, T.MathUtils.clamp((camera.aspect - .75) / .55, 0, 1));
    const vertical = Math.max(6.9, paintingHeight + 3.6, roomWidth / camera.aspect);
    distance = vertical / (2 * Math.tan(T.MathUtils.degToRad(camera.fov / 2)));
    camera.updateProjectionMatrix();
    target.set(.1, 2.40, .75);
  }
  function update(dt = 16, reduced = false, animate = false) {
    let moving = false;
    const ease = reduced ? 1 : 1 - Math.exp(-dt / 85);
    for (const key of Object.keys(pose)) {
      if (Math.abs(wanted[key] - pose[key]) > .00015) {
        pose[key] += (wanted[key] - pose[key]) * ease; moving = !reduced;
      } else pose[key] = wanted[key];
    }
    const d = distance * pose.zoom;
    // Tall viewports need more distance. Keep the eye inside the gallery even then,
    // instead of orbiting above the ceiling or around the ends of the display cases.
    camera.position.set(target.x + T.MathUtils.clamp(Math.sin(pose.yaw) * d, -3.1, 3.1),
      Math.min(5.1, target.y + Math.sin(pose.pitch) * d),
      target.z + Math.cos(pose.yaw) * Math.cos(pose.pitch) * d);
    camera.lookAt(target); camera.updateMatrixWorld();
    return clockwork.update(dt, animate && !reduced) || moving;
  }
  const clamp = T.MathUtils.clamp;
  return {
    scene, camera, update, ready: Promise.allSettled([clockwork.ready,surfaces.ready]).then(results=>{
      const failure=results.find(r=>r.status==='rejected');if(failure)throw failure.reason;
    }),
    setArtwork(aspect) {
      artwork.clear();
      for (const g of frameGeometries) { g.dispose(); geometries.delete(g); }
      frameGeometries.clear();
      paintingWidth = aspect >= 1 ? 3.8 : 3.5 * aspect;
      paintingHeight = paintingWidth / aspect;
      center = Math.max(2.6, paintingHeight / 2 + .85);
      const backing = box(paintingWidth + .23, paintingHeight + .23, .09, black, 0, center, .035, artwork);
      frameGeometries.add(backing.geometry); backing.castShadow = true;
      ring(paintingWidth + .10, paintingHeight + .10, .14, .15, .08, wood);
      ring(paintingWidth + .035, paintingHeight + .035, .035, .04, .16, brass);
      ring(paintingWidth, paintingHeight, .0175, .02, .17, linen);
      const paint = mesh(new T.PlaneGeometry(paintingWidth, paintingHeight), paintingMaterial, 0, center, .191, artwork);
      frameGeometries.add(paint.geometry);
      // A small physical label beneath the work, kept deliberately quiet.
      const label = box(.62, .105, .01, linen, -paintingWidth / 2 + .31, center - paintingHeight / 2 - .36, -.054, artwork);
      frameGeometries.add(label.geometry);
      for (let i = 0; i < 2; i++) {
        const line = box(i ? .24 : .38, .007, .004, seam, label.position.x - (i ? .07 : 0), label.position.y + .018 - i * .03, -.047, artwork);
        frameGeometries.add(line.geometry);
      }
      fit(); update(0, true);
    },
    resize(w, h) { width = Math.max(1, w); height = Math.max(1, h); fit(); update(0, false); },
    orbit(dx, dy) {
      wanted.yaw = clamp(wanted.yaw + dx, -.22, .22);
      wanted.pitch = clamp(wanted.pitch + dy, .075, .17);
    },
    zoom(delta) { wanted.zoom = clamp(wanted.zoom + delta, .90, 1.12); },
    reset(animate = true) {
      Object.assign(wanted, home);
      if (!animate) update(0, true);
    },
    enter(reduced) { if (!reduced) { pose.yaw = -.14; pose.zoom = 1.04; } },
    light(az, el) {
      const a = T.MathUtils.degToRad(az);
      // The ceiling head moves along its track; paint lighting itself uses the original
      // direction/elevation model so framing and room ambience never alter exported pixels.
      key.position.x = Math.cos(a) * 2.9;
      key.position.z = .9 + (85 - el) / 80 * 1.25;
      key.target.position.set(Math.cos(a) * .45, center + Math.sin(a) * .15, 0);
      aimKey();
    },
    frame(name) {
      const colors = { oak: 0xb08851, walnut: 0x493020, black: 0x242724 };
      wood.color.setHex(colors[name] ?? colors.walnut);
      wood.roughness = name === 'black' ? .38 : .48;
      brass.color.setHex(name === 'black' ? 0x958976 : 0xbda06b);
    },
    ambience(name) {
      const evening = name === 'evening';
      scene.background = new T.Color(evening ? 0x202a27 : 0x817a6b);
      wall.color.setHex(evening ? 0xb4ad96 : 0xfff0d8);
      canopy.color.setHex(evening ? 0x20221d : 0x292b25);
      floor.color.setHex(evening ? 0xb1ab9b : 0xf0e6d2);
      ambient.intensity = evening ? .025 : .10;
      scene.environmentIntensity = evening ? .10 : .22;
      key.intensity = evening ? 255 : 270; fill.intensity = evening ? 75 : 95;
      clockwork.ambience(evening);
    },
    inspect() {
      const corners = [[-1,-1],[-1,1],[1,-1],[1,1]].map(([x,y]) => {
        const p = new T.Vector3(x * (paintingWidth / 2 + .20), center + y * (paintingHeight / 2 + .20), .25).project(camera);
        return [p.x, p.y, p.z];
      });
      return { ...pose, corners, eye: camera.position.toArray(), ...clockwork.inspect() };
    },
    dispose() {
      clockwork.dispose();
      key.shadow.dispose(); fill.shadow.dispose();
      instances.forEach(object => object.dispose());
      geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
      textures.forEach(t => t.dispose()); scene.clear();
    },
  };
}
