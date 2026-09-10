"""Repair the supplied Cinema 4D OBJs and produce the museum's portable GLBs.

Offline tools: numpy, trimesh==4.8.3, mapbox_earcut==1.0.3, networkx==2.8.8.
python scripts/prepare_studio_assets.py E:/paint/old_3d_assets
The originals are read only. Full repaired copies and a repair report go to
.cache/museum-review/repaired; only the selected furniture ships with the tuner.
Missing MTL files are replaced by named material slots, styled in studio-furniture.js.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".cache/museum-review/python"))
import mapbox_earcut
import numpy as np
import trimesh


def read_obj(path):
    vertices, objects = [], []
    obj = None
    for line in path.open(encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        if t[0] == "v":
            vertices.append([float(x) for x in t[1:4]])
        elif t[0] == "o":
            obj = {"name": " ".join(t[1:]), "faces": []}
            objects.append(obj)
        elif t[0] == "f":
            if obj is None:
                obj = {"name": "mesh", "faces": []}
                objects.append(obj)
            face = [int(x.split("/")[0]) for x in t[1:]]
            obj["faces"].append([i - 1 if i > 0 else len(vertices) + i for i in face])
    return np.asarray(vertices, dtype=np.float64), objects


def repair(vertices, obj):
    triangles, ngons = [], 0
    for face in obj["faces"]:
        if len(face) == 3:
            triangles.append(face)
            continue
        # A fan triangulation cuts across concave chair backs and table mouldings.
        # Project onto the dominant plane and ear-clip, retaining the original winding.
        points = vertices[face]
        normal = np.cross(points, np.roll(points, -1, axis=0)).sum(axis=0)
        if np.linalg.norm(normal) < 1e-10:
            continue
        xy = np.delete(points, np.argmax(np.abs(normal)), axis=1).copy()
        indices = mapbox_earcut.triangulate_float64(xy, np.array([len(xy)], dtype=np.uint32)).reshape(-1, 3)
        tris = np.asarray(face)[indices]
        normals = np.cross(vertices[tris[:, 1]] - vertices[tris[:, 0]], vertices[tris[:, 2]] - vertices[tris[:, 0]])
        flip = normals @ normal < 0
        tris[flip] = tris[flip, ::-1]
        triangles.extend(tris.tolist())
        ngons += int(len(face) > 4)
    mesh = trimesh.Trimesh(vertices=vertices.copy(), faces=triangles, process=False)
    mesh.remove_unreferenced_vertices()
    before_vertices, before_faces = len(mesh.vertices), len(mesh.faces)
    mesh.merge_vertices(digits_vertex=5)
    mesh.update_faces(mesh.unique_faces())
    mesh.update_faces(mesh.nondegenerate_faces(height=1e-7))
    removed = before_faces - len(mesh.faces)
    before_fill = len(mesh.faces)
    trimesh.repair.fill_holes(mesh)  # Single triangle/quad gaps only; keep intentional openings.
    filled = len(mesh.faces) - before_fill
    before_winding = mesh.faces.copy()
    trimesh.repair.fix_normals(mesh, multibody=True)
    flipped = int(np.any(mesh.faces != before_winding, axis=1).sum())
    stats = dict(name=obj["name"], ngons=ngons, welded=before_vertices-len(mesh.vertices),
                 removed_faces=removed, filled_triangles=filled, flipped_faces=flipped,
                 watertight=bool(mesh.is_watertight), winding_consistent=bool(mesh.is_winding_consistent),
                 triangles=len(mesh.faces))
    # Split hard edges before recalculating normals, preserving flat table tops and
    # smooth turned legs. Original normals referred to the damaged polygon topology.
    smooth = trimesh.graph.smooth_shade(mesh, angle=np.deg2rad(42))
    stats["zero_normals"] = stabilize_normals(smooth)
    smooth.metadata = {"name": obj["name"]}
    return smooth, stats


def stabilize_normals(mesh):
    normals = mesh.vertex_normals.copy()
    bad = np.flatnonzero(np.linalg.norm(normals,axis=1) < 1e-8)
    for vertex in bad:
        faces = mesh.vertex_faces[vertex]
        faces = faces[faces >= 0]
        normals[vertex] = mesh.face_normals[faces[np.argmax(mesh.area_faces[faces])]]
    mesh.vertex_normals = normals
    return len(bad)


def export_scene(meshes, path):
    scene = trimesh.Scene()
    repaired_normals = 0
    for name, mesh in meshes:
        repaired_normals += stabilize_normals(mesh)
        scene.add_geometry(mesh, node_name=name, geom_name=name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    print(f"Wrote {path.name}: {path.stat().st_size / 1024:.0f} KB; repaired {repaired_normals} zero normals", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--all", action="store_true", help="Also repair the three unused variants")
    args = parser.parse_args()
    out = ROOT / "web/tune/studio-assets"
    archive = ROOT / ".cache/museum-review/repaired"
    report, prepared = {}, {}
    sources = sorted(args.source.glob("*.obj"))
    for path in sources:
        if not args.all and path.stem not in ("table_with_chairs_cups", "吊灯"):
            continue
        vertices, objects = read_obj(path)
        meshes, stats = [], []
        for i, obj in enumerate(objects):
            if not obj["faces"]:
                continue
            mesh, result = repair(vertices, obj)
            meshes.append((obj["name"], mesh)); stats.append(result)
            if i % 40 == 0:
                print(f"{path.name}: {i + 1}/{len(objects)} parts", flush=True)
        report[path.name] = {"parts": stats, "totals": {key:sum(s[key] for s in stats)
            for key in ("ngons","welded","removed_faces","filled_triangles","flipped_faces","zero_normals","triangles")}}
        export_scene(meshes, archive / (path.stem + ".glb"))
        prepared[path.stem] = meshes

    # Keep one detailed chair, the pedestal table, and its complete tea service.
    furniture = prepared["table_with_chairs_cups"]
    selected = []
    for i, (name, source) in enumerate(furniture):
        if 13 <= i < 39:
            continue
        mesh = source.copy()
        if i < 13:
            # The source chair faces the table; normalize it to face +Z.
            angle = np.arctan2(112.8-29.0, 352.2-296.1)
            transform = trimesh.transformations.rotation_matrix(np.pi-angle, [0,1,0])
            mesh.apply_translation([-29,320.4495,-296.1]); mesh.apply_transform(transform)
            role = "upholstery" if i in (1,2,11,12) else "walnut"
            name = "chair/" + role + "/" + name
        else:
            mesh.apply_translation([0,319.2131,0])
            if i < 62:
                role = "brass" if i in (39,40,41,42,43,47,50) else "porcelain"
            else:
                role = "marble" if i == 129 else "walnut"
            name = "table/" + role + "/" + name
        mesh.apply_scale(.0035)
        selected.append((name,mesh))
    # Merge parts sharing a transform/material to reduce draw calls without losing detail.
    groups = defaultdict(list)
    for name, mesh in selected:
        groups['/'.join(name.split('/')[:2])].append(mesh)
    export_scene([(name,trimesh.util.concatenate(meshes)) for name,meshes in groups.items()], out / "tea-furniture.glb")

    groups = defaultdict(list)
    chandelier = prepared["吊灯"]
    for name, source in chandelier:
        mesh = source.copy(); mesh.apply_translation([0,179.442509,0]); mesh.apply_scale(.0031)
        role = "crystal" if name.startswith("Cube") else "brass"
        if name.startswith("Sphere"):
            role = "pearl"
        groups[role].append(mesh)
    export_scene([(name,trimesh.util.concatenate(meshes)) for name,meshes in groups.items()], out / "chandelier.glb")
    archive.mkdir(parents=True,exist_ok=True)
    (archive / "repair-report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    for name, result in report.items():
        print(name,json.dumps(result["totals"]),flush=True)


if __name__ == "__main__":
    main()
