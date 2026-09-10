# Script CHOP — turns the stored curated frame into one channel set per orb.
# Name this operator `orbs` so websocket_callbacks.py can force it to recook.
#
# Expects a WebSocket DAT named `websocket1` as a sibling, running
# websocket_callbacks.py against ws://ORBSTATION:8080/ws
#
# Output: one sample per channel, channels named by orb serial, e.g.
#   a1b2c3:accelx  a1b2c3:accely  a1b2c3:accelz
#   a1b2c3:spin    a1b2c3:shake   a1b2c3:tilt
#   a1b2c3:cluster a1b2c3:hue     a1b2c3:battery  a1b2c3:charging
# plus fleet-wide: count, rate, activity, clusters
#
# Serials are stable across reboots, so channel names stay put and your
# references don't break between sessions.

SOURCE = 'websocket1'

# (suffix, how to get it from an orb dict)
FIELDS = [
    ('accelx',   lambda o: o['accel'][0]),
    ('accely',   lambda o: o['accel'][1]),
    ('accelz',   lambda o: o['accel'][2]),
    ('gyrox',    lambda o: o['gyro'][0]),
    ('gyroy',    lambda o: o['gyro'][1]),
    ('gyroz',    lambda o: o['gyro'][2]),
    ('spin',     lambda o: o['spin']),
    ('shake',    lambda o: o['shake']),
    ('tilt',     lambda o: o['tilt']),
    ('cluster',  lambda o: o['cluster']),
    ('hue',      lambda o: o['hue']),
    ('battery',  lambda o: o['battery_v']),
    ('charging', lambda o: 1.0 if o['charging'] else 0.0),
    ('rssi',     lambda o: o['rssi']),
]


def onSetupParameters(scriptOp):
    return


def onCook(scriptOp):
    scriptOp.clear()
    scriptOp.numSamples = 1

    src = scriptOp.parent().op(SOURCE)
    frame = src.fetch('frame', {}) if src is not None else {}
    orbs = frame.get('orbs', [])

    for o in orbs:
        for suffix, get in FIELDS:
            try:
                value = float(get(o))
            except (KeyError, TypeError, ValueError, IndexError):
                # A field the station didn't send this frame (server modes
                # differ) becomes 0 rather than breaking the whole cook.
                value = 0.0
            scriptOp.appendChan('%s:%s' % (o['id'], suffix))[0] = value

    scriptOp.appendChan('count')[0] = float(frame.get('orb_count', 0))
    scriptOp.appendChan('rate')[0] = float(frame.get('rate_hz', 0.0))
    scriptOp.appendChan('activity')[0] = float(frame.get('activity', 0.0))
    scriptOp.appendChan('clusters')[0] = float(len(frame.get('clusters', [])))
    return
