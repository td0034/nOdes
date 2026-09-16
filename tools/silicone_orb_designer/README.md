# Silicone Orb Designer

Live 3D parameter explorer for the Goldberg-lattice LED orb diffuser shell
(the "silicone fill" variant): geodesic hex/pentagon lattice with membrane,
SDF-blended fillets, magnetic-connector entry (through-hole + counterbore),
PCB harpoon-clip rims, and the castellated equator split with clearance gap.

**Use:** open `silicone_designer.html` in a browser (no server needed).
Drag sliders or type exact values; the model rebuilds in ~1-3 s. Settings
persist in the browser (localStorage) and "Copy settings" exports them.
STL export (per half when split) works from the locally opened file.

Preview geometry only (surface nets over an SDF); production solids are
built natively in Fusion 360 by scripts kept in the development repository
(GoldbergOrbFusion / GoldbergOrbDialog / GoldbergOrbBatch), which share the
same tiling maths.

**Files**
- `silicone_designer.html`: the tool (built, self-contained; three.js + Google Fonts from CDN)
- `silicone_core.js`: geometry/SDF/mesher source; Node-testable (`require` and call `buildModel`/`meshField`)
- `silicone_app_shell.html`: UI source with `/*__CORE__*/` marker
- `build.py`: assembles the tool from the two sources
