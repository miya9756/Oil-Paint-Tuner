// Painted fish and water surround the repaired furniture, without changing the painting.
import { createFurniture } from './studio-furniture.js';
export function createAquarium(T, scene, { mesh, box, standard, own, materials, textures, geometries, surfaces }) {
  const bronze = standard({ color: 0x766043, roughness: .36, metalness: .65 });
  const stone = standard({ color: 0x5e6958, roughness: .94 });
  const basic = options => own(materials, new T.MeshBasicMaterial(options));
  const glow = basic({ color: 0xf4ca85 });
  const waterTime = { value: 0 }, dusk = { value: 0 };
  const water = own(materials, new T.ShaderMaterial({
    uniforms: { time: waterTime, dusk },
    vertexShader: `varying vec2 vUv;
      void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`,
    fragmentShader: `varying vec2 vUv; uniform float time,dusk;
      void main(){
        vec2 p=vUv;
        vec3 c=mix(vec3(.002,.009,.010),vec3(.010,.042,.031),pow(p.y,.8));
        float rays=pow(.5+.5*sin(p.x*59.+p.y*5.+sin(p.x*13.+time*.09)),14.);
        float fine=pow(.5+.5*sin(p.x*113.-p.y*8.+time*.04),24.);
        c+=vec3(.035,.09,.065)*(rays*.32+fine*.09)*pow(p.y,1.6);
        c*=1.-.38*dusk;
        gl_FragColor=vec4(c,1.);
        #include <colorspace_fragment>
      }`,
  }));
  mesh(new T.PlaneGeometry(21, 6.5), water, 0, 3.15, -3.35);
  box(21, .13, 3.1, stone, 0, .25, -1.7).receiveShadow = true;
  box(21, .30, .38, bronze, 0, .16, -.14);
  box(21, .055, .14, bronze, 0, 5.7, -.12);
  box(21, .032, .04, glow, 0, 5.58, -.01);
  box(21, .025, .06, bronze, 0, .35, .065);

  // Three panes on either side of the painting pier. A restrained reflection makes
  // the glazing legible without a costly screen-space transmission pass.
  const glass = own(materials,new T.MeshPhysicalMaterial({color:0xa7c7bb,roughness:.08,metalness:.1,
    transparent:true,opacity:.09,depthWrite:false,side:T.DoubleSide,clearcoat:1,envMapIntensity:1.3}));
  for (const sign of [-1, 1]) for (let i = 0; i < 3; i++) {
    const x = sign * (3.58 + i * 2.65);
    mesh(new T.PlaneGeometry(2.6, 5.2), glass, x, 2.97, .015).renderOrder = 3;
    box(.055, 5.39, .11, bronze, sign * (2.25 + i * 2.65), 2.99, .02);
    box(.008, 5.2, .012, bronze, sign * (2.29 + i * 2.65), 2.97, .08);
  }

  let seed = 93;
  const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  const dummy = new T.Object3D();
  const rockGeometry = own(geometries, new T.IcosahedronGeometry(1, 3));
  const rocks = new T.InstancedMesh(rockGeometry, stone, 68); scene.add(rocks);
  for (let i = 0; i < 68; i++) {
    dummy.position.set((random() - .5) * 20, .38, -1 - random() * 2);
    dummy.rotation.set(random(), random() * 6, random());
    dummy.scale.set(.12 + random() * .35, .08 + random() * .19, .12 + random() * .22);
    dummy.updateMatrix(); rocks.setMatrixAt(i, dummy.matrix);
    rocks.setColorAt(i, new T.Color().setHSL(.16 + random() * .08, .14, .18 + random() * .12));
  }
  // Slender curved aquatic ribbons form a dark, layered planting bed.
  const foliage = standard({ color: 0x254f3c, side: T.DoubleSide, roughness: .72 });
  const leafGeometry = own(geometries,new T.PlaneGeometry(1,1,3,18));
  const leafVertices = leafGeometry.attributes.position;
  for (let i=0;i<leafVertices.count;i++) {
    const v=leafVertices.getY(i)+.5,u=leafVertices.getX(i);
    leafVertices.setXYZ(i,u*Math.pow(Math.sin(v*Math.PI),.65)*.13+v*v*.13,v*1.5,Math.sin(v*3.3)*.18+u*u*.09);
  }
  leafGeometry.computeVertexNormals();
  const leaves = new T.InstancedMesh(leafGeometry, foliage, 360); scene.add(leaves);
  for (let i = 0; i < 360; i++) {
    const cluster = Math.floor(i / 45), sign = cluster < 4 ? -1 : 1;
    const x = sign * (2.9 + (cluster % 4) * 1.55);
    dummy.position.set(x + (random() - .5) * 1.5, .34 + random() * .12, -1.2 - random()*1.7);
    dummy.rotation.set(-.1 + random() * .4, (random() - .5) * 1.3, (random() - .5) * 1.2);
    dummy.scale.set(.7 + random() * .8, .35 + random() * 1.05, 1);
    dummy.updateMatrix(); leaves.setMatrixAt(i, dummy.matrix);
    leaves.setColorAt(i, new T.Color().setHSL(.27 + random() * .07, .3, .23 + random() * .20));
  }

  const atlas = own(textures, new T.Texture());
  atlas.colorSpace = T.SRGBColorSpace;
  const fish = [], fishGeometry = own(geometries, new T.PlaneGeometry(1, 1, 16, 3));
  // Await decode before the first room render; rejection is handled by Studio's fallback.
  const ready = new Promise((resolve, reject) => {
    const loader = new T.ImageLoader();
    loader.load(new URL('./aquarium-fish.png', import.meta.url).href, img => {
      atlas.image = img; atlas.needsUpdate = true; resolve();
    }, undefined, reject);
  });
  const fishMaterial = kind => own(materials, new T.ShaderMaterial({
    uniforms: { map: { value: atlas }, tile: { value: kind }, time: waterTime },
    transparent: true, depthWrite: false, side: T.DoubleSide,
    vertexShader: `varying vec2 vUv;varying float vDepth;uniform float time;
      void main(){vUv=uv;vec3 p=position;
        p.z+=sin(uv.x*8.-time*1.4)*pow(1.-uv.x,2.)*.075;
        vDepth=-(modelMatrix*vec4(p,1.)).z;
        gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.);}`,
    fragmentShader: `varying vec2 vUv;varying float vDepth;uniform sampler2D map;uniform float tile;
      void main(){vec4 c=texture2D(map,vec2((vUv.x+tile)/3.,vUv.y));
        if(c.a<.025)discard;
        gl_FragColor=vec4(mix(c.rgb*.87,vec3(.012,.065,.055),clamp(vDepth*.15,0.,.42)),c.a);
        #include <colorspace_fragment>
      }`,
  }));
  const fishMaterials = [0, 1, 2].map(fishMaterial);
  const shoal = [
    [-3.25,3.95,-1,1.2,0,1],[-4.7,2.85,-.8,1.25,2,-1],[-3.2,2.15,-1.7,.8,1,1],
    [3.45,3.9,-1.5,1.1,2,-1],[4.1,2.7,-.7,1.45,1,1],[3.2,1.7,-2,.9,0,-1],
    [-6.2,4.5,-1.5,.95,1,1],[6.1,4.7,-2,.9,0,-1],[-7.5,2.2,-1,1.2,2,1],
    [7.6,3.1,-1.4,1.15,1,-1],[-5.7,1.8,-2.4,.7,0,1],[5.65,1.8,-2.3,.75,2,1],
  ];
  for (const [x,y,z,size,kind,direction] of shoal) {
    const object = mesh(fishGeometry, fishMaterials[kind], x, y, z);
    object.scale.set(direction * size*.86, size * 280 / 512*.86, 1);
    object.renderOrder = 1;
    fish.push({ object, x, y: object.position.y, phase: random() * 6.28, speed: .085 + random() * .08, direction });
  }

  const furniture = createFurniture(T,scene,{own,geometries,materials,surfaces});
  const loungeLight = new T.SpotLight(0xffe3bc,125,15,.85,1,2);
  loungeLight.position.set(-2,5.7,4.5);loungeLight.target.position.set(.8,.5,2.5);
  loungeLight.castShadow=true;loungeLight.shadow.mapSize.set(2048,2048);
  loungeLight.shadow.normalBias=.015;loungeLight.shadow.bias=-.0001;
  loungeLight.shadow.radius=5;
  scene.add(loungeLight,loungeLight.target);
  const waterBounce = new T.PointLight(0x7baea0, 10, 9, 2);
  waterBounce.position.set(-3.5,2.5,-.2); scene.add(waterBounce);

  let elapsed = 0;
  return {
    ready: Promise.allSettled([ready,furniture.ready]).then(results=>{
      const failure=results.find(r=>r.status==='rejected');if(failure)throw failure.reason;
    }),
    update(dt, animate, camera) {
      if (animate) elapsed += dt / 1000;
      waterTime.value = elapsed;
      for (const f of fish) {
        // Each fish loops beyond the outer glass, never reversing or teleporting in view.
        const x = ((f.x + f.direction * elapsed * f.speed + 10.5) % 21 + 21) % 21 - 10.5;
        f.object.position.x = x;
        f.object.position.y = f.y + Math.sin(elapsed * .27 + f.phase) * .10;
        f.object.rotation.y = camera.rotation.y;
        f.object.rotation.z = Math.sin(elapsed * .3 + f.phase) * .025;
      }
      return animate;
    },
    ambience(evening) {
      dusk.value = evening ? 1 : 0;
      loungeLight.intensity = evening ? 150 : 125; furniture.ambience(evening);
      waterBounce.intensity = evening ? 15 : 10;
    },
    inspect() { return { fish: fish.length, aquariumTime: elapsed, fishLoaded: !!atlas.image?.naturalWidth, ...furniture.inspect() }; },
    dispose() { furniture.dispose(); rocks.dispose(); leaves.dispose(); loungeLight.shadow.dispose(); },
  };
}
