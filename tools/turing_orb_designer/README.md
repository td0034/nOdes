# Turing Orb Designer

Variant of the Silicone Orb Designer where the shell pattern is a Turing
pattern: a Gray–Scott reaction–diffusion system solved live on an icosphere
(graph Laplacian, classic Pearson formulation), thresholded into walls cut
through the spherical shell. Membrane + fillets, magnetic-connector entry,
PCB harpoon rims, plain equator split with clearance gap, per-half STL
export — all as in the silicone tool.

**Model variables** (standard Gray–Scott parameterisation, unit lattice
spacing so paper values carry over): diffusion rates `Du`, `Dv`, feed rate
`F`, kill rate `k`, timestep `dt`, iteration count, sim resolution
(icosphere frequency = pattern scale on the orb), seed patches/noise, RNG
seed (Reseed button). Pearson regime presets: coral, mitosis, worms, maze,
holes, solitons. Wall threshold + invert select the level set.

Open `turing_designer.html` in a browser; `build.py` reassembles it from
`turing_core.js` (Node-testable) + `turing_app_shell.html`.
