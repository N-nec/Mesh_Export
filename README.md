# Export Minetest Schematic (Remesh, .MTS) — Blender Addon


UPDATE: NOW EXPORTS .MTS FILES

## Overview
This Blender addon lets you export a selected mesh as a Minetest schematic in Lua table format, using Blender's Remesh (Blocks) modifier for voxelization.

## Installation
1. Open Blender (2.80 or newer).
2. Go to **Edit → Preferences → Add-ons → Install**.
3. Select the `blender_export_minetest_schematic_remesh.py` script and install it.
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
