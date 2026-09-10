# Engineering notes

Working rules carried over from the development repository. Read the safety-critical section before touching firmware.

nOdes is a fleet of 24+ sealed wireless orbs (ESP32-S3: LEDs, accelerometer,
speaker, mic, battery) plus a central C++ server, used for collaborative
music-making at festivals and in research studies. This repo is both the
**field deployment** and the **active research codebase** — robustness rules
runbooks: `docs/` (see "Where truth lives").

## Safety-critical invariants (read before touching firmware)

- **Orbs are sealed — OTA is the ONLY update path.** No USB access on fleet
  orbs. **OTA rollback is NOT enabled** (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`
  unset; can't be enabled without USB). A bad OTA build permanently bricks
  hardware. Treat every fleet OTA like a production deploy: review the diff,
  bench-test on 2–3 orbs first.
- **Binary must stay under 1MB** (partition limit; typically ~99x KB with ~5%
  free). Check `client/build/espidf_orb.bin` size after every build. A custom
  partition table is NOT possible (needs USB).
- **New hardware/peripheral init must be server-triggered, never boot-time.**
  A boot-time crash loop on a sealed orb is unrecoverable; server-triggered
  init lets a bad feature stay dormant.
- **Never deploy the `audio_testing` line (fw 3.18–3.22) to the fleet.**
  Orb-local audio browns out the ESP32-S3 on non-full batteries → reset loop.
  v3.22 is a brownout sweep build with no WiFi — flashed orbs are
  OTA-unrecoverable. Audio experiments go on the `audio_testing` branch,
  flashed to a **USB-powered test board only** (`tools/reflash-orb.sh`, USB-powered test board only).
- **Deployed fleet version and staged binaries: check `docs/VERSIONS.md`**
  (kept current, includes md5s). Do not trust remembered version numbers —
  the 3.18 number was reused and renumbered to 3.23. Known-good deployed
  binaries are committed at `client/firmware-stable/` (with SHA256SUMS).

## Firmware constraints (ESP32-S3, ESP-IDF 5.4)

- **Core 0 runs WiFi (prio 23) and a 5s task watchdog on the idle task.** Any
  long-running task on core 0 MUST `vTaskDelay(1)` regularly — I2S read/write
  loops trigger the TWDT otherwise. Networking/battery tasks live on core 1.
- I2S output amplitude max ~16000 — higher causes battery sag → WiFi drop.
- `esp_wifi_sta_get_ap_info()` can fail during disconnect — never wrap it in
  `ESP_ERROR_CHECK`.
- `sdkconfig` is auto-generated — don't edit directly.
- ESP-NOW is part of `esp_wifi` in IDF 5.4 (not a separate component).
  Broadcast has two pacers: one-shot 2ms after multicast RX, and a 50Hz
  standalone timer gated on `online_watchdog` (keeps running server-silent).
- Firmware `compute_cluster_ids` mirrors the server's adaptive-gap flood-fill —
  **keep the two implementations in sync** when touching either.
- SD-card code was removed to save ~66KB — don't reintroduce fatfs/sdmmc/vfs.

## Build, release, versioning

```bash
# Firmware (bundled IDF)
cd client && source esp-idf/export.sh && idf.py build
ls -l build/espidf_orb.bin                  # MUST be < 1MB

# Stage for OTA
cp client/build/espidf_orb.bin server/espidf_orb.bin
cd server && python3 -m http.server 8000    # orbs fetch http://10.0.0.8:8000/espidf_orb.bin
# OTA trigger: server sends command 0x01 with target serial

# Server
cd server/build && make

# USB reflash of a single (opened/test) orb via CP210x jig
tools/reflash-orb.sh
```

- Firmware version: `#define ORBVERSION VERSION(M,m)` in
  `client/main/hello_world_main.c` (~line 52) — bump on each release; the orb
  reports it in every uplink packet.
- Server/visualiser/sound carry `@orb-version: X.Y` markers;
  `.githooks/prepare-commit-msg` appends `[tag vX.Y]` per touched component.
- After any release: update `docs/VERSIONS.md` and `docs/FIRMWARE_HISTORY.md`.

## Runtime architecture essentials

- Server → orbs: 50Hz multicast UDP `239.255.0.1:5000`, 128-byte packet
  (packet_num, command, slot_alloc, led_colour[32]) via Ruckus R500 AP
  ("OrbAP", multicast→unicast conversion).
- Orbs → server: unicast reply (serial, accel, rssi, battery, proximity /
  rssi_proximity). Packet size has grown across versions — server recv is
  backward-compatible (older/shorter packets get new fields zeroed).
- Slot allocation: server assigns slots; orbs self-identify by MAC-derived
  24-bit serial.
- Server modes: 0 ICOSAHEDRON, 1 CLUSTER, 2 PROXIMITY, 3 COMMS, 4 AWARENESS.
- Server exports live JSON at `/tmp/orb_data` (slot↔serial map, proximity,
  rssi_proximity) — consumed by `visualiser/proximity_graph.py` and tools.
- Server pins its 50Hz loop to isolated CPU core 2 with SCHED_FIFO.

## Hardware per orb (ESP32-S3)

- APA102 LEDs ×12 on SPI2: DATA=GPIO18, CLK=GPIO8
- LSM6DS accel on I2C0: SDA=GPIO9, SCL=GPIO10, INT1=GPIO21; INA219 battery on I2C0
- Speaker/amp on I2S0: DIN=GPIO40, BCLK=GPIO41, LRCK=GPIO42
- ICS-43434 mic on I2S1: WS=GPIO15, SCK=GPIO17, SD=GPIO16
- SD (removed from fw) on SPI3: MOSI=GPIO13, MISO=GPIO11, SCK=GPIO12, CS=GPIO14

## Field deployment rules

- **The orb-station PC must be `10.0.0.8`** — firmware hardcodes the server
  IP. The Ruckus (`10.0.0.1`) is the only DHCP server; if the PC boots before
  the AP it gets a wrong lease and no orb connects. First check when orbs
  won't connect: `ip -4 addr show eth0` == `10.0.0.8`. Prefer a static
  netplan address / DHCP reservation so boot order never matters.
- Pre-gig network self-test: `tools/field_netcheck.sh` (tether / remote-fix).
  (PipeWire, scsynth via `pw-jack`, 48k/1024) — never the Pi's 3.5mm, never
  can't corrupt the SD. Captures go to the writable partition.
