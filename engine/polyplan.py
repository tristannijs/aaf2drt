"""Plan trimmed poly-WAVs for every audio clip of a link-to AAF (no audio processing here)."""
import re, urllib.parse
from fractions import Fraction
import aafmodel

HANDLE_FRAMES = 10


def mac_path(url):
    p = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    if not p.startswith(('/Volumes/', '/Users/', '/private/')):
        p = '/Volumes' + p
    return p


def tape_info(file_mob):
    """(tape mob, tape slot id, physical track number, file start on tape in seconds)"""
    try:
        slot = list(file_mob.slots)[0]
        seg = slot.segment
        sc = seg if type(seg).__name__ == 'SourceClip' else next(c for c in seg.components if type(c).__name__ == 'SourceClip')
        rate = aafmodel.frac(slot.edit_rate)
        tape = sc.mob
        tslot = next(s for s in tape.slots if s.slot_id == sc.slot_id)
        try:
            ptn = tslot['PhysicalTrackNumber'].value
        except Exception:
            ptn = sc.slot_id
        return tape, sc.slot_id, ptn, Fraction(sc.start or 0) / rate
    except Exception:
        return None, None, None, Fraction(0)


def master_of(chain):
    for mob, slot_id, pos, rate in chain:
        if type(mob).__name__ == 'MasterMob':
            return mob, slot_id, pos / rate
    return None, None, None


def track_names(mob):
    """Recorder track names Avid keeps in the bin columns TRK1, TRK2, ... (from the BWF/iXML at import)."""
    names = {}
    if mob is None:
        return names
    try:
        comments = mob.get('UserComments') or []
    except Exception:
        comments = []
    for t in comments:
        try:
            k, v = t.name, t.value
        except Exception:
            continue
        if isinstance(k, str) and k.upper().startswith('TRK') and k[3:].isdigit() and isinstance(v, str) and v.strip():
            names[int(k[3:])] = v.strip()
    return names


def channel_name(c, names):
    """TRK name from the bin (e.g. 'TrkC'), else Avid's channel label (A05)."""
    if c.get('ptn') and c['ptn'] in names:
        return names[c['ptn']]
    return 'A%02d' % c['ptn'] if c.get('ptn') else None


def channels_of_master(master):
    """All sound channels of a master clip: list of dict(slot, file_mob, path, ptn, tape_start, file_rate)."""
    out = []
    for s in master.slots:
        if s.media_kind != 'Sound':
            continue
        seg = s.segment
        sc = seg if type(seg).__name__ == 'SourceClip' else next((c for c in getattr(seg, 'components', []) if type(c).__name__ == 'SourceClip'), None)
        if sc is None or sc.mob is None:
            continue
        fm = sc.mob
        if not aafmodel.is_file_mob(fm):
            continue
        url = aafmodel.file_url(fm)
        tape, tslot, ptn, tstart = tape_info(fm)
        try:
            dlen = fm.descriptor['Length'].value; drate = aafmodel.frac(fm.descriptor['SampleRate'].value)
            dur = Fraction(dlen) / drate
        except Exception:
            dur = None
        out.append({'master_slot': s.slot_id, 'file_mob': fm, 'path': mac_path(url) if url else None,
                    'ptn': ptn, 'tape': tape.name if tape else None, 'tape_start': tstart,
                    'mslot_start': Fraction(sc.start or 0) / aafmodel.frac(s.edit_rate), 'dur': dur})
    out.sort(key=lambda c: (c['ptn'] if c['ptn'] is not None else 999, c['master_slot']))
    return out


def plan_clip(sourceclips, length_frames, fps=24):
    """Plan one poly file for a timeline clip. sourceclips: 1 (mono pick) or 2 (stereo combiner)."""
    h = Fraction(HANDLE_FRAMES, fps)
    dur = Fraction(length_frames, fps)
    picks = []
    for sc in sourceclips:
        r = aafmodel.resolve_source(sc, Fraction(fps))
        if r is None:
            return None
        master, mslot, mpos = master_of(r['chain'])
        picks.append((sc, r, master, mslot, mpos))
    sc, r, master, mslot, mpos = picks[0]
    if master is not None and len(picks) == 1:
        chans = channels_of_master(master)
        pick_idx = next((i for i, c in enumerate(chans) if c['master_slot'] == mslot), 0)
        stereo = False
    else:
        # stereo combiner (or no master): one channel per source clip, in order L,R
        chans = []
        for sc_, r_, m_, ms_, mp_ in picks:
            fm = r_['mob']
            tape, tslot, ptn, tstart = tape_info(fm)
            try:
                dd = Fraction(fm.descriptor['Length'].value) / aafmodel.frac(fm.descriptor['SampleRate'].value)
            except Exception:
                dd = None
            chans.append({'master_slot': ms_, 'file_mob': fm, 'path': mac_path(aafmodel.file_url(fm)),
                          'ptn': ptn, 'tape': tape.name if tape else None, 'tape_start': tstart,
                          'mslot_start': Fraction(0), 'dur': dd, 'file_pos': r_['pos']})
        pick_idx = 0
        stereo = len(picks) == 2
    # position of the clip start inside the picked file, and on the tape
    pick = chans[pick_idx]
    file_pos = r['pos'] if 'file_pos' not in pick else pick['file_pos']
    tape_pos = pick['tape_start'] + file_pos
    seg_start = max(Fraction(0), file_pos - h)
    seg_end = file_pos + dur + h
    if pick['dur'] is not None:
        seg_end = min(seg_end, pick['dur'])
    head = file_pos - seg_start
    if seg_end <= seg_start:
        # clip lies (partly) outside the media file: report it instead of handing ffmpeg a negative length
        return {'error': 'out_of_range', 'file_pos': float(file_pos),
                'file_dur': float(pick['dur']) if pick['dur'] is not None else None,
                'path': pick['path'], 'master': master.name if master is not None else None}
    plan = {'master': master.name if master is not None else None, 'tape': pick['tape'],
            'stereo': stereo, 'pick_index': pick_idx, 'tape_seg_start': pick['tape_start'] + seg_start,
            'seg_dur': seg_end - seg_start, 'head': head, 'channels': []}
    names = track_names(master)
    for _, _, m_, _, _ in picks[1:]:
        for k, v in track_names(m_).items():
            names.setdefault(k, v)
    for c in chans:
        # where does this channel's file have the same tape position?
        fpos = (pick['tape_start'] + seg_start) - c['tape_start']
        plan['channels'].append({'path': c['path'], 'ptn': c['ptn'], 'file_offset': fpos,
                                 'name': channel_name(c, names)})
    return plan
