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

def raw_model(layout, ntsc=False, reverbs=None, inserts=None):
    """layout: string of 'm' (mono) / 's' (stereo), one char per audio track.
    reverbs: {track index: {Reverb parameter name: normalized value}} -> a Fairlight Reverb insert on that track.
    inserts: {track index: reverb_insert(...) / plugin_insert(...)} -> any insert (slot 1) on that track."""
    inserts = dict(inserts or {})
    for t, v in (reverbs or {}).items():
        inserts[t] = reverb_insert(v)
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
            if vid == 29 and t in inserts:          # insert active on this channel
                vals.append(struct.pack('<I', 1))
            elif vid == 170 and t in inserts and r != 'm':   # (stereo channels with an insert)
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
    if inserts:
        out = _add_inserts(out, layout, inserts)
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


def _plugin_record(first_channel, ident, display, payload):
    """Third-party plugin instance (AU 'au:<manufacturer>:<subtype>' or VST3 'vst3:<class id>') for EffectPresets:
    record header from a Resolve 21 export (fl_aurecord.bin) + the plugin's own state (may be empty = defaults)."""
    hdr = bytearray(open(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                                      'fl_aurecord.bin'), 'rb').read())
    struct.pack_into('<III', hdr, 0, (first_channel & 0xFF) << 8, len(payload), 1)   # channel sits in byte 1
    hdr[12:268] = ident.encode()[:255].ljust(256, b'\0')
    hdr[384:424] = display.encode('utf-8', 'replace')[:39].ljust(40, b'\0')
    return bytes(hdr) + payload


def _add_inserts(out, layout, inserts):
    """Add plugin inserts (slot 1) to the model. Five places, calibrated against Resolve 21.1 exports with zero,
    one and two Reverb inserts and one third-party (Soundly Place it) insert: EffectContext (one entry per
    instance, listing the parameter ids of its plugin type), a new EffectPresets section (one record per instance,
    keyed by the track's first channel), the effect and parameter name tables (ids are shared per plugin type),
    per-channel insert flags (vectors 29/170, done in raw_model) and the plugin slot table.
    inserts: {track: {'type': key, 'display': name, 'record': fn(first_channel) -> bytes,
                      'io': [names], 'params': [names]}}"""
    x = bytes(out)
    first, c = [], 0
    for fmt in layout:
        first.append(c); c += 1 if fmt == 'm' else 2
    tracks = sorted(t for t in inserts if 0 <= t < len(layout))
    if not tracks:
        return out
    n = len(tracks)
    secs, end = _sections(x)
    # parameter name table: ids continue after the highest existing one; one block per plugin type
    pt = x.find(struct.pack('<II', 0x532, 9) + b'Preview 1', end) - 4
    cnt = struct.unpack_from('<I', x, pt)[0]
    o, maxid = pt + 4, 0
    for _ in range(cnt):
        i, l = struct.unpack_from('<II', x, o); maxid = max(maxid, i); o += 8 + l
    pt_end = o
    nid = maxid + 1
    type_pids, names_all = {}, []
    for t in tracks:
        sp = inserts[t]
        if sp['type'] in type_pids:
            continue
        io = sp['io'](layout, [u for u in tracks if inserts[u]['type'] == sp['type']])
        names = io + list(sp['params'])
        ids = list(range(nid, nid + len(names))); nid += len(names)
        type_pids[sp['type']] = ids[len(io):]
        names_all += list(zip(ids, names))
    name_tab = b''.join(struct.pack('<II', i, len(nm.encode())) + nm.encode() for i, nm in names_all)
    # effect name table (Previewer, Generator, ... DolbyAtmosRenderer): keys 0x1b..; add 'Reverb', 'Reverb:2', ...
    et = x.find(struct.pack('<II', 0x1b, 9) + b'Previewer', end) - 4
    ecnt = struct.unpack_from('<I', x, et)[0]
    o, maxkey = et + 4, 0
    for _ in range(ecnt):
        k, l = struct.unpack_from('<II', x, o); maxkey = max(maxkey, k); o += 8 + l
    et_end = o
    keys = list(range(maxkey + 1, maxkey + 1 + n))
    seen, enames = {}, []
    for t in tracks:
        d = inserts[t]['display']; seen[d] = seen.get(d, 0) + 1
        enames.append(d if seen[d] == 1 else '%s:%d' % (d, seen[d]))
    eff_tab = b''.join(struct.pack('<II', k, len(nm.encode())) + nm.encode() for k, nm in zip(keys, enames))
    # plugin slot table (pid 0x4a4, 100 slots): first slot in use
    st = x.find(bytes.fromhex('01000000a404000064000000'), end)
    # tail rewrite (offsets after `end`, in increasing order: effect names, parameter names, slot table)
    y = bytearray(x)
    struct.pack_into('<I', y, st + 12, 1)
    y[pt_end:pt_end] = name_tab
    struct.pack_into('<I', y, pt, cnt + len(names_all))
    y[et_end:et_end] = eff_tab
    struct.pack_into('<I', y, et, ecnt + n)
    # sections: EffectContext gets one entry per instance, EffectPresets is inserted right after it
    ctx = next(s for s in secs if s[1] == 'EffectContext')
    co, csz = ctx[0], ctx[3]
    body = bytes(y[co + 48:co + 48 + csz])
    ccnt = struct.unpack_from('<I', body, 0)[0]
    entries = b''
    for k, t in zip(keys, tracks):
        pids = type_pids[inserts[t]['type']]
        entries += struct.pack('<II', k, len(pids)) + struct.pack('<%dI' % len(pids), *pids)
    body = struct.pack('<I', ccnt + n) + body[4:] + entries
    ctx_sec = bytes(y[co:co + 44]) + struct.pack('<I', len(body)) + body
    recs = b''.join(inserts[t]['record'](first[t]) for t in tracks)
    pchunk = struct.pack('<II', 5, n) + recs
    pre_sec = (b'EffectPresets'.ljust(32, b'\0') + struct.pack('<I', 2) + MAGIC + struct.pack('<II', 1, len(pchunk))
               + pchunk)
    if any(s[1] == 'EffectPresets' for s in secs):
        raise ValueError('model already has EffectPresets')
    y[co:co + 48 + csz] = ctx_sec + pre_sec
    return y


def reverb_insert(values):
    return {'type': 'bmd:Reverb', 'display': 'Reverb', 'params': REVERB_PARAMS,
            'io': lambda layout, ts: ['Out %d' % (k + 1) for k in range(2 if any(layout[t] == 's' for t in ts) else 1)],
            'record': lambda ch: _reverb_record(ch, values)}


def juce_vst3_state(vc2=None):
    """VST3 state of a JUCE plugin as Resolve keeps it, around a JUCE state ('VC2!' + length + XML). Without a
    state a neutral one is used: the plugin loads with its default settings (an empty state makes Resolve drop
    the insert). Layout from a Resolve 21.1 export (fl_vst3juce.bin): [0][length - 16][..] VC2 JUCEPrivateData."""
    p = open(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'fl_vst3juce.bin'),
             'rb').read()
    if vc2 is None:
        xml = b'<?xml version="1.0" encoding="UTF-8"?> <DEFAULT/>\0'
        vc2 = b'VC2!' + struct.pack('<I', len(xml)) + xml
    i = p.find(b'VC2!'); n = struct.unpack_from('<I', p, i + 4)[0]
    tail = p[i + 8 + n:]
    total = i + len(vc2) + len(tail)
    return p[:4] + struct.pack('<I', total - 16) + p[8:i] + vc2 + tail


def juce_vst3_id(manufacturer, product):
    """VST3 class id JUCE gives a plugin: ABCDEF01 9182FAEB + manufacturer code + plugin code (the same numbers
    Avid stores as AAX manufacturer and product id)."""
    return 'vst3:ABCDEF019182FAEB%08X%08X' % (int(manufacturer) & 0xFFFFFFFF, int(product) & 0xFFFFFFFF)


def plugin_insert(ident, display, payload=b'', params=(), io=None):
    """Third-party plugin insert. params: its parameter names if known (Resolve fills them in when the plugin
    loads otherwise). io: names of its inputs/outputs ('In M', 'Out M' for a mono track)."""
    return {'type': ident, 'display': display, 'params': list(params),
            'io': (lambda layout, ts: list(io)) if io is not None else (lambda layout, ts: []),
            'record': lambda ch: _plugin_record(ch, ident, display, payload)}


def qt_blob(layout, ntsc=False, reverbs=None, inserts=None):
    """FLStudioModelBA bytes as stored in the Sm2Sequence FieldsBlob (qCompress format)."""
    raw = raw_model(layout, ntsc, reverbs, inserts)
    return struct.pack('>I', len(raw)) + zlib.compress(raw, 9)
