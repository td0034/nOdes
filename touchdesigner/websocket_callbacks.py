# Paste into the callbacks DAT of a WebSocket DAT pointed at
#   ws://ORBSTATION:8080/ws        (curated — matches orbs_script_chop.py)
#   ws://ORBSTATION:8080/ws/raw    (the unmodified server substrate)
#
# Parses each pushed frame and parks it in the WebSocket DAT's storage under
# 'frame'. Downstream Script CHOPs read it from there. Written for TD 2022.3+.
#
# Storage rather than a Table DAT because the frame is nested (the proximity
# matrix is a list of lists) and flattening it to text just to re-parse it
# downstream would cost more than it saves.

import json


def onConnect(dat):
    # Clear any stale frame so a reconnect can't serve data from last session.
    dat.store('frame', {})
    dat.store('connected', True)
    dat.store('frames_received', 0)
    return


def onDisconnect(dat):
    dat.store('connected', False)
    return


def onReceiveText(dat, rowIndex, message):
    try:
        frame = json.loads(message)
    except ValueError:
        # A partial or malformed frame is not worth stopping for — the next one
        # arrives in 40 ms. Keep the previous good frame in place.
        return

    dat.store('frame', frame)
    dat.store('frames_received', dat.fetch('frames_received', 0) + 1)

    # Force the dependent Script CHOPs to recook. Without this they only cook
    # when something else in the network asks them to, and the display lags.
    for name in ('orbs', 'proximity'):
        op_ = dat.parent().op(name)
        if op_ is not None:
            op_.cook(force=True)
    return


def onReceiveBinary(dat, contents):
    return


def onReceivePing(dat, contents):
    dat.sendPong(contents)      # keep the connection alive
    return


def onReceivePong(dat, contents):
    return


def onMonitorMessage(dat, message):
    return
