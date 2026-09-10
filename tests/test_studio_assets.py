"""Validate shipped GLB geometry, including the normals required by HDR effects."""
import json
import hashlib
from pathlib import Path
import struct

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TYPES = {5126:"<f4",5125:"<u4",5123:"<u2",5121:"u1"}
WIDTHS = {"SCALAR":1,"VEC2":2,"VEC3":3,"VEC4":4}


def check(path):
    raw = path.read_bytes()
    magic, version, length = struct.unpack_from("<III",raw)
    assert magic == 0x46546C67 and version == 2 and length == len(raw), path.name
    cursor, doc, binary = 12, None, None
    while cursor < len(raw):
        size, kind = struct.unpack_from("<II",raw,cursor)
        block = raw[cursor+8:cursor+8+size]
        if kind == 0x4E4F534A: doc = json.loads(block)
        if kind == 0x004E4942: binary = block
        cursor += 8+size
    assert doc and binary

    def accessor(index):
        a = doc["accessors"][index]; view = doc["bufferViews"][a["bufferView"]]
        width = WIDTHS[a["type"]]; dtype = np.dtype(TYPES[a["componentType"]])
        offset = view.get("byteOffset",0)+a.get("byteOffset",0)
        stride = view.get("byteStride",width*dtype.itemsize)
        assert offset+(a["count"]-1)*stride+width*dtype.itemsize <= len(binary)
        return np.ndarray((a["count"],width),dtype,buffer=binary,offset=offset,strides=(stride,dtype.itemsize))

    triangles = 0
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            p = accessor(primitive["attributes"]["POSITION"])
            n = accessor(primitive["attributes"]["NORMAL"])
            indices = accessor(primitive["indices"]).ravel()
            assert len(p)==len(n) and np.isfinite(p).all() and np.isfinite(n).all()
            assert np.allclose(np.linalg.norm(n,axis=1),1,atol=1e-4), f"{mesh['name']}: invalid normals"
            assert len(indices)%3==0 and indices.max()<len(p)
            faces = indices.reshape(-1,3)
            area = np.linalg.norm(np.cross(p[faces[:,1]]-p[faces[:,0]],p[faces[:,2]]-p[faces[:,0]]),axis=1)
            assert np.all(area>0), f"{mesh['name']}: degenerate triangles"
            triangles += len(faces)
    print(f"ok  {path.name}: {triangles:,} triangles, finite positions, unit normals, valid indices")


if __name__ == "__main__":
    for filename in ("tea-furniture.glb","chandelier.glb"):
        check(ROOT/"web/tune/studio-assets"/filename)
    manifest=json.loads((ROOT/"web/tune/studio-assets/materials.json").read_text())
    for source in manifest.values():
        for filename,info in source["files"].items():
            raw=(ROOT/"web/tune/studio-assets"/filename).read_bytes()
            assert hashlib.md5(raw).hexdigest()==info["md5"],filename
    print("ok  scanned material maps match their source checksums")
