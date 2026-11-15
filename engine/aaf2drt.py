#!/usr/bin/env python3
"""
aaf2drt: Avid link-to AAF + OP-Atom MXF  ->  trimmed poly-WAVs + DaVinci Resolve timeline (.drt)

  python aaf2drt.py --aaf film.aaf --out /path/to/handoff [--template template.drt] [--handles 10]

Output folder: <out>/Audio/*.wav (one poly-WAV per timeline clip, all iso channels, handles)
               <out>/<sequence>.drt
"""
import argparse, json, os, re, struct, subprocess, sys, datetime
from fractions import Fraction
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# data files (template.drt, fl_base.json) live next to the code, or in the bundle when frozen
RES = getattr(sys, '_MEIPASS', HERE)
import aafmodel, polyplan, gen, codec

FPS = 24.0
RATE = Fraction(24)
PATHMAP = []   # [(mac_prefix, local_prefix)] for running on another machine


def local(p):
    for a, b in PATHMAP:
        if p.startswith(a):
            return b + p[len(a):]
    return p


def macp(p):
    for a, b in PATHMAP:
        if p.startswith(b):
            return a + p[len(b):]
    return p


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- audio rendering
def ffmpeg_bin():
    return os.environ.get('FFMPEG', 'ffmpeg')


def decode_segment(path, start_sample, n, sr):
    """Decode n samples starting at start_sample from a mono MXF/WAV -> int32 left-justified."""
    s0 = max(0, start_sample)
    coarse = max(0, s0 - sr)                 # seek to ~1 s before, then trim sample-accurately
    cmd = [ffmpeg_bin(), '-v', 'error', '-nostdin']
    if coarse:
        cmd += ['-ss', '%.6f' % (coarse / sr)]
    cmd += ['-i', path, '-map', '0:a:0',
            '-af', 'atrim=start_sample=%d:end_sample=%d' % (s0 - coarse, s0 - coarse + n),
            '-f', 's32le', '-acodec', 'pcm_s32le', '-']
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError('ffmpeg: %s' % p.stderr.decode(errors='replace')[:300])
    a = np.frombuffer(p.stdout, dtype='<i4').astype(np.int32)
    out = np.zeros(n, dtype=np.int32)
    off = -start_sample if start_sample < 0 else 0
    m = min(len(a), n - off)
    if m > 0:
        out[off:off + m] = a[:m]
    return out


def ixml(names):
    tr = ''.join('<TRACK><CHANNEL_INDEX>%d</CHANNEL_INDEX><INTERLEAVE_INDEX>%d</INTERLEAVE_INDEX><NAME>%s</NAME></TRACK>'
                 % (i + 1, i + 1, n) for i, n in enumerate(names))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<BWFXML><IXML_VERSION>2.10</IXML_VERSION><TRACK_LIST><TRACK_COUNT>%d'
            '</TRACK_COUNT>%s</TRACK_LIST></BWFXML>' % (len(names), tr)).encode()


def write_bwf(path, samples, sr, bits, timeref, names, description=''):
    n, ch = samples.shape
    bps = bits // 8
    sh = samples >> (32 - bits)
    if bps == 3:
        raw = sh.astype('<i4').view(np.uint8).reshape(n, ch, 4)[:, :, :3].tobytes()
    elif bps == 2:
        raw = sh.astype('<i2').tobytes()
    else:
        raw = sh.astype('<i4').tobytes()
    bext = bytearray(602)
    d = description.encode('ascii', 'replace')[:256]
    bext[0:len(d)] = d
    o = b'aaf2drt'; bext[256:256 + len(o)] = o
    now = datetime.datetime.now()
    bext[320:330] = now.strftime('%Y-%m-%d').encode(); bext[330:338] = now.strftime('%H:%M:%S').encode()
    bext[338:346] = struct.pack('<Q', int(timeref))
    bext[346:348] = struct.pack('<H', 1)
    fmt = struct.pack('<HHIIHH', 1, ch, sr, sr * ch * bps, ch * bps, bits)
    chunks = [(b'bext', bytes(bext)), (b'fmt ', fmt), (b'iXML', ixml(names))]
    body = bytearray()
    for cid, b in chunks:
        body += cid + struct.pack('<I', len(b)) + b + (b'\0' if len(b) & 1 else b'')
    body += b'data' + struct.pack('<I', len(raw)) + raw + (b'\0' if len(raw) & 1 else b'')
    with open(path, 'wb') as fh:
        fh.write(b'RIFF' + struct.pack('<I', len(body) + 4) + b'WAVE' + bytes(body))


def probe_audio(path):
    """(sample_rate, bits) of an audio MXF via ffmpeg -i stderr parsing (works without ffprobe)."""
    p = subprocess.run([ffmpeg_bin(), '-hide_banner', '-nostdin', '-i', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t = p.stderr.decode(errors='replace')
    m = re.search(r'Audio: pcm_s(\d+)\w*, (\d+) Hz', t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r'Audio: .*?, (\d+) Hz', t)
    return (int(m.group(1)) if m else 48000), 24


def probe_video(path):
    p = subprocess.run([ffmpeg_bin(), '-hide_banner', '-nostdin', '-i', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t = p.stderr.decode(errors='replace')
    info = {'mtime': os.path.getmtime(path)}
    try:
        info['birth'] = os.stat(path).st_birthtime
    except AttributeError:
        pass
    m = re.search(r'Video: (\w+)', t); info['codec'] = m.group(1) if m else None
    m = re.search(r', (\d{2,5})x(\d{2,5})', t)
    if m: info['w'], info['h'] = int(m.group(1)), int(m.group(2))
    m = re.search(r'timecode\s*:\s*([0-9:;]+)', t); info['tc'] = m.group(1) if m else '00:00:00:00'
    m = re.search(r'reel_name\s*:\s*(.+)', t); info['reel'] = m.group(1).strip() if m else None
    m = re.search(r'material_package_name\s*:\s*(.+)', t); info['mat_name'] = m.group(1).strip() if m else None
    m = re.search(r'bitrate: (\d+) kb/s', t); info['bitrate'] = int(m.group(1)) * 1000 if m else 0
    m = re.search(r'Video:.*?, (\d+(?:\.\d+)?) fps', t) or re.search(r'Video:.*?, (\d+(?:\.\d+)?) tbr', t)
    if m:
        v = float(m.group(1))
        # ffmpeg rounds NTSC rates: 23.98 / 29.97 / 59.94
        for exact in (24000 / 1001, 30000 / 1001, 60000 / 1001):
            if abs(v - exact) < 0.01: v = exact
        info['fps'] = v
    m = re.search(r'Duration: (\d+):(\d+):(\d+)\.(\d+)', t)
    if m:
        secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + float('0.' + m.group(4))
        info['frames'] = int(round(secs * (info.get('fps') or FPS)))
    return info


def audio_effects(it, plan, pan_pts):
    """Clip gain (constant + Avid volume keyframes) and track pan -> EffectFiltersBA hex."""
    db, kf = aafmodel.gain_db(it['ops'])
    head = float(plan['head']) * FPS             # file frame at the (untrimmed) clip start
    L = it['length']
    groups = []
    vol = None
    if kf:
        pts = [(head + t * L, v + db) for t, v in kf]
        vol = (95, gen.keyframe_bytes_generic(pts))
    elif abs(db) > 1e-6:
        vol = (95, db)
    if vol is not None:
        groups.append((124, [vol], 5))
    if pan_pts:
        s0, s1 = it['start'], it['start'] + L
        inside = [(x, v) for x, v in pan_pts if s0 < x < s1]
        v0 = aafmodel.value_at(pan_pts, s0); v1 = aafmodel.value_at(pan_pts, s1 - 1)
        vals = [v0, v1] + [v for _, v in inside]
        if max(vals) - min(vals) < 1e-6:
            if abs(v0) > 1e-6:
                if not groups: groups.append((124, [], 5))
                groups.append((144, [(96, v0)], 1))
        else:
            pts = [(head, v0)] + [(head + (x - s0), v) for x, v in inside] + [(head + L - 1, v1)]
            if not groups: groups.append((124, [], 5))
            groups.append((144, [(96, gen.keyframe_bytes_generic(pts))], 1))
    return gen.effect_blob(groups) if groups else None


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aaf', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--template', default=os.path.join(RES, 'template.drt'))
    ap.add_argument('--handles', type=int, default=10)
    ap.add_argument('--name', default=None)
    ap.add_argument('--plan-only', action='store_true')
    ap.add_argument('--path-map', action='append', default=[], help='MACPREFIX=LOCALPREFIX')
    args = ap.parse_args()
    polyplan.HANDLE_FRAMES = args.handles
    for m in args.path_map:
        a, b = m.split('=', 1); PATHMAP.append((a, b))

    f, top, tc, tracks = aafmodel.load(args.aaf)
    global FPS, RATE
    rates = [aafmodel.frac(s.edit_rate) for s in top.slots if getattr(s, 'edit_rate', None)]
    RATE = max(set(rates), key=rates.count) if rates else Fraction(24)
    FPS = float(RATE)
    gen.FPS, gen.FPS_NOM = FPS, int(round(FPS))
    log('Framerate: %.3f fps' % FPS)
    seqname = args.name or top.name
    safe = re.sub(r'[^\w\-. ]+', '_', seqname)
    audio_dir = os.path.join(args.out, 'Audio')
    os.makedirs(audio_dir, exist_ok=True)
    T = gen.Template(args.template)
    folder_id = re.search(r'<Sm2MpFolder DbId="([^"]+)"', T.mp).group(1)
    slots = {s.slot_id: s for s in top.slots}

    media_xml, bt_ids = [], []
    rendered = {}          # plan key -> (filename, info)
    probe_cache = {}
    counter = {}
    audio_tracks = []
    report = []
    log('Audio ...')
    for t in tracks:
        if t['kind'] != 'audio':
            continue
        stereo_track = aafmodel.track_format(slots[t['slot']]) == 2
        items = aafmodel.resolve_edits(t['items'])
        pan_pts = aafmodel.track_pan_points(t['track_ops'], slots[t['slot']].segment.length or 0)
        xml = []
        for i, it in enumerate(items):
            if it['kind'] == 'clip':
                plan = polyplan.plan_clip(it['clips'], it['length'], RATE)
                if plan is None or any(c['path'] is None for c in plan['channels']):
                    report.append({'track': t['name'], 'start': tc + it['r_start'], 'status': 'unresolved'})
                    continue
                # trims at transitions move the clip head inside the planned segment
                key = (tuple((c['path'], float(c['file_offset'])) for c in plan['channels']), float(plan['seg_dur']))
                missing = [c['path'] for c in plan['channels'] if not os.path.exists(local(c['path']))]
                if missing and key not in rendered:
                    log('  ontbrekende media: %s' % missing[0])
                    report.append({'track': t['name'], 'start': tc + it['r_start'], 'status': 'missing', 'file': missing[0]})
                    continue
                if key not in rendered:
                    p0 = plan['channels'][plan['pick_index']]['path']
                    if p0 not in probe_cache:
                        probe_cache[p0] = probe_audio(local(p0)) if os.path.exists(local(p0)) else (48000, 24)
                    sr, bits = probe_cache[p0]
                    n = int(round(plan['seg_dur'] * sr))
                    base = re.sub(r'[^\w\-]+', '_', (plan['master'] or plan['tape'] or 'clip'))
                    counter[base] = counter.get(base, 0) + 1
                    fn = '%s_%03d.wav' % (base, counter[base])
                    names = ['A%02d' % c['ptn'] if c['ptn'] else 'CH%d' % (k + 1) for k, c in enumerate(plan['channels'])]
                    outp = os.path.join(audio_dir, fn)
                    expected = 44 + 610 + 8 + len(ixml(names)) + 8 + 16 + n * len(plan['channels']) * (bits // 8)
                    if not args.plan_only and not (os.path.exists(outp) and abs(os.path.getsize(outp) - expected) < 64):
                        cols = [decode_segment(local(c['path']), int(round(c['file_offset'] * sr)), n, sr) for c in plan['channels']]
                        write_bwf(os.path.join(audio_dir, fn), np.stack(cols, axis=1), sr, bits,
                                  round(plan['tape_seg_start'] * sr), names, description=plan['master'] or '')
                    info = {'ch': len(plan['channels']), 'sr': sr, 'bits': bits, 'frames': n,
                            'start': float(plan['tape_seg_start'])}
                    iid, bid, mx = gen.mp_audio_item(folder_id, fn, macp(audio_dir), info['ch'], sr, n, info['start'], bits)
                    media_xml.append(mx); bt_ids.append((bid, gen.audio_metadata_blob(fn, names, ixml(names), plan['master'] or '')))
                    rendered[key] = (fn, info, iid)
                    log('  %s  %d ch  %.1fs' % (fn, info['ch'], n / sr))
                fn, info, iid = rendered[key]
                inn = int(round(plan['head'] * FPS)) + it['trim_head']
                stereo = plan['stereo'] and stereo_track
                codes = gen.channel_code(info['ch'], plan['pick_index'] + 1, stereo=stereo)
                effects_hex = audio_effects(it, plan, pan_pts)
                c = dict(start=tc + it['r_start'], duration=it['r_length'], **{'in': inn},
                         name=(it['clips'][0].mob.name if it['clips'][0].mob is not None else fn),
                         media_ref=iid, media_start=info['start'], path=macp(os.path.join(audio_dir, fn)), reel=plan['tape'],
                         media_frames=int(round(info['frames'] / info['sr'] * FPS)), codes=codes, stereo=stereo,
                         effects_hex=effects_hex)
                xml.append(gen.ti_audio_clip(c))
                report.append({'track': t['name'], 'start': tc + it['r_start'], 'file': fn,
                               'channel': plan['pick_index'] + 1, 'channels': info['ch'], 'status': 'ok'})
            elif it['kind'] == 'transition':
                L = items[i - 1]['kind'] if i > 0 else None
                R = items[i + 1]['kind'] if i + 1 < len(items) else None
                cut, ln = it['cut'], it['length']
                if L == 'clip' and R == 'clip': pos, align = 2, (1 if cut == 0 else 3 if cut == ln else 2)
                elif R == 'clip': pos, align = 1, 1
                else: pos, align = 3, 3
                xml.append(gen.ti_transition(tc + it['start'], ln, align, pos))
        audio_tracks.append({'name': t['name'], 'subtype': 0 if stereo_track else 1, 'items_xml': xml})

    log('Video ...')
    vfields = lambda start: codec.blob_encode(codec.pb_encode([[1, 2, [[1, 2, [[21, 0, start]]], [4, 0, 1], [9, 0, 1]]],
                                                              [2, 2, bytes.fromhex('0000000000000006ffffffffffffffff')]]), 2, False).hex()
    vmedia = {}
    video_tracks = []
    for t in tracks:
        if t['kind'] != 'video':
            continue
        items = aafmodel.resolve_edits(t['items'])
        xml = []
        for i, it in enumerate(items):
            if it['kind'] == 'clip':
                r = aafmodel.resolve_source(it['clips'][0])
                if not r or not aafmodel.file_url(r['mob']):
                    continue
                path = polyplan.mac_path(aafmodel.file_url(r['mob']))
                if path not in vmedia:
                    if not os.path.exists(local(path)):
                        log('  missing video', path); continue
                    info = probe_video(local(path))
                    iid, bid, mx = gen.mp_video_item(folder_id, os.path.dirname(path), os.path.basename(path), info,
                                                     fps=info.get('fps') or FPS)
                    media_xml.append(mx); bt_ids.append(bid)
                    vmedia[path] = (iid, info)
                iid, info = vmedia[path]
                mf = info.get('fps') or FPS          # media frame rate (can differ from the timeline, e.g. 50p in 25p)
                if it.get('inner_len') and it.get('length') and not it.get('speed_map') and r.get('rate'):
                    # real playback speed = source seconds used / timeline seconds. A Motion Control that only
                    # converts the frame rate (Avid 50p media in a 25p sequence) gives exactly 1.0.
                    it['speed'] = (it['inner_len'] / float(r['rate'])) / (it['length'] / FPS)
                # In counts timeline frames (seconds x sequence rate), also for media with another rate
                inn = int(round(r['pos'] * FPS)) + it['trim_head']
                tmap = None
                if it.get('speed') and abs(it['speed'] - 1.0) > 1e-6:
                    inn, tmap = gen.speed_timemap(r['pos'] * FPS, it['speed'],
                                                  int(round((info.get('frames') or 1) * FPS / mf)), FPS)
                rz = aafmodel.resize_params(it['ops'])
                eff_hex = gen.transform_effects(rz[0], rz[1], rz[2]) if rz else None
                c = dict(start=tc + it['r_start'], duration=it['r_length'], **{'in': inn},
                         timemap_hex=tmap, effects_hex=eff_hex,
                         name=info.get('mat_name') or info.get('reel') or os.path.basename(path), media_ref=iid,
                         media_start=gen.tc_to_frames(info['tc'], int(round(mf))) / mf, path=path, reel=info.get('reel'), fps=mf,
                         media_frames=int(info.get('frames') or 1), fields_hex=vfields(tc + it['r_start']),
                         preconform='00000100000030c20000010000003042')
                xml.append(gen.ti_video_clip(c))
            elif it['kind'] == 'transition':
                L = items[i - 1]['kind'] if i > 0 else None
                R = items[i + 1]['kind'] if i + 1 < len(items) else None
                cut, ln = it['cut'], it['length']
                if L == 'clip' and R == 'clip': pos, align = 2, (1 if cut == 0 else 3 if cut == ln else 2)
                elif R == 'clip': pos, align = 1, 1
                else: pos, align = 3, 3
                xml.append(gen.ti_transition(tc + it['start'], ln, align, pos, audio=False))
        video_tracks.append({'items_xml': xml})
    if not video_tracks:
        video_tracks = [{'items_xml': []}]

    # timeline resolution: the most common frame size among the video media (Avid media matches the project format)
    sizes = [(i.get('w'), i.get('h')) for _, i in vmedia.values() if i.get('w') and i.get('h')]
    resolution = max(set(sizes), key=sizes.count) if sizes else None
    if resolution:
        log('Resolutie: %dx%d' % resolution)
    drt_path = os.path.join(args.out, safe + '.drt')
    gen.build(T, seqname, video_tracks, audio_tracks, media_xml, bt_ids, drt_path, tc_start=tc, resolution=resolution,
              duration=max([s.segment.length or 0 for s in top.slots if getattr(s, 'segment', None) is not None and (s.segment.length or 0) < 10**8] or [0]))
    with open(os.path.join(args.out, 'aaf2drt_report.json'), 'w') as fh:
        json.dump(report, fh, indent=1)
    ok = sum(1 for r in report if r['status'] == 'ok')
    log('Done: %d audio clips, %d poly files, %d video files -> %s' % (ok, len(rendered), len(vmedia), drt_path))


if __name__ == '__main__':
    main()
