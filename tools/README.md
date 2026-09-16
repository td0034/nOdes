# Tools

Scripts, infrastructure configuration, and binary blobs that aren't part of the
orb firmware, server, visualiser, or sound host. Anything that helps us
deploy, operate, recover, or debug the system lives here.

## Current contents

- `R500_200.7.10.2.339.bl7`: Ruckus R500 access-point firmware image.
  Flash the AP to this version if it's ever downgraded or you're building a
  new stack. Everything in `server/src/multicast_sender.cpp` assumes the
  OrbAP runs this build.

## What belongs here

- Setup scripts (AP configuration, DHCP reservation helpers, one-shot
  provisioners)
- Session-recording / session-analysis CLIs (e.g. the study-time
  `session_recorder.py` / `session_analyzer.py` in the CHI 2027 protocol
  punchlist)
- Binary blobs we need to keep on hand (AP firmware, bootloader images)

## What belongs in `visualiser/` instead

- Anything whose output is a live or post-hoc visualisation: proximity graph,
  cluster scatter, Grafana dashboards, InfluxDB bridge
