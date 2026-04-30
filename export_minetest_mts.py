bl_info = {
    "name": "Export Minetest .mts (3D voxel grid, materials, progress)",
    "author": "L-Dog",
    "version": (1, 6, 0),
    "blender": (2, 80, 0),
    "location": "File > Export > Minetest schematic (.mts)",
    "description": "Export mesh as Minetest .mts schematic. Works as a "
                   "Blender addon (GUI) or headless CLI",
    "category": "Import-Export",
}

# ================================================================
# DUAL-MODE EXPORTER
#
# As a Blender addon (GUI):
#   Install via Edit > Preferences > Add-ons > Install.
#   Then File > Export > Minetest schematic (.mts).
#
# As a headless CLI tool (no GUI needed):
#   blender --background --python export_minetest_mts.py -- \
#       input.blend 10000 output.mts [options]
#
#   Options:
#     --side-padding N    Air padding on sides/bottom (default: 3)
#     --top-padding N     Air padding above the mesh (default: 15)
#     --no-force-air      Don't force-place air nodes
#     --default-node NAME Default node name (default: default:stone)
# ================================================================

import sys
import struct
import zlib
import io
import time

import bpy
import bmesh
import mathutils
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None
from mathutils.bvhtree import BVHTree

DEFAULT_NODE = "default:stone"

MTSCHEM_PROB_ALWAYS = 0x7F


# ---------- material -> node mapping ----------

def material_to_node_name(mat, default_node=None):
    if default_node is None:
        default_node = DEFAULT_NODE
    if not mat:
        return default_node
    name = mat.name.strip()
    if ":" in name:
        return name
    return default_node


# ---------- voxelization helpers ----------

def voxel_is_solid(bvh, point, half_step):
    """Decide whether a voxel at *point* should be solid.

    A voxel is solid when its centre is either:
      1. very close to the mesh surface (thin shell for thin walls), or
      2. on the interior side of the nearest face (solid fill).
    """
    loc, normal, _face_index, _dist = bvh.find_nearest(point)
    if loc is None:
        return False

    vec = point - loc
    distance = vec.length

    # Thin surface shell
    if distance <= half_step * 0.25:
        return True

    # Interior fill
    return vec.dot(normal) < 0


def voxel_material_from_point(bvh, bm, obj, point):
    loc, _normal, face_index, _dist = bvh.find_nearest(point)
    if loc is None or face_index is None or face_index < 0:
        return None
    face = bm.faces[face_index]
    if face.material_index < len(obj.material_slots):
        return obj.material_slots[face.material_index].material
    return None


# ---------- .mts writer (version 4, zlib-compressed) ----------

def write_mts(path, size, nodes, force_place_air=True):
    """Write an MTS version-4 schematic file compatible with Minetest 5.x."""
    sx, sy, sz = size

    used = set(n["name"] for n in nodes)
    used.add("air")

    id_to_name = sorted(used)
    name_to_id = {name: idx for idx, name in enumerate(id_to_name)}

    content_ids = [name_to_id[n["name"]] for n in nodes]

    FORCE_ALWAYS = 0xFF

    with open(path, "wb") as f:
        f.write(b"MTSM")
        f.write(struct.pack(">H", 4))
        f.write(struct.pack(">H", sx))
        f.write(struct.pack(">H", sy))
        f.write(struct.pack(">H", sz))

        f.write(bytes([MTSCHEM_PROB_ALWAYS] * sy))

        f.write(struct.pack(">H", len(id_to_name)))
        for name in id_to_name:
            name_bytes = name.encode("utf-8")
            f.write(struct.pack(">H", len(name_bytes)))
            f.write(name_bytes)

        air_id = name_to_id["air"]
        buf = io.BytesIO()
        for cid in content_ids:
            buf.write(struct.pack(">H", cid))
        for cid in content_ids:
            if cid != air_id:
                buf.write(struct.pack("B", FORCE_ALWAYS))
            elif force_place_air:
                buf.write(struct.pack("B", FORCE_ALWAYS))
            else:
                buf.write(struct.pack("B", 0))
        for _ in content_ids:
            buf.write(b'\x00')

        f.write(zlib.compress(buf.getvalue()))


# ---------- shared voxelize + export core ----------

def voxelize_and_export(combined_bm, obj_for_materials, filepath,
                        voxel_resolution, pad, top_pad,
                        force_place_air, default_node,
                        progress_callback=None):
    """Core export logic shared by both GUI and CLI modes."""

    bvh = BVHTree.FromBMesh(combined_bm)

    xs = [v.co.x for v in combined_bm.verts]
    ys = [v.co.y for v in combined_bm.verts]
    zs = [v.co.z for v in combined_bm.verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    max_dim = max(max_x - min_x, max_y - min_y, max_z - min_z)
    if max_dim == 0:
        return None, "Mesh has zero size"

    step = max_dim / float(voxel_resolution)
    half_step = step * 0.5

    nx = max(1, int((max_x - min_x) / step) + 1) + 2 * pad
    ny = max(1, int((max_y - min_y) / step) + 1) + 2 * pad
    nz = max(1, int((max_z - min_z) / step) + 1) + pad + top_pad

    origin_x = min_x - pad * step
    origin_y = min_y - pad * step
    origin_z = min_z - pad * step

    # Blender: X=right, Y=forward, Z=up
    # Minetest: X=east, Y=up, Z=south
    mts_sx = nx
    mts_sy = nz
    mts_sz = ny

    total_voxels = mts_sx * mts_sy * mts_sz

    nodes = []
    solid_count = 0
    start_time = time.time()
    last_report = start_time

    for iy in range(ny):
        if progress_callback:
            progress_callback(iy, ny, start_time)

        for iz in range(nz):
            for ix in range(nx):
                cx = origin_x + (ix + 0.5) * step
                cy = origin_y + (iy + 0.5) * step
                cz = origin_z + (iz + 0.5) * step

                center = mathutils.Vector((cx, cy, cz))

                if voxel_is_solid(bvh, center, half_step):
                    mat = voxel_material_from_point(
                        bvh, combined_bm, obj_for_materials, center)
                    node_name = material_to_node_name(mat, default_node)
                    solid_count += 1
                else:
                    node_name = "air"

                nodes.append({"name": node_name})

    size = (mts_sx, mts_sy, mts_sz)
    write_mts(filepath, size, nodes, force_place_air=force_place_air)

    elapsed = time.time() - start_time
    info = (f"{filepath}  ({mts_sx}x{mts_sy}x{mts_sz}, "
            f"{solid_count} solid / {total_voxels} total voxels, "
            f"{elapsed:.1f}s)")
    return info, None


# ================================================================
# GUI MODE — Blender addon operator
# ================================================================

from bpy.types import Operator
from bpy.props import IntProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper


class EXPORT_OT_minetest_mts(Operator, ExportHelper):
    bl_idname = "export_scene.minetest_mts"
    bl_label = "Export Minetest schematic (.mts)"
    filename_ext = ".mts"


    voxel_resolution: IntProperty(
        name="Voxel resolution",
        description="Number of voxels along the largest dimension",
        default=32,
        min=8,
        max=10000,
    )

    old_machine: BoolProperty(
        name="Old Machine Mode",
        description="Enable safe chunking and auto-merge for low-memory systems",
        default=False,
    )

    force_place_air: BoolProperty(
        name="Force-place air",
        description="Clear terrain, trees, and grass inside the schematic "
                    "bounding box. Disable to preserve existing terrain "
                    "where the schematic has air",
        default=True,
    )

    air_padding: IntProperty(
        name="Side/bottom padding",
        description="Extra air voxels around the sides and bottom of the mesh",
        default=3,
        min=0,
        max=50,
    )

    top_air_padding: IntProperty(
        name="Top padding",
        description="Extra air voxels ABOVE the mesh to clear tall trees "
                    "and mapgen decorations. Minetest trees can be 10-15 "
                    "blocks tall",
        default=15,
        min=0,
        max=100,
    )

    chunk_size: IntProperty(
        name="Chunk Size",
        description="Chunk size in Blender units (0=auto, disables chunking)",
        default=1,
        min=0,
        max=10000,
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object")
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(eval_obj)
        mesh.transform(obj.matrix_world)

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()

        if not bm.verts:
            self.report({'ERROR'}, "Mesh has no vertices")
            bm.free()
            bpy.data.meshes.remove(mesh)
            return {'CANCELLED'}

        wm = context.window_manager
        wm.progress_begin(0, 100)

        def gui_progress(iy, ny, _start):
            wm.progress_update(int(iy / ny * 100) if ny > 0 else 100)

        info, err = voxelize_and_export(
            bm, obj, self.filepath,
            self.voxel_resolution,
            self.air_padding,
            self.top_air_padding,
            self.force_place_air,
            DEFAULT_NODE,
            progress_callback=gui_progress,
        )
        wm.progress_end()
        bm.free()
        bpy.data.meshes.remove(mesh)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        self.report({'INFO'}, f"Exported {info}")
        return {'FINISHED'}


def menu_func_export(self, context):
    self.layout.operator(
        EXPORT_OT_minetest_mts.bl_idname,
        text="Minetest schematic (.mts)"
    )

def draw(self, context):
        layout = self.layout
        layout.prop(self, "voxel_resolution")
        layout.prop(self, "force_place_air")
        layout.prop(self, "air_padding")
        layout.prop(self, "top_air_padding")
        layout.prop(self, "chunk_size")
        layout.prop(self, "old_machine")

def register():
    bpy.utils.register_class(EXPORT_OT_minetest_mts)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.utils.unregister_class(EXPORT_OT_minetest_mts)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


# ================================================================
# CLI MODE — headless export from terminal
# ================================================================

def _is_headless():
    """Return True when Blender was launched with --background."""
    return bpy.app.background


def _has_cli_args():
    """Return True when CLI arguments were passed after '--'."""
    return "--" in sys.argv



def cli_main():
    # ...existing code for argument parsing and setup...
    # Place chunking/export logic here, not registration lines
    """Entry point for headless CLI mode with chunked export support."""
    import argparse
    import math
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(
        description="Export a .blend to Minetest .mts schematic (headless, with chunking support).\n"
                    "\nChunking options:\n"
                    "  --chunk-size N         Chunk size in Blender units (0=disable chunking).\n"
                    "  --blender-unit-to-node N  Blender units per Minetest node (default: 0.5).\n"
                    "  --old-machine          Enable safe chunking for low-memory systems.\n"
                    "\nExample: --chunk-size 50 --voxel_resolution 200 will export each 50x50x50 Blender unit region as a 200x200x200 node .mts file."
    )
    parser.add_argument("input_blend", help="Path to input .blend file")
    parser.add_argument("voxel_resolution", type=int,
                        help="Voxels along largest dimension (per chunk if chunking)")
    parser.add_argument("output_mts", help="Path for output .mts file or chunk prefix")
    parser.add_argument("--side-padding", type=int, default=3,
                        help="Air padding on sides/bottom (default: 3)")
    parser.add_argument("--top-padding", type=int, default=15,
                        help="Air padding above mesh (default: 15)")
    parser.add_argument("--no-force-air", action="store_true",
                        help="Don't force-place air nodes")
    parser.add_argument("--default-node", default=DEFAULT_NODE,
                        help="Default node name (default: default:stone)")
    parser.add_argument("--chunk-size", type=float, default=0,
                        help="Chunk size in Blender units (0=disable chunking)")
    parser.add_argument("--blender-unit-to-node", type=float, default=0.5,
                        help="How many Blender units per Minetest node (default: 0.5)")
    parser.add_argument("--old-machine", action="store_true",
                        help="Enable safe chunking for low-memory systems (sets chunk size to 300 if not set)")

    args = parser.parse_args(argv)

    # Old Machine Mode: set safe chunk size if not set
    if args.old_machine:
        if not args.chunk_size or args.chunk_size < 1:
            args.chunk_size = 300
            print("[mts_exporter] Old Machine Mode: chunk size set to 300 for low-memory export.")
        else:
            print(f"[mts_exporter] Old Machine Mode: using user chunk size {args.chunk_size}.")

    print(f"[mts_exporter] Loading {args.input_blend} ...")
    bpy.ops.wm.open_mainfile(filepath=args.input_blend)

    depsgraph = bpy.context.evaluated_depsgraph_get()

    # Gather all mesh objects into a single bmesh
    combined_bm = bmesh.new()
    obj_for_materials = None
    mesh_count = 0

    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue

        eval_obj = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(eval_obj)
        mesh.transform(obj.matrix_world)

        temp_bm = bmesh.new()
        temp_bm.from_mesh(mesh)

        if obj_for_materials is None:
            obj_for_materials = obj
        else:
            for face in temp_bm.faces:
                if face.material_index < len(obj.material_slots):
                    mat = obj.material_slots[face.material_index].material
                    found = False
                    for i, slot in enumerate(
                            obj_for_materials.material_slots):
                        if slot.material == mat:
                            face.material_index = i
                            found = True
                            break
                    if not found:
                        obj_for_materials.data.materials.append(mat)
                        face.material_index = (
                            len(obj_for_materials.material_slots) - 1)

        temp_bm.verts.ensure_lookup_table()
        temp_bm.faces.ensure_lookup_table()

        offset = len(combined_bm.verts)
        for v in temp_bm.verts:
            combined_bm.verts.new(v.co)
        combined_bm.verts.ensure_lookup_table()

        for face in temp_bm.faces:
            try:
                new_verts = [combined_bm.verts[v.index + offset]
                             for v in face.verts]
                new_face = combined_bm.faces.new(new_verts)
                new_face.material_index = face.material_index
            except Exception:
                pass

        temp_bm.free()
        bpy.data.meshes.remove(mesh)
        mesh_count += 1

    if mesh_count == 0 or not combined_bm.verts:
        print("[mts_exporter] ERROR: No mesh objects found in the .blend file.")
        sys.exit(1)

    combined_bm.verts.ensure_lookup_table()
    combined_bm.faces.ensure_lookup_table()
    combined_bm.normal_update()

    print(f"[mts_exporter] Merged {mesh_count} mesh object(s).")

    # Get bounding box in Blender units
    xs = [v.co.x for v in combined_bm.verts]
    ys = [v.co.y for v in combined_bm.verts]
    zs = [v.co.z for v in combined_bm.verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    # Convert to node space
    node_x = (max_x - min_x) / args.blender_unit_to_node
    node_y = (max_y - min_y) / args.blender_unit_to_node
    node_z = (max_z - min_z) / args.blender_unit_to_node

    if args.chunk_size > 0:
        # Chunking enabled
        chunk_size_nodes = int(args.chunk_size / args.blender_unit_to_node)
        nx = math.ceil(node_x / chunk_size_nodes)
        ny = math.ceil(node_y / chunk_size_nodes)
        nz = math.ceil(node_z / chunk_size_nodes)
        print(f"[mts_exporter] Chunking: {nx} x {ny} x {nz} chunks of {chunk_size_nodes} nodes each")
        MAX_VOXELS_PER_CHUNK = 10_000_000  # 10 million
        for cx in range(nx):
            for cy in range(ny):
                for cz in range(nz):
                    # Compute chunk bounds in Blender units
                    chunk_min_x = min_x + cx * args.chunk_size
                    chunk_max_x = min(chunk_min_x + args.chunk_size, max_x)
                    chunk_min_y = min_y + cy * args.chunk_size
                    chunk_max_y = min(chunk_min_y + args.chunk_size, max_y)
                    chunk_min_z = min_z + cz * args.chunk_size
                    chunk_max_z = min(chunk_min_z + args.chunk_size, max_z)

                    # Filter verts and faces in chunk
                    chunk_bm = bmesh.new()
                    vert_map = {}
                    for v in combined_bm.verts:
                        if (chunk_min_x <= v.co.x < chunk_max_x and
                            chunk_min_y <= v.co.y < chunk_max_y and
                            chunk_min_z <= v.co.z < chunk_max_z):
                            new_v = chunk_bm.verts.new(v.co)
                            vert_map[v.index] = new_v
                    chunk_bm.verts.ensure_lookup_table()
                    if not chunk_bm.verts:
                        chunk_bm.free()
                        continue

                    # Filter faces: only add faces where all verts are in chunk
                    for face in combined_bm.faces:
                        if all(idx in vert_map for idx in [v.index for v in face.verts]):
                            new_verts = [vert_map[v.index] for v in face.verts]
                            try:
                                new_face = chunk_bm.faces.new(new_verts)
                                new_face.material_index = face.material_index
                            except Exception:
                                pass
                    chunk_bm.faces.ensure_lookup_table()
                    chunk_bm.normal_update()

                    # Calculate proportional voxel resolution for this chunk
                    chunk_dim_x = (chunk_max_x - chunk_min_x) / args.blender_unit_to_node
                    chunk_dim_y = (chunk_max_y - chunk_min_y) / args.blender_unit_to_node
                    chunk_dim_z = (chunk_max_z - chunk_min_z) / args.blender_unit_to_node
                    max_chunk_dim = max(chunk_dim_x, chunk_dim_y, chunk_dim_z)
                    # Always use user-specified voxel_resolution for every chunk
                    chunk_voxel_res = args.voxel_resolution

                    # Estimate voxel count
                    step = max_chunk_dim / float(chunk_voxel_res)
                    nx_vox = max(1, int(chunk_dim_x / step) + 1) + 2 * args.side_padding
                    ny_vox = max(1, int(chunk_dim_y / step) + 1) + 2 * args.side_padding
                    nz_vox = max(1, int(chunk_dim_z / step) + 1) + args.side_padding + args.top_padding
                    est_voxels = nx_vox * ny_vox * nz_vox
                    if est_voxels > MAX_VOXELS_PER_CHUNK:
                        print(f"[mts_exporter] WARNING: Skipping chunk {cx},{cy},{cz} (est. {est_voxels:,} voxels exceeds safe limit)")
                        chunk_bm.free()
                        continue

                    chunk_name = f"{args.output_mts}_chunk_{cx}_{cy}_{cz}.mts"
                    print(f"[mts_exporter] Exporting chunk {chunk_name} at voxel_res={chunk_voxel_res} (est. {est_voxels:,} voxels)")
                    def chunk_progress(iy, ny, start_time):
                        pass  # Optionally add progress per chunk
                    info, err = voxelize_and_export(
                        chunk_bm, obj_for_materials, chunk_name,
                        chunk_voxel_res,
                        args.side_padding,
                        args.top_padding,
                        not args.no_force_air,
                        args.default_node,
                        progress_callback=chunk_progress,
                    )
                    chunk_bm.free()
                    if err:
                        print(f"[mts_exporter] ERROR: {err} (chunk {chunk_name})")
        print(f"[mts_exporter] Done chunked export.")
        # If Old Machine Mode, try to auto-merge chunks
        if getattr(args, 'old_machine', False):
            try:
                import subprocess
                # Count nx, ny, nz by scanning chunk files
                import glob
                import re
                chunk_files = glob.glob(f"{args.output_mts}_chunk_*.mts")
                coords = [tuple(map(int, re.findall(r"(\d+)", f.split('_chunk_')[-1]))) for f in chunk_files]
                if coords:
                    max_x = max(c[0] for c in coords) + 1
                    max_y = max(c[1] for c in coords) + 1
                    max_z = max(c[2] for c in coords) + 1
                    print(f"[mts_exporter] Auto-merging {len(chunk_files)} chunks: nx={max_x} ny={max_y} nz={max_z}")
                    merge_cmd = [
                        sys.executable, os.path.join(os.path.dirname(__file__), "mts_merge_chunks.py"),
                        "--chunk-prefix", f"{args.output_mts}_chunk_",
                        "--out", f"{args.output_mts}_merged.mts",
                        "--nx", str(max_x), "--ny", str(max_y), "--nz", str(max_z)
                    ]
                    print(f"[mts_exporter] Running merge: {' '.join(merge_cmd)}")
                    subprocess.run(merge_cmd, check=True)
                    print(f"[mts_exporter] Merged output: {args.output_mts}_merged.mts")
            except Exception as e:
                print(f"[mts_exporter] Auto-merge failed: {e}")
        return

    # No chunking: normal export
    def cli_progress(iy, ny, start_time):
        now = time.time()
        # Only print every 5 seconds
        if not hasattr(cli_progress, "_last"):
            cli_progress._last = 0
        if now - cli_progress._last < 5.0 and iy > 0:
            return
        cli_progress._last = now
        pct = (iy / ny) * 100 if ny > 0 else 100
        elapsed = now - start_time
        if iy > 0:
            eta = elapsed / iy * (ny - iy)
            eta_str = f"{eta:.0f}s"
        else:
            eta_str = "?"
        print(f"[mts_exporter]   slice {iy}/{ny} "
              f"({pct:.1f}%) elapsed={elapsed:.0f}s ETA={eta_str}")

    print(f"[mts_exporter] Voxelizing at resolution {args.voxel_resolution} ...")

    info, err = voxelize_and_export(
        combined_bm, obj_for_materials, args.output_mts,
        args.voxel_resolution,
        args.side_padding,
        args.top_padding,
        not args.no_force_air,
        args.default_node,
        progress_callback=cli_progress,
    )

    combined_bm.free()

    if err:
        print(f"[mts_exporter] ERROR: {err}")
        sys.exit(1)

    import os
    file_size = os.path.getsize(args.output_mts)
    print(f"[mts_exporter] Done! {info}")
    print(f"[mts_exporter] File size: {file_size:,} bytes")


# ================================================================
# Entry point: auto-detect mode
# ================================================================


# Only define register/unregister for Blender's Add-on system
# Do not call register() at import time; Blender handles this.

if __name__ == "__main__":
    if _is_headless() and _has_cli_args():
        cli_main()
