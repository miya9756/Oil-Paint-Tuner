// A slow mechanical exhibit behind glass. Furniture and paint lighting stay independent.
import { createFurniture } from './studio-furniture.js';

export function createClockwork(T, scene, { mesh, box, standard, own, materials, textures, geometries, surfaces }) {
  const grainSize = 256, grain = new Uint8Array(grainSize * grainSize * 4);
  let seed = 7521;
  for (let y = 0; y < grainSize; y++) for (let x = 0; x < grainSize; x++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const value = 217 + 6 * Math.sin(y * 1.7) + (seed >>> 24) * .09;
    const i = (y * grainSize + x) * 4;
    grain[i] = grain[i + 1] = grain[i + 2] = value; grain[i + 3] = 255;
  }
  const brushed = own(textures, new T.DataTexture(grain, grainSize, grainSize));
  brushed.wrapS = brushed.wrapT = T.RepeatWrapping;
  brushed.minFilter = T.LinearMipmapLinearFilter; brushed.magFilter = T.LinearFilter;
  brushed.generateMipmaps = true; brushed.anisotropy = 4; brushed.needsUpdate = true;
  const bronze = standard({ color: 0x796246, roughness: .34, metalness: .78 });
  const brass = standard({ color: 0xad874b, roughness: .44, metalness: .95,
    roughnessMap: brushed, bumpMap: brushed, bumpScale: .00012 });
  const paleBrass = standard({ color: 0xc2a16c, roughness: .37, metalness: .95,
    roughnessMap: brushed, bumpMap: brushed, bumpScale: .0001 });
  const steel = standard({ color: 0x464b49, roughness: .32, metalness: .86 });
  const dark = standard({ color: 0x272a26, roughness: .76, metalness: .18 });
  const backing = standard({ color: 0x33372f, roughness: .9, ...surfaces.plaster,
    normalScale: new T.Vector2(.12, .12) });
  const glow = own(materials, new T.MeshBasicMaterial({ color: 0xffdfab }));

  box(21, 6.5, .12, backing, 0, 3.15, -2.32).receiveShadow = true;
  box(21, .28, 2.35, dark, 0, .18, -1.1).receiveShadow = true;
  box(21, .30, .22, bronze, 0, .16, -.09);
  box(21, .07, .16, bronze, 0, 5.7, -.09);
  box(21, .018, .024, glow, 0, 5.59, -.02);
  box(21, .025, .06, bronze, 0, .35, .065);

  const glass = own(materials, new T.MeshPhysicalMaterial({ color: 0xe0dfcf,
    roughness: .09, metalness: .08, transparent: true, opacity: .055,
    depthWrite: false, side: T.DoubleSide, clearcoat: 1, envMapIntensity: .8 }));
  for (const sign of [-1, 1]) for (let i = 0; i < 3; i++) {
    const x = sign * (3.58 + i * 2.65);
    mesh(new T.PlaneGeometry(2.6, 5.2), glass, x, 2.97, .015).renderOrder = 3;
    box(.045, 5.39, .11, bronze, sign * (2.25 + i * 2.65), 2.99, .02);
    // Recessed vertical battens and a second depth plane make a display cabinet.
    box(.035, 5.2, .05, bronze, sign * (2.25 + i * 2.65), 2.97, -2.20);
  }

  const module = .048, pressureAngle = 20 * Math.PI / 180;
  const involute = radius => Math.sqrt(Math.max(0, radius * radius - 1)) - Math.acos(Math.min(1, 1 / radius));
  const polar = (path, radius, angle, move = false) =>
    path[move ? 'moveTo' : 'lineTo'](Math.cos(angle) * radius, Math.sin(angle) * radius);

  function wheelGeometry(teeth, spokes) {
    const pitch = teeth * module / 2, base = pitch * Math.cos(pressureAngle);
    const root = pitch - module * 1.25, tip = pitch + module;
    const halfPitch = Math.PI / (2 * teeth) - module * .035 / pitch;
    const halfAt = radius => halfPitch + involute(pitch / base) - involute(Math.max(1, radius / base));
    const shape = new T.Shape(), first = Math.max(base, root);
    for (let tooth = 0; tooth < teeth; tooth++) {
      const angle = tooth * Math.PI * 2 / teeth;
      polar(shape, root, angle - halfAt(first), tooth === 0);
      for (let j = 0; j <= 5; j++) {
        const radius = first + (tip - first) * j / 5;
        polar(shape, radius, angle - halfAt(radius));
      }
      for (let j = 1; j <= 3; j++) polar(shape, tip, angle + halfAt(tip) * (2 * j / 3 - 1));
      for (let j = 4; j >= 0; j--) {
        const radius = first + (tip - first) * j / 5;
        polar(shape, radius, angle + halfAt(radius));
      }
      polar(shape, root, angle + halfAt(first));
      const next = angle + Math.PI * 2 / teeth - halfAt(first);
      polar(shape, root, (angle + halfAt(first) + next) / 2);
    }
    shape.closePath();
    // Open sectors leave tapered spokes between a solid hub and the toothed rim.
    for (let i = 0; i < spokes; i++) {
      const start = i * Math.PI * 2 / spokes + .13;
      const end = (i + 1) * Math.PI * 2 / spokes - .13;
      const hole = new T.Path();
      polar(hole, pitch * .25, start, true);
      for (let j = 0; j <= 16; j++) polar(hole, pitch * .79, start + (end - start) * j / 16);
      for (let j = 16; j >= 0; j--) polar(hole, pitch * .25, start + (end - start) * j / 16);
      hole.closePath(); shape.holes.push(hole);
    }
    return new T.ExtrudeGeometry(shape, { depth: .15, steps: 1, bevelEnabled: true,
      bevelThickness: .006, bevelSize: .005, bevelSegments: 3, curveSegments: 8 });
  }
  function ring(radius, tube, material, x, y, z, parent) {
    return mesh(new T.TorusGeometry(radius, tube, 8, 96), material, x, y, z, parent);
  }
  function cylinder(radius, depth, material, x, y, z, parent) {
    const object = mesh(new T.CylinderGeometry(radius, radius, depth, 32), material, x, y, z, parent);
    object.rotation.x = Math.PI / 2; return object;
  }
  const wheels = [], links = [];
  function wheel(teeth, x, y, material, phase = 0, velocity = .018) {
    const group = new T.Group(); group.position.set(x, y, -1.10); scene.add(group);
    const radius = teeth * module / 2;
    const body = mesh(wheelGeometry(teeth, teeth >= 40 ? 6 : 5), material, 0, 0, 0, group);
    body.receiveShadow = true; body.castShadow = true;
    ring(radius * .845, .009, paleBrass, 0, 0, .157, group);
    cylinder(radius * .205, .16, bronze, 0, 0, .08, group);
    cylinder(radius * .12, .185, paleBrass, 0, 0, .12, group);
    cylinder(.035, .245, steel, 0, 0, .15, group);
    box(.046, .009, .006, dark, 0, 0, .275, group);
    for (let i = 0; i < 6; i++) {
      const a = i * Math.PI / 3;
      cylinder(.013, .012, steel, Math.cos(a) * radius * .16, Math.sin(a) * radius * .16, .218, group);
    }
    // Each wheel has a fixed axle and bearing mounted on the back of the case.
    cylinder(.052, 1.10, steel, x, y, -1.65);
    cylinder(.20, .09, bronze, x, y, -2.19);
    cylinder(.125, .14, steel, x, y, -2.12);
    for (const dx of [-.14, .14]) cylinder(.023, .015, paleBrass, x + dx, y, -2.135);
    group.rotation.z = phase;
    wheels.push({ group, teeth, radius, phase, velocity });
    return wheels.length - 1;
  }
  function engage(parent, teeth, angle, material) {
    const a = wheels[parent], radius = teeth * module / 2, distance = a.radius + radius;
    // At contact, a tooth meets a gap. Equal pitch speed gives opposite rotation
    // at a ratio determined by the tooth counts, including when motion is paused.
    const phase = angle + Math.PI - (Math.PI - a.teeth * (angle - a.phase)) / teeth;
    const child = wheel(teeth, a.group.position.x + Math.cos(angle) * distance,
      a.group.position.y + Math.sin(angle) * distance, material, phase, -a.velocity * a.teeth / teeth);
    links.push([parent, child]); return child;
  }

  const left = wheel(48, -3.64, 3.66, brass);
  const leftLow = engage(left, 24, -1.39, steel);
  engage(leftLow, 40, 3.40, brass);
  engage(left, 32, 2.89, bronze);
  const right = wheel(56, 3.98, 2.78, paleBrass, .025, -.015);
  const rightTop = engage(right, 20, 1.65, steel);
  engage(rightTop, 32, .12, brass);
  engage(right, 28, -.65, bronze);

  // Fine fixed graduations recall a horological instrument, without printed imagery.
  for (const index of [left, right]) {
    const { group, radius } = wheels[index], { x, y } = group.position;
    ring(radius + .17, .009, bronze, x, y, -2.20);
    ring(radius + .25, .004, bronze, x, y, -2.20);
    for (let i = 0; i < 60; i++) {
      const a = i * Math.PI / 30, r = radius + .21;
      const mark = box(.008, i % 5 ? .025 : .065, .008, bronze,
        x + Math.cos(a) * r, y + Math.sin(a) * r, -2.19);
      mark.rotation.z = a - Math.PI / 2;
    }
    box(.48, .085, .018, bronze, x, .53, -1.98);
    for (let i = 0; i < 2; i++) box(i ? .16 : .31, .005, .004, paleBrass, x - (i ? .075 : 0), .546 - i * .025, -1.968);
  }

  const caseLights = [];
  for (const x of [-4.2, 4.2]) {
    const light = new T.SpotLight(0xffe4b5, 120, 10, .76, 1, 2);
    light.position.set(x, 5.52, -.25); light.target.position.set(x, 2.8, -1.55);
    light.castShadow = true; light.shadow.mapSize.set(1024, 1024);
    light.shadow.camera.near = .2; light.shadow.camera.far = 8;
    light.shadow.bias = -.0002; light.shadow.normalBias = .008; light.shadow.radius = 3;
    scene.add(light, light.target); caseLights.push(light);
    box(1.3, .018, .04, glow, x, 5.53, -.3);
    const bounce = new T.PointLight(0xf8cc86, 5, 6, 2);
    bounce.position.set(x, .6, -.65); scene.add(bounce); caseLights.push(bounce);
  }
  const furniture = createFurniture(T, scene, { own, geometries, materials, surfaces });
  const loungeLight = new T.SpotLight(0xffe3bc, 125, 15, .85, 1, 2);
  loungeLight.position.set(-2, 5.7, 4.5); loungeLight.target.position.set(.8, .5, 2.5);
  loungeLight.castShadow = true; loungeLight.shadow.mapSize.set(2048, 2048);
  loungeLight.shadow.normalBias = .015; loungeLight.shadow.bias = -.0001; loungeLight.shadow.radius = 5;
  scene.add(loungeLight, loungeLight.target);

  let elapsed = 0;
  return {
    ready: furniture.ready,
    update(dt, animate) {
      if (animate) elapsed += dt / 1000;
      for (const w of wheels) w.group.rotation.z = w.phase + w.velocity * elapsed;
      return animate;
    },
    ambience(evening) {
      backing.color.setHex(evening ? 0x20271f : 0x33372f);
      caseLights.forEach((light, i) => { light.intensity = i % 2 ? (evening ? 7 : 5) : (evening ? 140 : 120); });
      loungeLight.intensity = evening ? 150 : 125; furniture.ambience(evening);
    },
    inspect() {
      return { gears: wheels.map(w => ({ teeth: w.teeth, radius: w.radius, angle: w.group.rotation.z,
        x: w.group.position.x, y: w.group.position.y })), gearLinks: links.map(pair => [...pair]),
        clockworkTime: elapsed, ...furniture.inspect() };
    },
    dispose() {
      furniture.dispose(); loungeLight.shadow.dispose();
      caseLights.forEach(light => light.shadow?.dispose());
    },
  };
}
