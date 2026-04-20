bl_info = {
    "name": "Export Minetest Schematic (Remesh Voxel)",
    "author": "L-Dog and Copilot",
    "version": (1, 4),
    "blender": (2, 80, 0),
    "location": "File > Export > Minetest Schematic (.lua)",
    "description": "Export selected mesh as a solid Minetest schematic Lua table using Remesh (Blocks).",
    "category": "Import-Export",
}

import bpy
import math
import os

from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, IntProperty
from bpy.types import Operator

MATERIAL_TO_NODE = {
    "grass": "default:dirt_with_grass",
    "stone": "default:stone",
    "dirt": "default:dirt",
    "sand": "default:sand",
    "wood": "default:wood",
    "glass": "default:glass",
}
FALLBACK_NODE = "default:stone"

class ExportMinetestSchematicRemesh(Operator, ExportHelper):
    bl_idname = "export_scene.minetest_schematic_remesh"
    bl_label = "Export Minetest Schematic (Remesh)"
    filename_ext = ".lua"

    filter_glob: StringProperty(
        default="*.lua",
        options={'HIDDEN'},
        maxlen=255,
    )
    voxel_resolution: IntProperty(
        name="Voxel Resolution",
        description="Number of blocks along the largest axis (higher = finer)",
        default=32,
        min=4,
        max=400,
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object.")
            return {'CANCELLED'}


        # Ensure we're in Object Mode
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')

        # Deselect all, select only the object
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # Duplicate the object to work on
        bpy.ops.object.duplicate()
        voxel_obj = context.active_object

        # Apply all transforms
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Add Remesh modifier (Blocks mode)
        remesh = voxel_obj.modifiers.new('Remesh', 'REMESH')
        remesh.mode = 'BLOCKS'
        remesh.octree_depth = int(math.log2(self.voxel_resolution))
        remesh.use_remove_disconnected = False
        remesh.use_smooth_shade = False
        bpy.ops.object.modifier_apply(modifier=remesh.name)

        # Axis fix: Blender Y up -> Minetest Z up
        voxel_obj.rotation_euler[0] = -math.pi/2
        bpy.ops.object.transform_apply(rotation=True)

        # Get mesh data
        mesh = voxel_obj.data
        verts = [v.co for v in mesh.vertices]
        minx = min(v.x for v in verts)
        miny = min(v.y for v in verts)
        minz = min(v.z for v in verts)
        maxx = max(v.x for v in verts)
        maxy = max(v.y for v in verts)
        maxz = max(v.z for v in verts)


        # Build a 3D grid and mark surface voxels
        import numpy as np
        grid_shape = (int(maxx - minx) + 3, int(maxy - miny) + 3, int(maxz - minz) + 3)
        offset = (int(round(minx)) - 1, int(round(miny)) - 1, int(round(minz)) - 1)
        grid = np.zeros(grid_shape, dtype=np.int8)  # 0=empty, 1=surface, 2=air, 3=solid
        mat_grid = np.full(grid_shape, -1, dtype=np.int16)

        # Mark surface voxels
        for poly in mesh.polygons:
            center = poly.center
            ix = int(math.floor(center.x)) - offset[0]
            iy = int(math.floor(center.y)) - offset[1]
            iz = int(math.floor(center.z)) - offset[2]
            grid[ix, iy, iz] = 1
            mat_grid[ix, iy, iz] = poly.material_index

        # Flood fill from outside to mark air
        from collections import deque
        q = deque()
        sx, sy, sz = grid.shape
        for x in range(sx):
            for y in range(sy):
                for z in range(sz):
                    if x in (0, sx-1) or y in (0, sy-1) or z in (0, sz-1):
                        if grid[x, y, z] == 0:
                            grid[x, y, z] = 2
                            q.append((x, y, z))
        while q:
            x, y, z = q.popleft()
            for dx, dy, dz in [(-1,0,0),(1,0,0),(0,-1,0),(0,1,0),(0,0,-1),(0,0,1)]:
                nx, ny, nz = x+dx, y+dy, z+dz
                if 0 <= nx < sx and 0 <= ny < sy and 0 <= nz < sz:
                    if grid[nx, ny, nz] == 0:
                        grid[nx, ny, nz] = 2
                        q.append((nx, ny, nz))

        # All remaining 0s are solid interior
        count = 0
        with open(self.filepath, 'w') as f:
            f.write("return {\n")
            for x in range(sx):
                for y in range(sy):
                    for z in range(sz):
                        if grid[x, y, z] == 1 or grid[x, y, z] == 0:
                            # Use surface material if available, else fallback
                            mat_idx = mat_grid[x, y, z]
                            mat_name = None
                            if voxel_obj.material_slots and mat_idx >= 0 and mat_idx < len(voxel_obj.material_slots):
                                mat_name = voxel_obj.material_slots[mat_idx].name
                            node = MATERIAL_TO_NODE.get(mat_name.lower() if mat_name else '', FALLBACK_NODE)
                            ix = (sx - 1 - x) + offset[0]
                            iy = y + offset[1]
                            iz = z + offset[2]
                            f.write(f"  {{{ix}, {iy}, {iz}, '{node}'}},\n")
                            count += 1
            f.write("}\n")

        # Delete the temporary voxel object
        bpy.ops.object.delete()

        return {'FINISHED'}

def menu_func_export(self, context):
    self.layout.operator(ExportMinetestSchematicRemesh.bl_idname, text="Minetest Schematic (Remesh, .lua)")

def register():
    bpy.utils.register_class(ExportMinetestSchematicRemesh)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.utils.unregister_class(ExportMinetestSchematicRemesh)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

if __name__ == "__main__":
    register()
