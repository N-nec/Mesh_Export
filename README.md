
 DUAL-MODE EXPORTER

# As a Blender addon (GUI):
   Install via Edit > Preferences > Add-ons > Install.
   Then File > Export > Minetest schematic (.mts).

# As a headless CLI tool (no GUI needed):
   blender --background --python Path/To/export_minetest_mts.py -- Path/to/Input.blend [voxel res / size] Output.mts
    
#   Options:
     --side-padding N    Air padding on sides/bottom (default: 3)
     --top-padding N     Air padding above the mesh (default: 15)
     --no-force-air      Don't force-place air nodes
     --default-node NAME Default node name (default: default:stone)



UPDATE: NOW EXPORTS .MTS FILES
UPDATE: NOW EXPORTS COLOR AND HEIGHTMAP ... For MapGen

## Overview
This Blender addon lets you export a selected mesh as a Minetest schematic in Lua table format, using Blender's Remesh (Blocks) modifier for voxelization.

## Installation
1. Open Blender (2.80 or newer).
2. Go to **Edit → Preferences → Add-ons → Install**.
3. Select the `export_minetest_mts.py` script and install it.
4. Enable the addon in the Add-ons list.

## Export Instructions
1. **Create or select your mesh in Blender.**
2. Enter **Edit Mode** and make any edits needed.
3. Switch back to **Object Mode**.
4. Go to **File → Export → Minetest Schematic (.mts)**.
5. Choose a filename and location for your `.mts` schematic file.
6. **Increase the Voxel Resolution** slider for higher detail and to avoid gaps in the exported schematic.

## Tips for Proper Export
- **Mesh Thickness:** Your mesh must have actual thickness (volume). Flat planes or single faces will not export as visible blocks. Use solidify or extrude to give thickness.
- **Scale:** Both large and small meshes are supported, but very tiny details may be lost at low voxel resolutions.
- **Materials:** The exporter maps Blender material names to Minetest node names. Unmapped materials default to `default:stone`.
- **Transformations:** All transforms (location, rotation, scale) are applied automatically before export.

## Troubleshooting
- If you see gaps, increase the **Voxel Resolution**.
- If nothing appears in Minetest, check that your mesh is solid (not just a plane).
- Make sure you are using Blender 2.80 or newer.

---

**Author:** L-Dog and Copilot
