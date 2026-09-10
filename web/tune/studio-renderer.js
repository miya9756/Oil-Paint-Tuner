// Museum presentation: HDR lighting, contact occlusion, restrained bloom, and
// rough planar reflections. The pixel-accurate Close-up bypasses this entire chain.
import { EffectComposer } from './vendor/addons/postprocessing/EffectComposer.js';
import { RenderPass } from './vendor/addons/postprocessing/RenderPass.js';
import { GTAOPass } from './vendor/addons/postprocessing/GTAOPass.js';
import { UnrealBloomPass } from './vendor/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from './vendor/addons/postprocessing/OutputPass.js';
import { RoomEnvironment } from './vendor/addons/environments/RoomEnvironment.js';
import { Reflector } from './vendor/addons/objects/Reflector.js';

export function createMuseumRenderer(T, renderer, room) {
  const { scene, camera } = room;
  const environment = new RoomEnvironment();
  const pmrem = new T.PMREMGenerator(renderer);
  const envTarget = pmrem.fromScene(environment, .06);
  scene.environment = envTarget.texture;
  environment.dispose(); pmrem.dispose();
  const floorGeometry = new T.PlaneGeometry(64, 72);
  const floor = new Reflector(floorGeometry, { textureWidth: 640, textureHeight: 640, multisample: 0,
    color: 0x625b4e, clipBias: .003,
    shader: {
      uniforms: T.UniformsUtils.clone(Reflector.ReflectorShader.uniforms),
      vertexShader: Reflector.ReflectorShader.vertexShader,
      fragmentShader: `uniform vec3 color;uniform sampler2D tDiffuse;varying vec4 vUv;
        void main(){
          vec2 p=vUv.xy/vUv.w;vec3 c=texture2D(tDiffuse,p).rgb*.24;
          for(int i=0;i<8;i++){
            float a=float(i)*.785398;vec2 o=vec2(cos(a),sin(a))*.005;
            c+=texture2D(tDiffuse,p+o).rgb*.095;
          }
          gl_FragColor=vec4(c*.32+color*.14,.28);
          #include <tonemapping_fragment>
          #include <colorspace_fragment>
        }`,
    },
  });
  floor.rotation.x = -Math.PI / 2; floor.position.set(0, -.014, 18);
  floor.material.transparent = true; floor.material.depthWrite = false;
  floor.renderOrder = 2; scene.add(floor);
  // Override-material passes must see the real floor underneath, not render another mirror.
  const reflect = floor.onBeforeRender.bind(floor);
  floor.onBeforeRender = (...args) => { if (!scene.overrideMaterial) reflect(...args); };
  let composer = null, ao = null, bloom = null, output = null;
  if (renderer.extensions.has('EXT_color_buffer_float')) {
    const target = new T.WebGLRenderTarget(1, 1, { type:T.HalfFloatType, samples:4 });
    composer = new EffectComposer(renderer, target);
    composer.addPass(new RenderPass(scene,camera));
    ao = new GTAOPass(scene,camera,1,1);
    ao.updateGtaoMaterial({ radius:.35, thickness:.6, distanceExponent:1.5, distanceFallOff:1 });
    ao.updatePdMaterial({ radius:5, samples:12, rings:2 });
    ao.blendIntensity = .75;
    const originalRender = ao.render.bind(ao);
    ao.render = (...args) => {
      const hidden = [];
      scene.traverse(o => {
        if (o.isMesh && o.visible && (o.material.transparent || o.userData.skipAO)) {
          hidden.push(o); o.visible = false;
        }
      });
      try { originalRender(...args); } finally { hidden.forEach(o => { o.visible = true; }); }
    };
    composer.addPass(ao);
    bloom = new UnrealBloomPass(new T.Vector2(1,1), .16, .5, 1.4);
    composer.addPass(bloom);
    output = new OutputPass(); composer.addPass(output);
  }
  return {
    render() {
      renderer.toneMapping = T.AgXToneMapping;
      renderer.toneMappingExposure = 1.15;
      if (composer) composer.render(); else renderer.render(scene,camera);
    },
    resize(w,h,pixelRatio) {
      if (composer) {
        composer.setPixelRatio(pixelRatio); composer.setSize(w,h);
        // AO is denoised and needs fewer pixels than the painting itself.
        ao.setSize(Math.max(1,Math.round(w*pixelRatio*.65)),Math.max(1,Math.round(h*pixelRatio*.65)));
      }
    },
    inspect() { return { museumEffects:!!composer, reflection:true }; },
    dispose() {
      floor.dispose(); floorGeometry.dispose(); scene.remove(floor);
      ao?.dispose(); ao?.gtaoMaterial.dispose(); ao?.blendMaterial.dispose();
      bloom?.dispose(); output?.dispose(); composer?.dispose();
      envTarget.dispose(); scene.environment = null;
    },
  };
}
