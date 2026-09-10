import { GLTFLoader } from './vendor/addons/loaders/GLTFLoader.js';

// Repaired copies of the supplied Cinema 4D models; the source OBJs are untouched.
export function createFurniture(T, scene, { own, geometries, materials, surfaces }) {
  const physical = options => own(materials,new T.MeshPhysicalMaterial(options));
  const palette = {
    walnut:physical({color:0xdbc19a,roughness:.6,clearcoat:.25,clearcoatRoughness:.3,...surfaces.wood,normalScale:new T.Vector2(.35,.35)}),
    upholstery:physical({color:0xa19476,roughness:.9,sheen:1,sheenRoughness:.7,sheenColor:0xd6c6a2}),
    marble:physical({color:0xd0c7ad,roughness:.24,clearcoat:.4,clearcoatRoughness:.15}),
    brass:physical({color:0xad9160,metalness:.85,roughness:.27}),
    porcelain:physical({color:0xe8e3d4,roughness:.16,clearcoat:.55,clearcoatRoughness:.12}),
    crystal:physical({color:0xdfdfcf,metalness:.15,roughness:.07,clearcoat:1,transparent:true,opacity:.48,depthWrite:false}),
    pearl:physical({color:0xffe6b0,roughness:.22,emissive:0xffd197,emissiveIntensity:.4}),
  };
  const root = new T.Group(); scene.add(root);
  const chair = new T.Group(), table = new T.Group(), chandelier = new T.Group();
  root.add(chair,table,chandelier);
  chair.position.set(2.05,0,2.45); chair.rotation.y=-.24;
  table.position.set(-.25,0,2.95); table.rotation.y=.12;
  chandelier.position.set(2.5,3.75,2.7);
  const chainGeometry=own(geometries,new T.TorusGeometry(.024,.004,6,12));
  for(let y=5.16,i=0;y<6.47;y+=.04,i++){
    const link=new T.Mesh(chainGeometry,palette.brass);link.position.set(2.5,y,2.7);
    link.rotation.y=i%2?Math.PI/2:0;root.add(link);
  }
  const loader = new GLTFLoader();
  let loaded = false, disposed = false;
  const adopt = (gltf,isLight) => {
    const replaced = new Set(), imported = [];
    gltf.scene.traverse(object => {
      if (!object.isMesh) return;
      imported.push(object);
      own(geometries,object.geometry); replaced.add(object.material);
      const name = object.name;
      const role = Object.keys(palette).find(key=>name.includes(key)) || 'walnut';
      object.material = palette[role];
      object.castShadow = !isLight; object.receiveShadow = true;
      if (!object.geometry.attributes.uv) {
        const p = object.geometry.attributes.position, n = object.geometry.attributes.normal;
        const uv = new Float32Array(p.count*2);
        for (let i=0;i<p.count;i++) {
          uv[i*2] = (Math.abs(n.getX(i))>.7?p.getZ(i):p.getX(i))*1.6;
          uv[i*2+1] = (Math.abs(n.getY(i))>.7?p.getZ(i):p.getY(i))*.75;
        }
        object.geometry.setAttribute('uv',new T.BufferAttribute(uv,2));
      }
    });
    replaced.forEach(m=>m.dispose());
    // Objects have no parent transforms in the prepared files.
    for (const object of imported) {
      (isLight?chandelier:object.name.includes('chair')?chair:table).add(object);
    }
  };
  const ready = Promise.allSettled([
    loader.loadAsync(new URL('./studio-assets/tea-furniture.glb',import.meta.url).href),
    loader.loadAsync(new URL('./studio-assets/chandelier.glb',import.meta.url).href),
  ]).then(results=>{
    // Adopt every successful load before reporting a failure, so Studio can release
    // all resources even if only one of the two downloads failed.
    results.forEach((result,i)=>{ if(result.status==='fulfilled')adopt(result.value,i===1); });
    if (disposed) root.clear();
    const failure = results.find(r=>r.status==='rejected');
    if(failure)throw failure.reason;
    loaded=true;
  });
  const lamp = new T.PointLight(0xffdcb4,22,10,2); lamp.position.set(2.5,4.15,2.7); root.add(lamp);
  return {
    ready,
    ambience(evening) { lamp.intensity=evening?30:18;palette.pearl.emissiveIntensity=evening?.8:.4; },
    inspect() { return { furnitureLoaded:loaded }; },
    dispose() { disposed=true;scene.remove(root); },
  };
}
