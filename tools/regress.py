"""Regression check for the converter: run it on a set of AAFs and compare the result with a stored snapshot.

    python tools/regress.py --golden DIR [--update] [--path-map MAC=LOCAL ...] AAF [AAF ...]

For each AAF the converter runs into a temporary folder (WAVs are cached in DIR/cache/<name>), and a snapshot is
taken of what matters in the .drt: per clip its name, timeline start, duration, source in-point and media start,
which clip effects it carries, the size of the Fairlight mixer model, and the status lines of the report.
Without --update the snapshot is compared with DIR/<name>.json and every difference is printed; the exit code
is 1 when anything changed. Project material (AAFs, snapshots) stays outside the repository.
"""
import argparse, hashlib, json, os, re, subprocess, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, '..', 'engine')
sys.path.insert(0, ENGINE)
import codec, drtio  # noqa: E402


def fl_model_size(F):
    mp = F['MediaPool/Master/MpFolder.xml']
    m = re.search(r'<Sm2Sequence DbId="[^"]+">\s*<FieldsBlob>([0-9a-f]+)</FieldsBlob>', mp)
    q = codec.qt_decode(bytes.fromhex(m.group(1)))
    blob = [e[1][2] for e in q[1] if e[0] == 'FLStudioModelBA'][0]
    return len(zlib.decompress(blob[4:]))


def clip_fx_digest(el):
    fb = re.search(r'<FieldsBlob>([0-9a-f]*)</FieldsBlob>', el)
    if not fb:
        return None
    d = codec.blob_decode(bytes.fromhex(fb.group(1)))
    if not d or b'FL::ClipFX' not in d['payload']:
        return None
    p = d['payload']
    i = p.find(b'FL::ClipFX')
    return hashlib.sha1(p[i:]).hexdigest()[:12]


def snapshot(drt, report):
    F = drtio.read_drt(drt)
    seq = ''.join(v for k, v in sorted(F.items()) if k.startswith('SeqContainer'))
    clips = []
    for kind in ('Sm2TiAudioClip', 'Sm2TiVideoClip'):
        for el in re.findall(r'<%s .*?</%s>' % (kind, kind), seq, re.S):
            g = lambda tag: (re.search(r'<%s>([^<]*)</%s>' % (tag, tag), el) or [None, None])[1]
            clips.append({'kind': 'audio' if 'Audio' in kind else 'video', 'name': g('Name'), 'start': g('Start'), 'duration': g('Duration'),
                          'in': g('In'), 'media_start': g('MediaStartTime'), 'fx': clip_fx_digest(el)})
    clips.sort(key=lambda c: (c['kind'], float(c['start'] or 0), c['name'] or ''))
    rep = json.load(open(report)) if os.path.exists(report) else []
    lines = sorted('%s|%s|%s|%s' % (r.get('track'), r.get('start'), r.get('status'), ';'.join(r.get('notes') or []))
                   for r in rep)
    return {'clips': clips, 'fl_model_bytes': fl_model_size(F), 'report': lines}


def diff(old, new):
    out = []
    if old['fl_model_bytes'] != new['fl_model_bytes']:
        out.append('mixer model: %d -> %d bytes' % (old['fl_model_bytes'], new['fl_model_bytes']))
    oc, nc = old['clips'], new['clips']
    if len(oc) != len(nc):
        out.append('clips: %d -> %d' % (len(oc), len(nc)))
    # match clips on (kind, name, start); report what changed, disappeared or appeared
    key = lambda c: (c['kind'], c['name'], c['start'])
    rest = list(nc)
    for a in oc:
        same = [b for b in rest if key(b) == key(a)]
        if not same:
            out.append('- %s %s @ %s (dur %s, in %s, media %s)' % (a['kind'], a['name'], a['start'], a['duration'],
                                                                   a['in'], a['media_start']))
            continue
        b = next((b for b in same if b == a), same[0])
        rest.remove(b)
        if a != b:
            out.append('~ %s %s @ %s: %s' % (a['kind'], a['name'], a['start'],
                                             ', '.join('%s %s -> %s' % (k, a[k], b[k]) for k in a if a[k] != b.get(k))))
    for b in rest:
        out.append('+ %s %s @ %s (dur %s, in %s, media %s)' % (b['kind'], b['name'], b['start'], b['duration'],
                                                               b['in'], b['media_start']))
    for r in sorted(set(old['report']) ^ set(new['report'])):
        out.append(('- ' if r in old['report'] else '+ ') + 'report ' + r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('aafs', nargs='+')
    ap.add_argument('--golden', required=True)
    ap.add_argument('--update', action='store_true')
    ap.add_argument('--path-map', action='append', default=[])
    a = ap.parse_args()
    os.makedirs(a.golden, exist_ok=True)
    changed = 0
    for aaf in a.aafs:
        name = re.sub(r'[^\w\-.]+', '_', os.path.splitext(os.path.basename(aaf))[0])
        out = os.path.join(a.golden, 'cache', name)
        cmd = [sys.executable, '-B', os.path.join(ENGINE, 'aaf2drt.py'), '--aaf', aaf, '--out', out]
        for m in a.path_map:
            cmd += ['--path-map', m]
        r = subprocess.run(cmd, capture_output=True, text=True)
        drts = [f for f in os.listdir(out) if f.endswith('.drt')] if os.path.isdir(out) else []
        if r.returncode or not drts:
            print('%s: CONVERSION FAILED\n%s' % (name, (r.stdout + r.stderr)[-2000:])); changed += 1; continue
        drt = max((os.path.join(out, f) for f in drts), key=os.path.getmtime)
        snap = snapshot(drt, os.path.join(out, 'aaf2drt_report.json'))
        gpath = os.path.join(a.golden, name + '.json')
        if a.update or not os.path.exists(gpath):
            json.dump(snap, open(gpath, 'w'), indent=1)
            print('%s: snapshot saved (%d clips)' % (name, len(snap['clips'])))
            continue
        d = diff(json.load(open(gpath)), snap)
        if d:
            changed += 1
            print('%s: %d CHANGES' % (name, len(d)))
            for line in d[:40]:
                print('   ' + line)
        else:
            print('%s: unchanged (%d clips)' % (name, len(snap['clips'])))
    sys.exit(1 if changed else 0)


if __name__ == '__main__':
    main()
