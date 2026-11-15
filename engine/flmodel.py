"""Generate a Fairlight mixer model (FLStudioModelBA) for a list of audio track formats.

Reverse-engineered from Resolve's own exports: the model is a fixed body plus 491
per-channel parameter vectors ([1][param id][n channels][n x 4 bytes]). Mono, stereo-L and
stereo-R channels each have their own default values; param 1112 holds the owning track
index for the first channel of a track (-1 for stereo R). A few global fields hold the
track count and the first channel of track 2.
"""
import base64, json, os, struct, sys, zlib

_DATA = None
def _data():
    global _DATA
    if _DATA is None:
        d = json.load(open(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'fl_base.json')))
        d['nv'] = zlib.decompress(base64.b64decode(d['nv']))
        d['pm'] = [bytes.fromhex(x) for x in d['pm']]
        d['ps'] = [(bytes.fromhex(a), bytes.fromhex(b)) for a, b in d['ps']]
        _DATA = d
    return _DATA

def _channels(layout):
    ch = []
    for t, c in enumerate(layout):
        ch += [(t, 'm')] if c == 'm' else [(t, 'L'), (t, 'R')]
    return ch

def raw_model(layout, ntsc=False):
    """layout: string of 'm' (mono) / 's' (stereo), one char per audio track."""
    if not layout:
        layout = 's'
    d = _data()
    nv = bytearray(d['nv'])
    ntr = len(layout); ch = _channels(layout); nch = len(ch)
    nv[300379] = ntr + 1
    nv[302407] = ntr + 1
    nv[302423] = ntr
    nv[302631] = (1 if layout[0] == 'm' else 2) if ntr > 1 else 0
    nv[303319] = ntr
    nv[303995] = min(255, 11 + 2 * nch)
    out = bytearray(); prev = 0
    for k, p in enumerate(d['pos']):
        out += nv[prev:p]
        vid = d['vids'][k]
        vals = []
        for t, r in ch:
            if vid == 1112:
                vals.append(struct.pack('<i', -1 if r == 'R' else t))
            elif r == 'm':
                vals.append(d['pm'][k])
            else:
                vals.append(d['ps'][k][0 if r == 'L' else 1])
        out += struct.pack('<I', nch) + b''.join(vals)
        prev = p
    out += nv[prev:]
    if ntsc:   # 23.976 / 29.97: Resolve stores a different value in three global fields
        out = bytearray(bytes(out).replace(bytes.fromhex('0074c13d7d'), bytes.fromhex('80244c72ab')))
    struct.pack_into('<I', out, 8, len(out) - 12)
    return bytes(out)

def qt_blob(layout, ntsc=False):
    """FLStudioModelBA bytes as stored in the Sm2Sequence FieldsBlob (qCompress format)."""
    raw = raw_model(layout, ntsc)
    return struct.pack('>I', len(raw)) + zlib.compress(raw, 9)
