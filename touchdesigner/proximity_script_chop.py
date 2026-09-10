# Script CHOP — the inter-orb proximity graph, two ways.
# Name this operator `proximity` so websocket_callbacks.py can force a recook.
#
# Expects a WebSocket DAT named `websocket1` as a sibling (see
# websocket_callbacks.py) connected to ws://ORBSTATION:8080/ws
#
# Output A — dense matrix, one channel per row, N samples per channel:
#     row0 row1 row2 ...        values 0..1  (row/col order == `ids` below)
#   Feed this straight into a CHOP to TOP for a heatmap, or read cell [i][j]
#   as row<i>[j].
#
# Output B — edge list, ranked strongest-first, fixed channel count:
#     edge0:a  edge0:b  edge0:strength ...
#   where a/b are the *indices* into `ids`. Fixed size (MAX_EDGES) so your
#   downstream network never restructures as orbs come and go — unused slots
#   read strength 0.
#
# The orb serial for index i is stored on this CHOP as 'ids' — read it with
#   op('proximity').fetch('ids')

SOURCE = 'websocket1'
MAX_EDGES = 32          # 6 orbs => 15 pairs; 8 orbs => 28. Raise for a bigger fleet.


def onSetupParameters(scriptOp):
    return


def onCook(scriptOp):
    scriptOp.clear()

    src = scriptOp.parent().op(SOURCE)
    frame = src.fetch('frame', {}) if src is not None else {}
    prox = frame.get('proximity', {})
    ids = prox.get('ids', [])
    matrix = prox.get('matrix', [])
    edges = prox.get('edges', [])

    # Publish the index -> serial mapping so downstream code can name things.
    scriptOp.store('ids', ids)

    n = len(ids)
    # A CHOP has ONE sample count for all its channels, and the two outputs
    # want different lengths (matrix rows want n, the edge list wants
    # MAX_EDGES). Take the larger and zero-pad the shorter — that also keeps
    # the sample count constant as orbs join and leave, so nothing downstream
    # restructures mid-session.
    scriptOp.numSamples = max(1, n, MAX_EDGES)

    # --- A: dense matrix, normalised 0..1 -------------------------------
    for i in range(n):
        chan = scriptOp.appendChan('row%d' % i)
        row = matrix[i] if i < len(matrix) else []
        for j in range(n):
            chan[j] = (row[j] / 255.0) if j < len(row) else 0.0

    # --- B: fixed-width edge list ---------------------------------------
    index_of = {s: i for i, s in enumerate(ids)}
    ca = scriptOp.appendChan('edge:a')
    cb = scriptOp.appendChan('edge:b')
    cs = scriptOp.appendChan('edge:strength')
    for k in range(MAX_EDGES):
        if k < len(edges):
            e = edges[k]
            ca[k] = float(index_of.get(e['a'], -1))
            cb[k] = float(index_of.get(e['b'], -1))
            cs[k] = float(e['norm'])
        else:
            ca[k] = -1.0
            cb[k] = -1.0
            cs[k] = 0.0
    return
