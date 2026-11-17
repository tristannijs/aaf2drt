"""Generate a Fairlight mixer model (FLStudioModelBA) for a list of audio track formats.

Reverse-engineered from Resolve's own exports: the model is a fixed body plus 491
per-channel parameter vectors ([1][param id][n channels][n x 4 bytes]). Mono, stereo-L and
stereo-R channels each have their own default values; param 1112 holds the owning track
index for the first channel of a track (-1 for stereo R). A few global fields hold the
track count and the first channel of track 2.
"""
import base64, json, math, os, struct, sys, zlib

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

MAX_INSERTS = 6          # Fairlight insert slots per track (Resolve 21.1 shows no more)


# Fairlight channel EQ (6 bands), per-channel vectors as Resolve 21.1 writes them (calibrated by changing one
# setting at a time in the mixer and exporting). Band i (0..5) uses vector base + i.
EQ_TOUCHED, EQ_ON, EQ_GAIN = 170, 223, 224           # 170 becomes 1 once the EQ is edited; gain in dB x 10
EQ_BAND_GAIN, EQ_BAND_Q, EQ_BAND_FREQ, EQ_BAND_TYPE, EQ_BAND_ON = 225, 233, 241, 249, 257
EQ_BAND_RANGE, EQ_BAND_KNOB, EQ_BAND_SLOPE = 648, 820, 1214
EQ_TYPES = {'low_shelf': 1, 'bell': 2, 'high_shelf': 3, 'notch': 4, 'lpf': 5, 'hpf': 6}
EQ_RANGES = [(30, 399), (100, 1500), (450, 8000), (1400, 22000)]     # L, ML, MH, H (Hz)
EQ_SLOPES = [6, 12, 18, 24]                                         # dB/oct, high- and low-pass


def channel_eq(bands, gain_db=0.0):
    """Fairlight channel EQ -> {vector id: int}. bands: 6 dicts {on, freq, gain (dB), q, type, slope (dB/oct)};
    band 1 is the high-pass (30-399 Hz), band 6 the low-pass (1.4-22 kHz), bands 2-5 take type
    low_shelf / bell / high_shelf / notch. Values outside Fairlight's ranges are clamped."""
    v = {EQ_TOUCHED: 1, EQ_ON: 1, EQ_GAIN: int(round(max(-20.0, min(20.0, gain_db)) * 10))}
    for i, b in enumerate(bands[:6]):
        f = float(b.get('freq') or (30 if i == 0 else 22000 if i == 5 else 1000))
        if i == 0:
            rng = 0
        elif i == 5:
            rng = 3
        else:
            fits = [k for k, (lo, hi) in enumerate(EQ_RANGES) if lo <= f <= hi] or [0 if f < 30 else 3]
            rng = min(fits, key=lambda k: abs(math.log(f / math.sqrt(EQ_RANGES[k][0] * EQ_RANGES[k][1]))))
        lo, hi = EQ_RANGES[rng]
        f = max(lo, min(hi, f))
        v[EQ_BAND_FREQ + i] = int(round(f))
        v[EQ_BAND_RANGE + i] = rng
        v[EQ_BAND_KNOB + i] = int(round(256 * math.log(f / lo) / math.log(hi / lo)))
        v[EQ_BAND_ON + i] = 1 if b.get('on') else 0
        v[EQ_BAND_TYPE + i] = 6 if i == 0 else 5 if i == 5 else EQ_TYPES[b.get('type') or 'bell']
        if i in (0, 5):
            sl = b.get('slope') or 12
            v[EQ_BAND_SLOPE + i] = min(range(4), key=lambda k: abs(EQ_SLOPES[k] - sl))
        else:
            v[EQ_BAND_GAIN + i] = int(round(max(-20.0, min(20.0, b.get('gain') or 0.0)) * 10))
            q = max(0.3, min(10.3, b.get('q') or 1.0))
            v[EQ_BAND_Q + i] = int(math.floor(q * 15 + 10.5 + 1e-9))
    return v


def raw_model(layout, ntsc=False, reverbs=None, inserts=None, eqs=None):
    """layout: string of 'm' (mono) / 's' (stereo), one char per audio track.
    reverbs: {track index: {Reverb parameter name: normalized value}} -> a Fairlight Reverb insert on that track.
    inserts: {track index: [reverb_insert(...) / plugin_insert(...), ...]} -> inserts on that track, slot 1, 2, ...
    eqs: {track index: channel_eq(...)} -> the channel EQ of that track."""
    eqs = eqs or {}
    inserts = {t: (list(v) if isinstance(v, (list, tuple)) else [v]) for t, v in (inserts or {}).items()}
    for t, v in (reverbs or {}).items():
        inserts.setdefault(t, []).append(reverb_insert(v))
    inserts = {t: v for t, v in inserts.items() if v}
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
            if t in eqs and vid in eqs[t]:
                vals.append(struct.pack('<i', eqs[t][vid]))
            elif vid == 29 and t in inserts:          # insert active on this channel
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


def _reverb_record(first_channel, values, slot=0):
    """One Reverb instance for the EffectPresets section (from a Resolve 21 export), parameters set."""
    rec = bytearray(open(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                                      'fl_trackreverb.bin'), 'rb').read())
    rec[1] = first_channel & 0xFF
    rec[3] = slot & 0xFF
    o = 496                                  # parameter records: 64-byte name + float32 LE, normalized
    while o + 68 <= len(rec):
        name = rec[o:o + 64].split(b'\0')[0].decode()
        if not name:
            break
        if name in values:
            struct.pack_into('<f', rec, o + 64, max(0.0, min(1.0, float(values[name]))))
        o += 68
    return bytes(rec)


def _plugin_record(first_channel, ident, display, payload, slot=0):
    """Third-party plugin instance (AU 'au:<manufacturer>:<subtype>' or VST3 'vst3:<class id>') for EffectPresets:
    record header from a Resolve 21 export (fl_aurecord.bin) + the plugin's own state (may be empty = defaults)."""
    hdr = bytearray(open(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))),
                                      'fl_aurecord.bin'), 'rb').read())
    struct.pack_into('<III', hdr, 0, (first_channel & 0xFF) << 8 | (slot & 0xFF) << 24, len(payload), 1)  # byte 1 channel, 3 slot
    hdr[12:268] = ident.encode()[:255].ljust(256, b'\0')
    hdr[384:424] = display.encode('utf-8', 'replace')[:39].ljust(40, b'\0')
    return bytes(hdr) + payload


def _add_inserts(out, layout, inserts):
    """Add plugin inserts to the model. Five places, calibrated against Resolve 21.1 exports: EffectContext (one
    entry per instance, listing the parameter ids of its plugin type), a new EffectPresets section (one record per
    instance: byte 1 the track's first channel, byte 3 the insert slot), the effect and parameter name tables (ids
    are shared per plugin type), per-channel insert flags (vectors 29/170, done in raw_model) and the plugin slot
    table. inserts: {track: [spec, ...]} in slot order, spec = {'type': key, 'display': name,
    'record': fn(first_channel, slot) -> bytes, 'io': fn(layout, tracks) -> [names], 'params': [names]}"""
    x = bytes(out)
    first, c = [], 0
    for fmt in layout:
        first.append(c); c += 1 if fmt == 'm' else 2
    inst = [(t, k, sp) for t in sorted(inserts) if 0 <= t < len(layout)
            for k, sp in enumerate(inserts[t][:MAX_INSERTS])]
    if not inst:
        return out
    n = len(inst)
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
    for t, k, sp in inst:
        if sp['type'] in type_pids:
            continue
        io = sp['io'](layout, sorted({u for u, _, q in inst if q['type'] == sp['type']}))
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
        kk, l = struct.unpack_from('<II', x, o); maxkey = max(maxkey, kk); o += 8 + l
    et_end = o
    keys = list(range(maxkey + 1, maxkey + 1 + n))
    seen, enames = {}, []
    for t, k, sp in inst:
        d = sp['display']; seen[d] = seen.get(d, 0) + 1
        enames.append(d if seen[d] == 1 else '%s:%d' % (d, seen[d]))
    eff_tab = b''.join(struct.pack('<II', kk, len(nm.encode())) + nm.encode() for kk, nm in zip(keys, enames))
    # plugin slot table (pid 0x4a4, 100 slots): the slots in use
    st = x.find(bytes.fromhex('01000000a404000064000000'), end)
    y = bytearray(x)
    for k in sorted({k for _, k, _ in inst}):
        struct.pack_into('<I', y, st + 12 + 4 * k, 1)
    # tail rewrite (offsets after `end`, in increasing order: effect names, parameter names)
    y[pt_end:pt_end] = name_tab
    struct.pack_into('<I', y, pt, cnt + len(names_all))
    y[et_end:et_end] = eff_tab
    struct.pack_into('<I', y, et, ecnt + n)
    # sections: EffectContext gets one entry per instance, EffectPresets is inserted right after it
    ctx = next(sc for sc in secs if sc[1] == 'EffectContext')
    co, csz = ctx[0], ctx[3]
    body = bytes(y[co + 48:co + 48 + csz])
    ccnt = struct.unpack_from('<I', body, 0)[0]
    entries = b''
    for kk, (t, k, sp) in zip(keys, inst):
        pids = type_pids[sp['type']]
        entries += struct.pack('<II', kk, len(pids)) + struct.pack('<%dI' % len(pids), *pids)
    body = struct.pack('<I', ccnt + n) + body[4:] + entries
    ctx_sec = bytes(y[co:co + 44]) + struct.pack('<I', len(body)) + body
    recs = b''.join(sp['record'](first[t], k) for t, k, sp in inst)
    pchunk = struct.pack('<II', 5, n) + recs
    pre_sec = (b'EffectPresets'.ljust(32, b'\0') + struct.pack('<I', 2) + MAGIC + struct.pack('<II', 1, len(pchunk))
               + pchunk)
    if any(sc[1] == 'EffectPresets' for sc in secs):
        raise ValueError('model already has EffectPresets')
    y[co:co + 48 + csz] = ctx_sec + pre_sec
    return y


def reverb_insert(values):
    return {'type': 'bmd:Reverb', 'display': 'Reverb', 'params': REVERB_PARAMS,
            'io': lambda layout, ts: ['Out %d' % (k + 1) for k in range(2 if any(layout[t] == 's' for t in ts) else 1)],
            'record': lambda ch, slot=0: _reverb_record(ch, values, slot)}


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
            'record': lambda ch, slot=0: _plugin_record(ch, ident, display, payload, slot)}


def qt_blob(layout, ntsc=False, reverbs=None, inserts=None, eqs=None):
    """FLStudioModelBA bytes as stored in the Sm2Sequence FieldsBlob (qCompress format)."""
    raw = raw_model(layout, ntsc, reverbs, inserts, eqs)
    return struct.pack('>I', len(raw)) + zlib.compress(raw, 9)
