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

def raw_model(layout, ntsc=False, reverbs=None):
    """layout: string of 'm' (mono) / 's' (stereo), one char per audio track.
    reverbs: {track index: {Reverb parameter name: normalized value}} -> a Fairlight Reverb insert on that track."""
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
            if vid == 29 and reverbs and t in reverbs:          # insert active on this channel
                vals.append(struct.pack('<I', 1))
            elif vid == 170 and reverbs and t in reverbs and r != 'm':   # (stereo channels with an insert)
                vals.append(struct.pack('<I', 1))
            elif vid == 1112:
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
    if reverbs:
        out = _add_reverbs(out, layout, reverbs)
    struct.pack_into('<I', out, 8, len(out) - 12)
    return bytes(out)


MAGIC = b'f\x88fw'
REVERB_PARAMS = ['Room Height', 'Room Length', 'Room Width', 'Room Size', 'Room Ratio', 'Pre-Delay', 'Reverb Time',
                 'Distance', 'Brightness', 'Modulation', 'Early Refl. Low Freq', 'Early Refl. High Freq',
                 'Early Refl. Low Gain', 'Early Refl. High Gain', 'Rev. Tone Low Freq', 'Rev. Tone High Freq',
                 'Rev. Tone Low Gain', 'Rev. Tone High Gain', 'Dry/Wet', 'Direct Gain', 'Early Refl. Gain',
                 'Late Rev. Gain', 'Internal Bypass', 'Reverb:Macro 1', 'Reverb:Macro 2']


def _sections(x):
    """Top-level named sections of the model: [(offset, name, kind, chunk size)]; returns (list, end offset)."""
    o, out = 20, []
    while o + 48 <= len(x) and x[o + 36:o + 40] == MAGIC:
        size = struct.unpack_from('<I', x, o + 44)[0]
        out.append((o, x[o:o + 32].split(b'\0')[0].decode('latin1'), struct.unpack_from('<I', x, o + 32)[0], size))
        o += 48 + size
    return out, o


def _reverb_record(first_channel, values):
    """One Reverb instance for the EffectPresets section (from a Resolve 21 export), parameters set."""
    rec = bytearray(open(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                                      'fl_trackreverb.bin'), 'rb').read())
    rec[1] = first_channel & 0xFF
    o = 496                                  # parameter records: 64-byte name + float32 LE, normalized
    while o + 68 <= len(rec):
        name = rec[o:o + 64].split(b'\0')[0].decode()
        if not name:
            break
        if name in values:
            struct.pack_into('<f', rec, o + 64, max(0.0, min(1.0, float(values[name]))))
        o += 68
    return bytes(rec)


def _add_reverbs(out, layout, reverbs):
    """Add Fairlight Reverb inserts (slot 1) to the model. Five places, calibrated against Resolve 21.1 exports
    with zero, one and two Reverb inserts: EffectContext (one entry per instance, listing the parameter ids),
    a new EffectPresets section (one record per instance, keyed by the track's first channel), the effect and
    parameter name tables, per-channel insert flags (vectors 29/170, done in raw_model) and the plugin slot table."""
    x = bytes(out)
    first, c = [], 0
    for fmt in layout:
        first.append(c); c += 1 if fmt == 'm' else 2
    tracks = sorted(t for t in reverbs if 0 <= t < len(layout))
    if not tracks:
        return out
    n = len(tracks)
    nouts = 2 if any(layout[t] == 's' for t in tracks) else 1
    # parameter name table: ids continue after the highest existing one
    secs, end = _sections(x)
    pt = x.find(struct.pack('<II', 0x532, 9) + b'Preview 1', end) - 4
    cnt = struct.unpack_from('<I', x, pt)[0]
    o, maxid = pt + 4, 0
    for _ in range(cnt):
        i, l = struct.unpack_from('<II', x, o); maxid = max(maxid, i); o += 8 + l
    pt_end = o
    nid = maxid + 1
    names = ['Out %d' % (k + 1) for k in range(nouts)] + REVERB_PARAMS
    ids = list(range(nid, nid + len(names)))
    pids = ids[nouts:]
    name_tab = b''.join(struct.pack('<II', i, len(nm)) + nm.encode() for i, nm in zip(ids, names))
    # effect name table (Previewer, Generator, ... DolbyAtmosRenderer): keys 0x1b..; add Reverb, Reverb:2, ...
    et = x.find(struct.pack('<II', 0x1b, 9) + b'Previewer', end) - 4
    ecnt = struct.unpack_from('<I', x, et)[0]
    o, maxkey = et + 4, 0
    for _ in range(ecnt):
        k, l = struct.unpack_from('<II', x, o); maxkey = max(maxkey, k); o += 8 + l
    et_end = o
    keys = list(range(maxkey + 1, maxkey + 1 + n))
    enames = ['Reverb' if j == 0 else 'Reverb:%d' % (j + 1) for j in range(n)]
    eff_tab = b''.join(struct.pack('<II', k, len(nm)) + nm.encode() for k, nm in zip(keys, enames))
    # plugin slot table (pid 0x4a4, 100 slots): first slot in use
    st = x.find(bytes.fromhex('01000000a404000064000000'), end)
    # tail rewrite (offsets after `end`, in increasing order: effect names, parameter names, slot table)
    y = bytearray(x)
    struct.pack_into('<I', y, st + 12, 1)
    y[pt_end:pt_end] = name_tab
    struct.pack_into('<I', y, pt, cnt + len(names))
    y[et_end:et_end] = eff_tab
    struct.pack_into('<I', y, et, ecnt + n)
    # sections: EffectContext gets one entry per instance, EffectPresets is inserted right after it
    ctx = next(s for s in secs if s[1] == 'EffectContext')
    co, csz = ctx[0], ctx[3]
    body = bytes(y[co + 48:co + 48 + csz])
    ccnt = struct.unpack_from('<I', body, 0)[0]
    body = struct.pack('<I', ccnt + n) + body[4:] + b''.join(
        struct.pack('<II', k, len(pids)) + struct.pack('<%dI' % len(pids), *pids) for k in keys)
    ctx_sec = bytes(y[co:co + 44]) + struct.pack('<I', len(body)) + body
    recs = b''.join(_reverb_record(first[t], reverbs[t]) for t in tracks)
    pchunk = struct.pack('<II', 5, n) + recs
    pre_sec = (b'EffectPresets'.ljust(32, b'\0') + struct.pack('<I', 2) + MAGIC + struct.pack('<II', 1, len(pchunk))
               + pchunk)
    if any(s[1] == 'EffectPresets' for s in secs):
        raise ValueError('model already has EffectPresets')
    y[co:co + 48 + csz] = ctx_sec + pre_sec
    return y


def qt_blob(layout, ntsc=False, reverbs=None):
    """FLStudioModelBA bytes as stored in the Sm2Sequence FieldsBlob (qCompress format)."""
    raw = raw_model(layout, ntsc, reverbs)
    return struct.pack('>I', len(raw)) + zlib.compress(raw, 9)
