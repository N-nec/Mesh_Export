bl_info = {
    "name": "Export Minetest .mts (3D voxel grid, materials, progress)",
    "author": "L-Dog",
    "version": (1, 4, 0),
    "blender": (3, 0, 0),
    "location": "File > Export > Minetest schematic (.mts)",
    "description": "Export active mesh as Minetest .mts schematic using a 3D voxel grid and Blender materials as Minetest nodes",
    "category": "Import-Export",
}

import bpy
import bmesh
import struct
import zlib
import io
import mathutils
from mathutils.bvhtree import BVHTree

from bpy.types import Operator
from bpy.props import IntProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper

DEFAULT_NODE = "default:stone"

MTSCHEM_PROB_ALWAYS = 0x7F


# ---------- material → node mapping ----------

def material_to_node_name(mat):
    if not mat:
        return DEFAULT_NODE
    name = mat.name.strip()
    if ":" in name:
        return name
    return DEFAULT_NODE


# ---------- voxelization helpers ----------


def voxel_is_solid(bvh, point, half_step):
    """Decide whether a voxel at *point* should be solid.

    A voxel is solid when its centre is either:
      1. very close to the mesh surface (thin shell for thin walls), or
      2. on the interior side of the nearest face (solid fill).
    """
    loc, normal, face_index, dist = bvh.find_nearest(point)
    if loc is None:
        return False

    vec = point - loc
    distance = vec.length

    # Thin surface shell – catches thin walls without creating
    # a thick halo of extra blocks around the outside of the mesh.
    if distance <= half_step * 0.25:
        return True

    # Interior fill – the vector from surface to point opposes the normal
    return vec.dot(normal) < 0


def voxel_material_from_point(bvh, bm, obj, point):
    loc, normal, face_index, dist = bvh.find_nearest(point)
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

    # param1 encoding:
    #   bits 0-6: placement probability (0 = never, 127 = always)
    #   bit 7:    force-place flag (overwrite non-air nodes)
    FORCE_ALWAYS = 0xFF   # force-place + probability 127

    with open(path, "wb") as f:
        # -- header --
        f.write(b"MTSM")
        f.write(struct.pack(">H", 4))           # version 4 (u16 big-endian)
        f.write(struct.pack(">H", sx))
        f.write(struct.pack(">H", sy))
        f.write(struct.pack(">H", sz))

        # -- Y-slice probabilities (one byte per Y layer) --
        f.write(bytes([MTSCHEM_PROB_ALWAYS] * sy))

        # -- name-ID mapping (index in list = node content ID) --
        f.write(struct.pack(">H", len(id_to_name)))
        for name in id_to_name:
            name_bytes = name.encode("utf-8")
            f.write(struct.pack(">H", len(name_bytes)))
            f.write(name_bytes)

        # -- node data (zlib compressed) --
        air_id = name_to_id["air"]
        buf = io.BytesIO()
        for cid in content_ids:
            buf.write(struct.pack(">H", cid))
        for cid in content_ids:
            if cid != air_id:
                # Solid nodes: always place, force-overwrite existing
                buf.write(struct.pack("B", FORCE_ALWAYS))
            elif force_place_air:
                # Air nodes: force-place to clear terrain/trees/grass
                buf.write(struct.pack("B", FORCE_ALWAYS))
            else:
                # Air nodes: don't overwrite (preserve existing terrain)
                buf.write(struct.pack("B", 0))
        for _ in content_ids:
            buf.write(b'\x00')                   # param2 (facedir etc.)

        f.write(zlib.compress(buf.getvalue()))


# ---------- exporter operator ----------

class EXPORT_OT_minetest_mts(Operator, ExportHelper):
    bl_idname = "export_scene.minetest_mts"
    bl_label = "Export Minetest schematic (.mts)"
    filename_ext = ".mts"

    voxel_resolution: IntProperty(
        name="Voxel resolution",
        description="Number of voxels along the largest dimension",
        default=32,
        min=8,
        max=1000,
    )

    force_place_air: BoolProperty(
        name="Force-place air",
        description="Clear terrain, trees, and grass inside the schematic bounding box. "
                    "Disable to preserve existing terrain where the schematic has air",
        default=True,
    )

    air_padding: IntProperty(
        name="Air padding",
        description="Extra air voxels around the mesh on all sides to prevent "
                    "mapgen decorations (trees, grass) from appearing on the structure",
        default=3,
        min=0,
        max=50,
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object")
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(eval_obj)
        mesh.transform(obj.matrix_world)  # world-space so export matches viewport

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

        bvh = BVHTree.FromBMesh(bm)

        xs = [v.co.x for v in bm.verts]
        ys = [v.co.y for v in bm.verts]
        zs = [v.co.z for v in bm.verts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)

        max_dim = max(max_x - min_x, max_y - min_y, max_z - min_z)
        if max_dim == 0:
            self.report({'ERROR'}, "Mesh has zero size")
            bm.free()
            bpy.data.meshes.remove(mesh)
            return {'CANCELLED'}

        step = max_dim / float(self.voxel_resolution)
        half_step = step * 0.5
        pad = self.air_padding

        nx = max(1, int((max_x - min_x) / step) + 1) + 2 * pad
        ny = max(1, int((max_y - min_y) / step) + 1) + 2 * pad
        nz = max(1, int((max_z - min_z) / step) + 1) + 2 * pad

        # Offset origin so the mesh is centered within the padded grid
        origin_x = min_x - pad * step
        origin_y = min_y - pad * step
        origin_z = min_z - pad * step

        # Blender axes:   X = right,   Y = forward, Z = up
        # Minetest axes:  X = east,    Y = up,      Z = south
        # Mapping: Blender X -> MTS X,  Blender Z -> MTS Y,  Blender Y -> MTS Z
        mts_sx = nx
        mts_sy = nz   # Blender Z (up) -> MTS Y (up)
        mts_sz = ny   # Blender Y (forward) -> MTS Z (forward)

        nodes = []
        solid_count = 0

        wm = context.window_manager
        wm.progress_begin(0, ny)

        try:
            # MTS stores nodes in Z-Y-X order (Z outermost, X innermost).
            # With our axis mapping:
            #   MTS Z loop -> iterates Blender Y
            #   MTS Y loop -> iterates Blender Z
            #   MTS X loop -> iterates Blender X
            for iy in range(ny):            # MTS Z index (Blender Y)
                wm.progress_update(iy)

                for iz in range(nz):        # MTS Y index (Blender Z)
                    for ix in range(nx):    # MTS X index (Blender X)
                        cx = origin_x + (ix + 0.5) * step
                        cy = origin_y + (iy + 0.5) * step
                        cz = origin_z + (iz + 0.5) * step

                        center = mathutils.Vector((cx, cy, cz))

                        if voxel_is_solid(bvh, center, half_step):
                            mat = voxel_material_from_point(bvh, bm, obj, center)
                            node_name = material_to_node_name(mat)
                            solid_count += 1
                        else:
                            node_name = "air"

                        nodes.append({"name": node_name})

        finally:
            wm.progress_end()

        size = (mts_sx, mts_sy, mts_sz)

        try:
            write_mts(self.filepath, size, nodes,
                      force_place_air=self.force_place_air)
        except Exception as e:
            bm.free()
            bpy.data.meshes.remove(mesh)
            self.report({'ERROR'}, f"Failed to write .mts: {e}")
            return {'CANCELLED'}

        bm.free()
        bpy.data.meshes.remove(mesh)

        total = mts_sx * mts_sy * mts_sz
        self.report(
            {'INFO'},
            f"Exported {self.filepath}  "
            f"({mts_sx}x{mts_sy}x{mts_sz}, "
            f"{solid_count} solid / {total} total voxels)"
        )
        return {'FINISHED'}


def menu_func_export(self, context):
    self.layout.operator(
        EXPORT_OT_minetest_mts.bl_idname,
        text="Minetest schematic (.mts)"
    )


def register():
    bpy.utils.register_class(EXPORT_OT_minetest_mts)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(EXPORT_OT_minetest_mts)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
    register()
