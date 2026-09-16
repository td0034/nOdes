# nOdes → TouchDesigner

Everything here runs on **your** machine, not on the orbstation. The station is
already serving data the moment it boots; nothing needs installing on it.

Three ways in, easiest first. **Start with OSC**: it needs no code at all.

| | Route | Code needed | Gets you |
|---|---|---|---|
| 1 | **OSC In CHOP** | none | every orb as CHOP channels, ~25 Hz |
| 2 | **Web Client DAT** | none | one JSON snapshot on demand |
| 3 | **WebSocket DAT** | ~20 lines (provided) | full JSON pushed, incl. the whole proximity matrix |

Replace `ORBSTATION` below with whichever applies:

| Where you are | Address |
|---|---|
| Running the **simulator** on your own machine | `127.0.0.1` |
| Eggbox, **USB-C cable** (preferred) | `10.55.0.1` |
| Eggbox, joined to the **`OrbAP` hotspot** | `10.0.0.8` |

Everything else is identical in all three cases, so moving from the simulator
to real orbs is a one-line change.

First, sanity-check the station from a browser: **<http://ORBSTATION:8080/>**.
You should see a live table of orbs. If that page works, everything below will.

---

## 1. OSC In CHOP: no scripting

**a.** Visit this once in a browser (or any HTTP tool):

```
http://ORBSTATION:8080/osc/subscribe?port=7000
```

That tells the station "stream OSC to whatever machine just asked, on port
7000". It is **permanent**: it survives until you unsubscribe or the Pi
reboots. You do not need to keep the browser open.

**b.** In TouchDesigner: add an **OSC In CHOP**, set **Network Port** to `7000`.

Channels appear immediately:

```
orb/a1b2c3/accel1  accel2  accel3     accelerometer x,y,z  (g, gravity included)
orb/a1b2c3/gyro1   gyro2   gyro3      gyroscope x,y,z      (deg/s)
orb/a1b2c3/spin                       |gyro|, one number for "is it spinning"
orb/a1b2c3/shake                      ||accel|-1g|, "is it being shaken"
orb/a1b2c3/tilt                       -1..1, 1 = upright
orb/a1b2c3/cluster                    cluster id, -1 = not in one
orb/a1b2c3/hue                        0..1 the colour the orb is showing
orb/a1b2c3/battery                    volts, 3.0 empty .. 4.2 full
orb/a1b2c3/charging                   1 while sat on a charger pad
orb/a1b2c3/rssi                       orb→Pi wifi signal, dBm
prox/a1b2c3/d4e5f6                    INTER-ORB closeness: 0..1, then 0..255
fleet/count  fleet/rate  fleet/activity  fleet/clusters
```

`a1b2c3` is an orb's serial number, printed on the orb and stable forever, so you
can reference a specific orb by name in your patch.

**To stop the stream:** `http://ORBSTATION:8080/osc/unsubscribe?port=7000`

> **Note on `prox/`**: channel names only exist for pairs of orbs that can
> currently hear each other. Two orbs at opposite ends of the room have no
> channel at all rather than a zero. Use a **Null CHOP** downstream and read by
> name defensively, or use route 3 if you want the full fixed-size matrix.

---

## 2. Web Client DAT: a snapshot, no scripting

Add a **Web Client DAT**, set **URL** to `http://ORBSTATION:8080/td.json`, and
pulse **Request**. The JSON lands in the DAT. Set **Auto-request** on with a
rate if you want it repeatedly (this polls; route 1 or 3 is smoother).

Useful URLs:

| URL | What |
|---|---|
| `/td.json` | curated frame: the documented, stable shape |
| `/proximity.json` | just the inter-orb proximity matrix + edge list |
| `/orb_data.json` | the **raw** server substrate, verbatim, nothing hidden |
| `/schema.json` | what every single field means: read this |
| `/health` | is the bridge alive, how many orbs, who's subscribed to OSC |

---

## 3. WebSocket DAT: full JSON, pushed

Best route if you want the **whole NxN proximity matrix** as a fixed grid, or
anything from the raw substrate.

**a.** Add a **WebSocket DAT**. Set:
- **Network Address** `ORBSTATION`
- **Network Port** `8080`
- **Request URL** `/ws`  (or `/ws/raw` for the unmodified server substrate)
- **Active** on

**b.** Open its callbacks DAT and paste [`websocket_callbacks.py`](websocket_callbacks.py).

**c.** Add a **Script CHOP** next to it and paste [`orbs_script_chop.py`](orbs_script_chop.py)
You get one channel set per orb. For the proximity grid as a **TOP-ready
matrix**, add a second Script CHOP with [`proximity_script_chop.py`](proximity_script_chop.py).

Add `?hz=10` to the Request URL (`/ws?hz=10`) to receive fewer frames if 25 Hz
is more than your patch needs.

---

## The data, in one paragraph

Six orbs sit in an eggbox case. Each one reports its **motion** (accelerometer +
gyro) 50 times a second over Wi-Fi. Separately, each orb also listens for
**every other orb** over ESP-NOW radio and reports how strongly it hears them.
That is the **proximity** data, and it is genuinely orb-to-orb, not derived from
position. Strength runs 0 (can't hear it) to 255 (touching). It is *not* a
distance: it falls off non-linearly and is noisy. Treat it as "togetherness".
The station clusters orbs that hear each other strongly, and that clustering is
what drives the built-in light and sound behaviour.

## Gotchas worth knowing before you build

- **Orb serials are stable; slot numbers are not.** Key everything on the
  6-character serial (`a1b2c3`). `slot` only exists to index the proximity matrix.
- **An orb that is not charging sleeps after 30 seconds of stillness** and
  vanishes from the data entirely until shaken. This is normal, and it is the
  main thing to design around. Orbs sitting in *powered* charging wells stay
  awake, but once one is fully charged the charger tapers off and it can drop
  out too. Never assume a fixed orb count between frames.
- **Charging orbs still report, but vanish from the station's own screen.**
  The display in the eggbox lid deliberately hides docked orbs, so a case full
  of charging orbs shows an empty screen. They are still in *your* data,
  flagged `charging: true`. Filter on it if you only want orbs in hands.
  Test the path with `./run.sh 6 --charging 2` in the simulator.
- **Orbs get hot while charging**: charging must be attended. Never leave the
  charger supply running overnight or in an empty room.
- **The gyro saturates near 993 deg/s.** A hard spin pins the value; use
  *duration* of spin, not peak magnitude, if you want dynamics.
- **There is no wall clock.** The Pi has no battery-backed clock and no
  internet. `t` is seconds since the bridge started. Timestamps are meaningless.
- **Wi-Fi seats are scarce.** The Pi's radio only holds ~8 devices and the orbs
  need those seats. **Use the USB-C cable** for your laptop wherever you can.

## When something is wrong

| What you see | Try |
|---|---|
| Browser can't reach `:8080` | Wrong address. USB-C → `10.55.0.1`, hotspot → `10.0.0.8` |
| Page loads, "no orbs slotted" | Orbs are asleep. Lift one off the charger and shake it |
| OSC channels never appear | Re-visit the `/osc/subscribe` URL; check `/health` lists your IP |
| Channels appear then freeze | Check `/health`: if `ok` is false the server stopped |
| A `prox/` channel disappeared | Normal: those two orbs stopped hearing each other |
