# Orb firmware

`main/hello_world_main.c` is the whole firmware: Wi-Fi/multicast client, ESP-NOW
peer sensing, the adaptive-gap clusterer (`compute_cluster_ids`, mirrored line for
line from the server), LED, motion, battery and audio drivers, OTA. The INA219
battery monitor is the one component split out (`components/ina219`).

Build with ESP-IDF v5.4 — see the top-level README. Before building, set
`WIFI_SSID` / `WIFI_PASS` to your own access point; the values here are placeholders.

**Read the safety-critical invariants in the top-level README first.** Orbs are
sealed: OTA is the only update path, rollback is off, and the image must stay under
1 MB.

`firmware-stable/` and `firmware-3.23/` are the images actually deployed to the
authors' fleet, with `SHA256SUMS`, for reference. Version history:
`../docs/FIRMWARE_HISTORY.md`.
