"""Flatten an Avid AAF composition into a timeline model (tracks -> items) with resolved media."""
import aaf2, math, urllib.parse
from fractions import Fraction


def frac(r):
    try:
        return Fraction(int(r.numerator), int(r.denominator))
    except AttributeError:
        return Fraction(str(r))


def file_url(mob):
    try:
        for loc in mob.descriptor['Locator']:
            return loc['URLString'].value
    except Exception:
        return None


def is_file_mob(mob):
    return type(mob).__name__ == 'SourceMob' and type(mob.descriptor).__name__ in (
        'WAVEDescriptor', 'PCMDescriptor', 'AIFCDescriptor', 'CDCIDescriptor', 'RGBADescriptor', 'MultipleDescriptor')


def segment_at(seg, pos):
    """Return (component, offset_into_component) for position pos (edit units) in a segment."""
    n = type(seg).__name__
    if n == 'Sequence':
        p = 0
        comps = list(seg.components)
        for c in comps:
            if type(c).__name__ == 'Transition':
                p -= c.length
                continue
            ln = c.length or 0
            if p <= pos < p + ln:
                return segment_at(c, pos - p)
            p += ln
        return None, 0
    if n == 'OperationGroup':
        segs = list(c for c in seg.segments)
        return segment_at(segs[0], pos) if segs else (None, 0)
    if n == 'NestedScope':
        sl = list(seg.slots)
        return segment_at(sl[-1], pos) if sl else (None, 0)
    if n == 'EssenceGroup':
        try:
            for ch in seg['Choices'].value:
                if type(ch).__name__ == 'SourceClip' and ch.mob is not None:
                    return ch, pos
        except Exception:
            pass
        return None, 0
    return seg, pos


def resolve_source(clip, ctx_rate=None):
    """Follow a SourceClip down to a file SourceMob.
    Returns dict(mob, slot_id, pos_seconds, rate, chain) or None.  pos = position (s) in the file mob's slot.

    AAF: a SourceClip's StartTime is in edit units of the slot that CONTAINS the clip (ctx_rate), not of
    the slot it references. Avid mixes rates (25 fps sequence -> 100 Hz subclip/master sound slots ->
    25 fps file mob), so every step is converted through seconds.  ctx_rate = edit rate of the track the
    clip sits on; without it the referenced slot's rate is assumed (only right when rates are equal)."""
    mob = clip.mob
    slot_id = clip.slot_id
    start = Fraction(clip.start or 0)
    chain = []
    for _ in range(12):
        if mob is None:
            return None
        try:
            slot = mob.slot_at(slot_id)
        except Exception:
            slot = next((s for s in mob.slots if s.slot_id == slot_id), None)
        if slot is None:
            return None
        rate = frac(slot.edit_rate)
        sec = start / (ctx_rate or rate)          # position in this slot, in seconds
        pos = sec * rate                          # ... and in this slot's edit units
        chain.append((mob, slot_id, pos, rate))
        if is_file_mob(mob):
            return {'mob': mob, 'slot_id': slot_id, 'pos': sec, 'rate': rate, 'chain': chain}
        comp, off = segment_at(slot.segment, int(pos))
        if comp is None or type(comp).__name__ != 'SourceClip' or comp.mob is None:
            return None
        # comp.start is in this slot's units (it contains comp); add the offset into comp, same units
        start = Fraction(comp.start or 0) + off + (pos - int(pos))
        ctx_rate = rate
        mob = comp.mob
        slot_id = comp.slot_id
    return None


def op_params(og):
    out = {'op': og.operation.name if og.operation else None, 'params': {}}
    for pr in og.parameters:
        try:
            if type(pr).__name__ == 'VaryingValue':
                out['params'][pr.name] = [(float(cp.time), cp.value) for cp in pr.pointlist]
            else:
                out['params'][pr.name] = pr.value
        except Exception:
            pass
    if out['op'] == 'Audio Suite Plugin':      # real-time AudioSuite on a clip: keep the plugin state
        try:
            for t in og.get('ComponentAttributeList') or []:
                if t.name in ('_EFFECT_PLUGIN_NAME', '_AUDIO_SUITE_EFFECT_0_ALL_CHUNKS'):
                    out[t.name] = t.value
        except Exception:
            pass
    return out


def eq3_bands(chunk):
    """Avid EQ3 7-Band AudioSuite chunk -> dict of named settings (d_ = double, l_ = long).
    Records: 8 bytes ASCII name (e.g. 'd_frq2'), then a big-endian double (d_, 8 bytes) or int32 (l_, 4 bytes)."""
    import struct as _st
    b = bytes(chunk)
    i = b.find(b'l_hak3')
    out = {}
    while i >= 0 and i + 12 <= len(b):
        name = b[i:i + 8].split(b'\0')[0].decode('ascii', 'replace')
        if not (len(name) >= 3 and name[1] == '_' and name[0] in 'dl'):
            break
        if name.startswith('d_'):
            out[name] = _st.unpack('>d', b[i + 8:i + 16])[0]; i += 16
        else:
            out[name] = _st.unpack('>i', b[i + 8:i + 12])[0]; i += 12
    return out


DVERB_ALGOS = ['Hall', 'Church', 'Plate', 'Room 1', 'Room 2', 'Ambient', 'Non-Lin']
DVERB_SIZES = ['Small', 'Medium', 'Large']


def track_plugin(g):
    """Plugin inserted on an Avid audio track (inside an 'Audio Track Effect Container'), or None."""
    while type(g).__name__ == 'OperationGroup':
        attrs = {}
        try:
            for t in g.get('ComponentAttributeList') or []:
                attrs[t.name] = t.value
        except Exception:
            pass
        if attrs.get('_EFFECT_PLUGIN_NAME'):
            return {'name': attrs['_EFFECT_PLUGIN_NAME'], 'chunk': attrs.get('_ATN_AUDIO_RTAS_EFFECT_CHUNK'),
                    'insert': attrs.get('INSERT')}
        segs = list(g.segments)
        g = segs[0] if segs else None
    return None


def dverb_params(chunk):
    """Avid D-Verb AudioSuite chunk -> settings. Ten big-endian int32 knob values from offset 56, each a
    position -1..1 (int / 2^31). Calibrated with six exports plus reading the dialog:
    algorithm, size, input, mix, diffusion, decay, HF cut, LP filter, pre-delay, (unused)."""
    import struct as _st
    b = bytes(chunk)
    if len(b) < 96 or b[8:16] != b'igiDbrvR':
        return None
    x = [(_st.unpack('>i', b[56 + 4 * k:60 + 4 * k])[0] / 2 ** 31 + 1) / 2 for k in range(10)]
    algo = min(6, max(0, int(round(x[0] * 6))))
    size = min(2, max(0, int(round(x[1] * 2))))
    pd = x[8] * 100 if x[8] <= 0.5 else 50 + (x[8] - 0.5) * 500
    base = [0.2, 0.2, 0.101, 0.101, 0.101, 0.051, 0.101][algo] * (1, 1.5, 2)[size]
    if algo == 5:                                   # Ambient: min .. 10x min
        decay = base * 10 ** (x[5] ** 0.9)
    elif algo == 6:                                 # Non-Lin: min .. 2.5x min
        decay = base * 2.5 ** (x[5] ** 0.9)
    else:                                           # Hall, Church, Plate, Rooms: min .. infinite
        decay = float('inf') if x[5] >= 0.999 else base * math.exp(4.7 * x[5])
    lp = 200 + 19800 * x[7]
    return {'algo': DVERB_ALGOS[algo], 'size': DVERB_SIZES[size],
            'input_db': 20 * math.log10(x[2]) if x[2] > 1e-6 else -144.0,
            'mix': x[3] * 100, 'diffusion': x[4] * 100, 'decay': decay,
            'hf_cut': 200 + 19800 * x[6], 'lp': None if x[7] >= 0.999 else lp, 'predelay': pd}


def flatten(seg, pos, ops, items, rate):
    n = type(seg).__name__
    if n == 'Sequence':
        p = pos
        comps = list(seg.components)
        for c in comps:
            cn = type(c).__name__
            if cn == 'Transition':
                p -= c.length
                og = c['OperationGroup'].value
                items.append({'kind': 'transition', 'start': p, 'length': c.length,
                              'cut': c['CutPoint'].value, 'op': op_params(og) if og else None})
                continue
            flatten(c, p, ops, items, rate)
            p += c.length or 0
    elif n == 'OperationGroup':
        o = op_params(seg)
        segs = list(seg.segments)
        if o['op'] in ('Audio Channel Combiner',):
            # stereo combiner: several sourceclips in parallel
            srcs = [s for s in segs if type(s).__name__ == 'SourceClip']
            if srcs:
                items.append({'kind': 'clip', 'start': pos, 'length': seg.length, 'clips': srcs,
                              'ops': ops, 'combiner': True})
                return
        if o['op'] and 'MatteKey' in o['op'] and len(segs) >= 2 and type(segs[1]).__name__ == 'SourceClip':
            # Avid matte key: the fill clip (e.g. a letterbox PNG with alpha) becomes a plain overlay clip
            items.append({'kind': 'clip', 'start': pos, 'length': seg.length, 'clips': [segs[1]], 'ops': ops})
            return
        if o['op'] == 'Motion Control' and segs:
            inner = []
            flatten(segs[0], pos, [], inner, rate)
            clips = [x for x in inner if x['kind'] == 'clip']
            if clips:
                c = clips[0]
                c['length'] = seg.length            # timeline length of the speed effect
                c['inner_len'] = segs[0].length     # source edit units used (source rate may differ, e.g. 50p in 25p)
                c['ops'] = ops + [o]
                sp = o['params'].get('PARAM_SPEED_MAP_U')
                if isinstance(sp, list) and sp:
                    c['speed'] = float(sp[0][1]); c['speed_map'] = True
                elif o['params'].get('SpeedRatio') is not None:
                    c['speed'] = 1.0 / float(frac(o['params']['SpeedRatio']))
                c['start'] = pos
                items.append(c)
            return
        if segs and (seg.length or 0) and (segs[0].length or 0) and segs[0].length != seg.length \
                and o['op'] not in ('Motion Control',):
            # rate conversion (e.g. Avid "Audio Warp" for 50p media in a 25p sequence): the inner segment
            # counts in source units; scale its positions/lengths back to sequence frames.
            inner = []
            flatten(segs[0], 0, ops + [o], inner, rate)
            ratio = Fraction(seg.length, segs[0].length)
            for x in inner:
                if 'start' in x: x['start'] = pos + int(round(x['start'] * ratio))
                if 'length' in x and x['length'] is not None: x['length'] = int(round(x['length'] * ratio))
                items.append(x)
            return
        if segs:
            flatten(segs[0], pos, ops + [o], items, rate)
    elif n == 'SourceClip':
        items.append({'kind': 'clip', 'start': pos, 'length': seg.length, 'clips': [seg], 'ops': ops})
    elif n in ('Filler', 'ScopeReference'):
        items.append({'kind': 'filler', 'start': pos, 'length': seg.length})
    elif n == 'NestedScope':
        for s in seg.slots:   # each scope slot is a video layer
            items.append({'kind': 'layer'})
            flatten(s, pos, ops, items, rate)
    elif n == 'Selector':
        flatten(seg['Selected'].value, pos, ops, items, rate)
    else:
        items.append({'kind': 'unknown', 'type': n, 'start': pos, 'length': getattr(seg, 'length', None)})


def load(path):
    f = aaf2.open(path, 'r')
    top = next(iter(f.content.toplevel()))
    tc_start = 0
    for s in top.slots:
        if s.media_kind == 'Timecode':
            seg = s.segment
            try:
                tc_start = seg.start if type(seg).__name__ == 'Timecode' else seg.components[0].start
            except Exception:
                pass
            break
    tracks = []
    for s in top.slots:
        if s.media_kind not in ('Sound', 'Picture'):
            continue
        rate = frac(s.edit_rate)
        items = []
        flatten(s.segment, 0, [], items, rate)
        if s.media_kind == 'Picture':
            # split nested scope layers into separate video tracks
            layers, cur = [], None
            for it in items:
                if it['kind'] == 'layer':
                    cur = []; layers.append(cur)
                elif cur is not None:
                    cur.append(it)
                else:
                    layers.append([it]) if not layers else layers[-1].append(it)
            for i, L in enumerate(layers or [items]):
                tracks.append({'kind': 'video', 'name': s.name, 'slot': s.slot_id, 'rate': rate, 'items': L,
                               'layer': i, 'track_ops': []})
        else:
            # track-level OperationGroup (pan/volume automation on the whole track)
            track_ops, track_plugins = [], []
            seg = s.segment
            while type(seg).__name__ == 'OperationGroup':
                track_ops.append(op_params(seg))
                segs = list(seg.segments)
                if seg.operation is not None and seg.operation.name == 'Audio Track Effect Container':
                    track_plugins += [p for p in (track_plugin(g) for g in segs[1:]) if p]
                seg = segs[0]
            if track_ops:
                items = []
                flatten(seg, 0, [], items, rate)
            tracks.append({'kind': 'audio', 'name': s.name, 'slot': s.slot_id, 'rate': rate, 'items': items,
                           'track_ops': track_ops, 'track_plugins': track_plugins,
                           'phys': getattr(s, 'physical_track_number', None)})
    return f, top, tc_start, tracks


def resolve_edits(items):
    """Apply Resolve's model: clips meet at a transition's cut point; the transition overlaps both.
    Returns new list of items with clip start/length/src_offset adjusted (src_offset in timeline frames)."""
    out = []
    for i, it in enumerate(items):
        it = dict(it)
        if it['kind'] == 'clip':
            it.setdefault('trim_head', 0)
            it.setdefault('trim_tail', 0)
            prev = items[i - 1] if i > 0 else None
            nxt = items[i + 1] if i + 1 < len(items) else None
            # crossfade only when there is a clip on both sides of the transition
            if prev is not None and prev['kind'] == 'transition' and i >= 2 and items[i - 2]['kind'] == 'clip':
                it['trim_head'] = prev['cut']
            if nxt is not None and nxt['kind'] == 'transition' and i + 2 < len(items) and items[i + 2]['kind'] == 'clip':
                it['trim_tail'] = nxt['length'] - nxt['cut']
            it['r_start'] = it['start'] + it['trim_head']
            it['r_length'] = it['length'] - it['trim_head'] - it['trim_tail']
        out.append(it)
    return out


def track_format(slot):
    try:
        for tv in slot['TimelineMobAttributeList'].value:
            if tv.name == '_TRACK_FORMAT':
                return tv.value
    except Exception:
        pass
    return 1


def reel_name(file_mob):
    """Name of the deepest source mob below a file mob (tape / import source)."""
    mob, name = file_mob, None
    for _ in range(6):
        try:
            seg = list(mob.slots)[0].segment
            sc = seg if type(seg).__name__ == 'SourceClip' else next((c for c in getattr(seg, 'components', []) if type(c).__name__ == 'SourceClip'), None)
        except Exception:
            sc = None
        if sc is None or sc.mob is None:
            break
        mob = sc.mob
        name = mob.name
    return name


def gain_db(ops):
    """Constant clip gain in dB from Avid 'Audio Gain' ops (Amplitude). Returns (db, keyframes or None)."""
    import math
    db, kf = 0.0, None
    for o in ops:
        if o['op'] != 'Audio Gain':
            continue
        p = o['params']
        amp = p.get('Amplitude')
        if amp is not None:
            a = float(frac(amp)) if not isinstance(amp, (int, float)) else float(amp)
            if a > 0:
                db += 20 * math.log10(a)
        if 'AvidControlClipRatio' in p and isinstance(p['AvidControlClipRatio'], list):
            kf = [(t, (20 * math.log10(float(v)) if float(v) > 0 else -100.0)) for t, v in p['AvidControlClipRatio']]
    return db, kf


def resize_params(ops):
    """Avid PaintResize -> (zoom_x, zoom_y, pos_x, pos_y) in Resolve units, or None."""
    for o in ops:
        if o['op'] and o['op'].startswith('PaintResize'):
            p = o['params']
            def num(k, d):
                v = p.get(k)
                if v is None: return d
                if isinstance(v, list): v = v[0][1]
                return float(frac(v)) if not isinstance(v, (int, float)) else float(v)
            sx = num('AFX_SCALE_X_U', 100.0); sy = num('AFX_SCALE_Y_U', sx)
            return sx / 100.0, sy / 100.0, num('AFX_POS_X_U', 0.0) / 1000.0, num('AFX_POS_Y_U', 0.0) / 1000.0
    return None


def track_pan_points(track_ops, track_len):
    """Avid track 'Audio Pan' automation -> [(timeline_frame, resolve_pan -100..100)] or None."""
    for o in track_ops:
        if o['op'] == 'Audio Pan':
            pv = o['params'].get('Pan')
            if pv is None:
                return None
            if not isinstance(pv, list):
                pv = [(0.0, pv)]
            pts = sorted((float(t) * track_len, (float(v) - 0.5) * 200.0) for t, v in pv)
            return pts
    return None


def value_at(points, x):
    if not points: return None
    if x <= points[0][0]: return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]
