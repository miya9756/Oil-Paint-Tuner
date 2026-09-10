// Bundled 1K CC0 material scans; sources and checksums are in studio-assets/materials.json.
export function createStudioTextures(T, own, textures) {
  const jobs = [], sets = {};
  for (const name of ['wood','stone','plaster']) {
    const maps = {};
    for (const [suffix,slot] of [['color','map'],['normal','normalMap'],['rough','roughnessMap']]) {
      const texture = own(textures,new T.Texture());
      texture.wrapS=texture.wrapT=T.RepeatWrapping;
      texture.colorSpace=suffix==='color'?T.SRGBColorSpace:T.NoColorSpace;
      texture.anisotropy=4;
      maps[slot]=texture;
      jobs.push(new Promise((resolve,reject)=>{
        new T.ImageLoader().load(new URL(`./studio-assets/${name}-${suffix}.jpg`,import.meta.url).href,
          image=>{texture.image=image;texture.needsUpdate=true;resolve();},undefined,reject);
      }));
    }
    sets[name]=maps;
  }
  return {...sets,ready:Promise.allSettled(jobs).then(results=>{
    const failure=results.find(r=>r.status==='rejected');if(failure)throw failure.reason;
  })};
}
