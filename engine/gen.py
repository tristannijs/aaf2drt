"""Generate a DaVinci Resolve .drt from a timeline model, using a Resolve-written .drt as skeleton template."""
import re, uuid, struct, html
import codec, drtio, flmodel

# timeline frame rate (exact, e.g. 24000/1001 for 23.976) and its nominal integer; set by aaf2drt
FPS = 24.0
FPS_NOM = 24

def rate_hex(fps=None):
    return (struct.pack('<d', fps or FPS) + b'\x00' * 8).hex()


def fmtnum(v):
    v = float(v)
    return str(int(v)) if v == int(v) else repr(v)


def U():
    return str(uuid.uuid4())

def esc(s):
    return html.escape(s, quote=False)

def ledouble(*v):
    return b''.join(struct.pack('<d', x) for x in v)

def qtb(entries):
    return codec.qt_encode(1, entries)

def qs(k, v):  return [k, [codec.QT_STRING, 0, v]]
def qi(k, v):  return [k, [codec.QT_INT, 0, v]]
def qu(k, v):  return [k, [codec.QT_UINT, 0, v]]
def qll(k, v): return [k, [codec.QT_LL, 0, v]]
def qd(k, v):  return [k, [codec.QT_DOUBLE, 0, v]]
def qb(k, v):  return [k, [codec.QT_BYTES, 0, v]]
def qbool(k, v): return [k, [codec.QT_BOOL, 0, v]]


# ------------------------------------------------------------------ template
class Template:
    def __init__(self, path):
        self.files = drtio.read_drt(path)
        self.project = self.files['project.xml']
        self.mp = self.files['MediaPool/Master/MpFolder.xml']
        self.seqname = [n for n in self.files if n.startswith('SeqContainer/')][0]
        self.seq = self.files[self.seqname]
        hdr = re.match(r'(<\?xml[^>]*>\s*<!--[^>]*-->\s*)', self.mp).group(1)
        self.xmlhdr = hdr


# ------------------------------------------------------------------ clip metadata (BtMetadataLockableBlob)
def audio_metadata_blob(filename, names, ixml_bytes=b'', description=''):
    """Resolve's cached file metadata for an audio clip: channel names (keys 240+), channel count (2),
    file name (215), iXML (449), description (64/225). Built per file, so every clip shows its own channel names."""
    ents = [(2, str(len(names)).encode()), (215, filename.encode())]
    ents += [(240 + k, n.encode()) for k, n in enumerate(names[:16])]
    if ixml_bytes:
        ents.append((449, ixml_bytes))
    if description:
        ents += [(64, description.encode()), (225, description.encode())]
    body = b''.join(codec.pb_encode([[2, 2, codec.pb_encode([[1, 0, k], [2, 2, v]])]]) for k, v in ents)
    inner = bytes.fromhex('000f4241') + struct.pack('>I', len(body)) + body
    blob = codec.blob_encode(codec.pb_encode([[2, 2, inner]]), ver=10001, compress=True)
    return codec.qt_encode(1, [['BlobData', [codec.QT_BYTES, 0, blob]]]).hex()


# ------------------------------------------------------------------ media pool items
def mp_audio_item(folder_id, name, dirpath, ch, sr, frames, start_sec, bits, fps=None):
    fps = fps or FPS
    item_id, bt_id = U(), U()
    dur = frames / sr
    bitcode = {16: 1, 24: 2, 32: 3}.get(bits, 2)
    ext = ledouble(start_sec, dur)
    entries = []
    if ch == 2:
        cv = qtb([qb('1', qtb([qs('MediaRef', bt_id), qi('ChannelIdx', 1)])),
                  qb('0', qtb([qs('MediaRef', bt_id)]))])
        entries.append(qb('0', qtb([qu('Type', 0), qb('MediaExtents', ext), qs('CodecName', 'Linear PCM'),
                                    qb('ChannelVecBA', cv), qu('BitDepth', bitcode)])))
    else:
        for k in reversed(range(ch)):
            inner = [qs('MediaRef', bt_id)] + ([qi('ChannelIdx', k)] if k else [])
            cv = qtb([qb('0', qtb(inner))])
            entries.append(qb(str(k), qtb([qb('MediaExtents', ext), qs('CodecName', 'Linear PCM'),
                                           qb('ChannelVecBA', cv), qu('BitDepth', bitcode)])))
    fields = [[1, 2, [[1, 2, [[6, 0, 4]]], [5, 2, qtb(entries)]]], [2, 2, [[1, 1, struct.pack('<d', fps)]]]]
    fb = codec.blob_encode(codec.pb_encode(fields), ver=2, compress=True).hex()
    clip = codec.blob_encode(codec.pb_encode([[1, 2, dirpath], [2, 2, name], [5, 2, 'Linear PCM'],
                                              [15, 0, 4], [16, 0, 100], [18, 0, 32768]]), ver=2, compress=True).hex()
    track = qtb([qs('UniqueId', U()), qd('StartTime', start_sec), qu('SampleRate', sr), qi('NumChannels', ch),
                 qi('IdxTrack', 0), qll('Duration', frames), qs('DbType', 'BtAudioTrack'),
                 qs('CodecName', 'Linear PCM'), qu('BitDepth', bitcode)])
    tracks = qtb([qb('0', track)]).hex()
    xml = f'''<Element>
   <Sm2MpAudioClip DbId="{item_id}">
    <FieldsBlob>{fb}</FieldsBlob>
    <Name>{esc(name)}</Name>
    <MpFolder>{folder_id}</MpFolder>
    <UniqueMediaPoolItemId>{U()}</UniqueMediaPoolItemId>
    <MarkIn/>
    <MarkInVideo/>
    <MarkInAudio/>
    <MarkOut/>
    <MarkOutVideo/>
    <MarkOutAudio/>
    <CurPlayheadPosition/>
    <PinsBA/>
    <VirtualAudioTracksBA/>
    <EmbeddedAudioVec>
     <Element>
      <BtAudioInfo DbId="{bt_id}">
       <FieldsBlob/>
       <Clip>{clip}</Clip>
       <TracksBA>{tracks}</TracksBA>
       <MediaMetadata/>
      </BtAudioInfo>
     </Element>
    </EmbeddedAudioVec>
   </Sm2MpAudioClip>
  </Element>
'''
    return item_id, bt_id, xml


# ------------------------------------------------------------------ timeline items
def channel_code(ch_count, idx, stereo=False):
    if stereo:
        return [0x4001, 0x4002]
    if ch_count > 2:
        return [(idx << 14) | 1]
    return [(1 << 14) | idx]


def vat_blob(codes, mono=True):
    body = struct.pack('>II', 2, len(codes)) + b''.join(struct.pack('>I', c) for c in codes)
    return qtb([qb('ChannelsBA', body), [ 'AudioType', [codec.QT_INT, 0, 1 if mono else 0]]]).hex()


def ti_fields(start, link=None):
    inner = []
    if link:
        inner.append([1, 2, qtb([qs('0', link)])])
    inner.append([21, 0, start])
    pb = [[1, 2, [[1, 2, inner], [4, 0, 1], [9, 0, 1]]], [15, 0, 4]]
    raw = codec.pb_encode(pb)
    return codec.blob_encode(raw, ver=2, compress=len(raw) > 40).hex()


def timemap(frames_tl, fps=None):
    fps = fps or FPS
    return (b'\x02' + struct.pack('>d', (frames_tl - 1) / fps)).hex()


def keyframe_bytes_generic(points):
    body = b''.join(struct.pack('<iHBBd', int(round(f)), 0, 0x0C, 0, v) for f, v in points)
    return struct.pack('>i', -len(body)) + body


def transform_effects(zx, zy, px):
    """Resolve video transform (effect 4: slots 42 zoomX, 43 zoomY, -, 40 posX, 8 empty) + effect 6 (6 empty)."""
    e4 = [[1, 0, 4]]
    for pid, v in ((42, zx), (43, zy), (None, None), (40, px)):
        if pid is None or v is None or (pid == 40 and abs(v) < 1e-9):
            e4.append([9, 2, b'']); continue
        e4.append([9, 2, [[1, 0, pid], [3, 2, [[1, 2, [[2, 1, struct.pack('<d', v)]]]]]]])
    e4 += [[9, 2, b''] for _ in range(10)]
    e6 = [[1, 0, 6]] + [[9, 2, b''] for _ in range(6)]
    raw = codec.pb_encode([[1, 2, e4], [1, 2, e6]])
    return codec.blob_encode(raw, ver=2, compress=True).hex()


def speed_timemap(src_start_frames, speed, media_frames, fps=None):
    fps = fps or FPS
    """Constant speed -> (In in output frames, MediaTimemapBA hex)."""
    inn = int(src_start_frames / speed)
    ymin = (src_start_frames - speed * inn) / fps
    ymax = (media_frames - 1) / fps
    xmax = (ymax - ymin) / speed
    kf = codec.pb_encode([[160, 0, 9], [2, 1, struct.pack('<d', ymin)],
                          [1, 2, [[1, 1, struct.pack('<d', xmax)], [2, 1, struct.pack('<d', ymax)]]]])
    q = qtb([qd('YMin', ymin), qd('YMax', ymax), qd('XMax', xmax), qs('UniqueId', U()),
             qd('LastValidYOffset', ymax), qb('KeyframesBA', kf), qs('DbType', 'Sm2TimeMap')]).hex()
    return inn, q


def effect_blob(effects):
    """effects: list of (effect_id, [(param_id, value_or_keyframes)], n_empty_slots)"""
    if not effects:
        return ''
    fields = []
    for eid, params, nslots in effects:
        sub = [[1, 0, eid]]
        for pid, val in params:
            if isinstance(val, (bytes, bytearray)):
                sub.append([9, 2, [[1, 0, pid], [10, 2, bytes(val)]]])
            else:
                sub.append([9, 2, [[1, 0, pid], [3, 2, [[1, 2, [[2, 1, struct.pack('<d', val)]]]]]]])
        for _ in range(max(0, nslots - len(params))):
            sub.append([9, 2, b''])
        fields.append([1, 2, sub])
    raw = codec.pb_encode(fields)
    return codec.blob_encode(raw, ver=2, compress=len(raw) > 40).hex()


def keyframe_bytes(points):
    """points: [(source_frame, dB)] -> Resolve keyframe list"""
    body = b''.join(struct.pack('<iHBBd', f, 0, 0x0C, 0, v) for f, v in points)
    return struct.pack('>i', -len(body)) + body


def ti_audio_clip(c):
    inn = '<In>%d</In>' % c['in'] if c['in'] else '<In/>'
    eff = c.get('effects_hex') or effect_blob(c.get('effects'))
    effx = '<EffectFiltersBA>%s</EffectFiltersBA>' % eff if eff else '<EffectFiltersBA/>'
    return f'''<Element>
      <Sm2TiAudioClip DbId="{U()}">
       <FieldsBlob>{ti_fields(c['start'], c.get('link'))}</FieldsBlob>
       <PrettyType/>
       <Name>{esc(c['name'])}</Name>
       <Start>{c['start']}</Start>
       <Duration>{c['duration']}</Duration>
       <LinkedItemSync/>
       <WasDisbanded>false</WasDisbanded>
       <MarkersBA/>
       <UiMemento>0</UiMemento>
       <Flags>0</Flags>
       <PriorityIndex>0</PriorityIndex>
       {effx}
       <ImportExportMetadataBA/>
       <RenderTextEnabled>false</RenderTextEnabled>
       <RenderTextGanged>false</RenderTextGanged>
       <RenderTextPrefixed>false</RenderTextPrefixed>
       {inn}
       <MixedFrameRateAlignment>0</MixedFrameRateAlignment>
       <MediaRef>{c['media_ref']}</MediaRef>
       <MediaStartTime>{fmtnum(c['media_start'])}</MediaStartTime>
       <MediaFilePath>{esc(c['path'])}</MediaFilePath>
       <MediaReelNumber>{esc(c.get('reel') or '')}</MediaReelNumber>
       <MediaFrameRate>{rate_hex()}</MediaFrameRate>
       <MediaTimemapBA>{timemap(c['media_frames'])}</MediaTimemapBA>
       <LastChangedTime>0</LastChangedTime>
       <LastRenderedTime>0</LastRenderedTime>
       <IsMarkedForCaching>false</IsMarkedForCaching>
       <IsForceConformed>true</IsForceConformed>
       <MatchConflictState>0</MatchConflictState>
       <UseOppositeSrcForLeftEye>false</UseOppositeSrcForLeftEye>
       <UseOppositeSrcForRightEye>false</UseOppositeSrcForRightEye>
       <RenderCacheBA/>
       <VirtualAudioTrackBA>{vat_blob(c['codes'], mono=not c.get('stereo'))}</VirtualAudioTrackBA>
       <MediaTrackIdx>0</MediaTrackIdx>
      </Sm2TiAudioClip>
     </Element>
'''


def ti_transition(start, duration, align, position, audio=True):
    fb = codec.blob_encode(codec.pb_encode([[1, 2, [[21, 0, start]]],
                                            [2, 2, bytes.fromhex('0000002c789c636660642016000000da0005')]]),
                           ver=2, compress=False).hex()
    eff = '0000000200000009800a0608504a004a00' if audio else \
          '0000000200000017800a1408124a004a04083e48014a04083f48014a004a00'
    pretty = 'Cross Fade 0DB' if audio else 'Cross Dissolve'
    rt = 'true'
    return f'''<Element>
      <Sm2TiTransition DbId="{U()}">
       <FieldsBlob>{fb}</FieldsBlob>
       <PrettyType>{pretty}</PrettyType>
       <Name/>
       <Start>{start}</Start>
       <Duration>{duration}</Duration>
       <LinkedItemSync/>
       <WasDisbanded>false</WasDisbanded>
       <MarkersBA/>
       <UiMemento>0</UiMemento>
       <Flags>0</Flags>
       <PriorityIndex>0</PriorityIndex>
       <EffectFiltersBA>{eff}</EffectFiltersBA>
       <ImportExportMetadataBA/>
       <RenderTextEnabled>{rt}</RenderTextEnabled>
       <RenderTextGanged>{rt}</RenderTextGanged>
       <RenderTextPrefixed>{rt}</RenderTextPrefixed>
       <AlignmentType>{align}</AlignmentType>
       <Position>{position}</Position>
      </Sm2TiTransition>
     </Element>
'''


def ti_track(track_id, seq_id, ttype, subtype, items_xml, name=None):
    if ttype == 0:
        fb = qtb([qi('NumLayers', 0)]).hex()
    else:
        fb = qtb([qi('NumLayers', 0), qbool('ExcludeTrackFromSequenceCaching', True)]).hex()
    items = '<Items>\n     ' + ''.join(items_xml) + '    </Items>' if items_xml else '<Items/>'
    nm = '<UserDefinedName>%s</UserDefinedName>' % esc(name) if name else '<UserDefinedName/>'
    return f'''<Element>
   <Sm2TiTrack DbId="{track_id}">
    <FieldsBlob>{fb}</FieldsBlob>
    <Type>{ttype}</Type>
    <SubType>{subtype}</SubType>
    <Flags>0</Flags>
    <Sequence>{seq_id}</Sequence>
    {items}
    <FusionCompHolderItems/>
    {nm}
    <LayersVec/>
   </Sm2TiTrack>
  </Element>
'''


# ------------------------------------------------------------------ assembly
def build(template, timeline_name, video_tracks, audio_tracks, media_items_xml, bt_ids, out_path, tc_start=None, resolution=None, duration=None):
    """video_tracks/audio_tracks: list of dict(name, subtype, items_xml). media_items_xml: list of xml strings."""
    T = template
    mp = T.mp
    # --- ids we need to wire up
    seq_id = re.search(r'<Sm2Sequence DbId="([^"]+)"', mp).group(1)
    folder_id = re.search(r'<Sm2MpFolder DbId="([^"]+)"', mp).group(1)
    seqc_id = re.search(r'<Sm2SequenceContainer DbId="([^"]+)"', T.seq).group(1)
    # --- media pool: keep the timeline clip element, replace the other media
    tl_el = re.search(r'<Element>\s*<Sm2MpTimelineClip .*?</Sm2MpTimelineClip>\s*</Element>\s*', mp, re.S).group(0)
    tl_el = re.sub(r'(<Name>)[^<]*(</Name>)', r'\g<1>%s\g<2>' % esc(timeline_name), tl_el)
    # timeline frame rate and start timecode (seconds)
    tl_el = re.sub(r'<FrameRate>[0-9a-f]*</FrameRate>', '<FrameRate>%s</FrameRate>' % rate_hex(), tl_el)
    if tc_start is not None:
        dur_hex = struct.pack('<d', duration / FPS).hex() if duration else None
        tl_el = re.sub(r'<MediaExtents>[0-9a-f]{16}([0-9a-f]{16})</MediaExtents>',
                       lambda m: '<MediaExtents>%s%s</MediaExtents>' % (struct.pack('<d', tc_start / FPS).hex(), dur_hex or m.group(1)), tl_el)
    start_mv = mp.index('<MediaVec>') + len('<MediaVec>\n  ')
    end_mv = mp.index('</MediaVec>')
    new_mp = mp[:start_mv] + tl_el + ''.join('  ' + x for x in media_items_xml) + ' ' + mp[end_mv:]
    # --- sequence container
    seq = T.seq
    head = seq[:seq.index('<VideoTrackVec>')]
    vt = '<VideoTrackVec>\n  ' + ''.join(ti_track(U(), seq_id, 0, 0, t['items_xml'], t.get('name')) for t in video_tracks) + ' </VideoTrackVec>\n '
    at = '<AudioTrackVec>\n  ' + ''.join(ti_track(U(), seq_id, 1, t['subtype'], t['items_xml'], t.get('name')) for t in audio_tracks) + ' </AudioTrackVec>\n '
    tail = seq[seq.index('<SubtitleTrackVec'):]
    new_seq = head + vt + at + tail
    # The template's Fairlight mixer model (FLStudioModelBA) describes the template's own track layout.
    # Replace it with a model generated for our own tracks (mono/stereo per track, in order).
    first_audio = re.search(r'<AudioTrackVec>\s*<Element>\s*<Sm2TiTrack DbId="([^"]+)"', new_seq)
    layout = ''.join('s' if t['subtype'] == 0 else 'm' for t in audio_tracks)
    fl_blob = flmodel.qt_blob(layout, ntsc=abs(FPS - FPS_NOM) > 1e-6)
    def fix_seq_blob(m):
        q = codec.qt_decode(m.group(1))
        ents = [e for e in q[1] if e[0] not in ('FLStudioModelBA', 'FirstAudioOnlyTrack')]
        out = []
        for e in ents:
            if e[0] == 'SequenceSetup' and isinstance(e[1][2], bytes) and len(e[1][2]) > 243:
                ss = bytearray(e[1][2])
                ss[83] = FPS_NOM & 0xFF
                if resolution:
                    w, h = resolution
                    for o in (40, 228): struct.pack_into('>Q', ss, o, int(w))
                    for o in (48, 236): struct.pack_into('>Q', ss, o, int(h))
                e[1][2] = bytes(ss)
            if e[0] == 'DolbyVisionVersion':
                if first_audio:
                    out.append(['FirstAudioOnlyTrack', [codec.QT_BYTES, 0, first_audio.group(1).encode()]])
                out.append(['FLStudioModelBA', [codec.QT_BYTES, 0, fl_blob]])
            out.append(e)
        if not any(e[0] == 'FLStudioModelBA' for e in out):
            if first_audio:
                out.append(['FirstAudioOnlyTrack', [codec.QT_BYTES, 0, first_audio.group(1).encode()]])
            out.append(['FLStudioModelBA', [codec.QT_BYTES, 0, fl_blob]])
        ents = out
        return m.group(0).replace(m.group(1), codec.qt_encode(q[0], ents).hex())
    mp_new = re.sub(r'<Sm2Sequence DbId="[^"]+">\s*<FieldsBlob>([0-9a-f]+)</FieldsBlob>', fix_seq_blob, new_mp, count=1)
    new_mp = mp_new
    # --- project: lockable blobs per BtAudioInfo, keep sequence + mediapool owners
    proj = T.project
    proj = re.sub(r'(<ProjectName>)[^<]*(</ProjectName>)', r'\g<1>%s\g<2>' % esc(timeline_name), proj)
    # one BtMetadataLockableBlob per audio file, generated from that file's own channel names
    proj = re.sub(r'<Element>\s*<BtMetadataLockableBlob .*?</BtMetadataLockableBlob>\s*</Element>\s*', '', proj, flags=re.S)
    lbs = ''.join('''<Element>
     <BtMetadataLockableBlob DbId="%s">
      <FieldsBlob>%s</FieldsBlob>
      <BlobOwner>%s</BlobOwner>
      <DbSavedTime>0</DbSavedTime>
     </BtMetadataLockableBlob>
    </Element>
    ''' % (U(), fb, bid) for bid, fb in (x for x in bt_ids if isinstance(x, tuple) and x[1]))
    if lbs:
        if '<LocableBlobSet/>' in proj:
            proj = proj.replace('<LocableBlobSet/>', '<LocableBlobSet>\n    ' + lbs + '</LocableBlobSet>', 1)
        elif '<LocableBlobSet>' in proj:
            i = proj.index('<LocableBlobSet>') + len('<LocableBlobSet>')
            proj = proj[:i] + '\n    ' + lbs + proj[i:]
    files = {'project.xml': proj, 'MediaPool/Master/MpFolder.xml': new_mp, T.seqname: new_seq}
    # fresh ids everywhere (keeps internal consistency, avoids clashes on repeated imports)
    files, _ = drtio.remap_all(files)
    drtio.write_drt(files, out_path)
    return files


# ------------------------------------------------------------------ video
def video_codec_label(info):
    c = (info.get('codec') or '').lower()
    h = info.get('h') or 1080
    if c == 'dnxhd':
        mbps = int(round(int(info.get('bitrate') or 0) / 1e6))
        for std in (36, 45, 60, 75, 80, 90, 110, 115, 120, 145, 175, 185, 220, 365, 440):
            if abs(mbps - std) <= max(2, std * 0.05):
                mbps = std; break
        return 'dnxhd_%dp%d' % (h, mbps)
    if c in ('', 'none') or info.get('codec') is None:
        return 'dnxuncompressed_rgb_8b'
    return c


def tc_to_frames(tc, fps=None, drop=None):
    """Timecode string -> frame count. Drop-frame ("01:00:00;00", 29.97/59.94) skips frame numbers
    0,1 (or 0-3 at 60) at the start of every minute except every tenth minute."""
    fps = int(round(fps or FPS_NOM))
    hh, mm, ss, ff = [int(x) for x in tc.replace(';', ':').replace('.', ':').split(':')]
    frames = ((hh * 60 + mm) * 60 + ss) * fps + ff
    if drop is None:
        drop = (';' in tc or '.' in tc) and fps in (30, 60)
    if drop:
        per_min = fps // 15          # 2 at 30, 4 at 60
        minutes = hh * 60 + mm
        frames -= per_min * (minutes - minutes // 10)
    return frames


def mp_video_item(folder_id, dirpath, name, info, fps=None):
    fps = fps or FPS
    import datetime
    tz = None   # local time of this computer, like Resolve itself
    item_id, bt_id = U(), U()
    mt = info.get('mtime') or 0
    datestr = datetime.datetime.fromtimestamp(mt, tz).strftime('%a %b %d %H:%M:%S %Y')
    datestr = datestr[:8] + datestr[8:10].replace('0', ' ', 1) + datestr[10:] if datestr[8] == '0' else datestr
    birth = info.get('birth') or mt
    codecname = video_codec_label(info)
    fb = codec.blob_encode(codec.pb_encode([[1, 2, [[1, 2, [[1, 2, [[6, 0, 4]]]]], [2, 2, bytes.fromhex('0000000000000006ffffffffffffffff')]]]]),
                           ver=2, compress=False).hex()
    clip = codec.blob_encode(codec.pb_encode([[1, 2, dirpath], [2, 2, name], [3, 2, datestr], [5, 2, codecname],
                                              [6, 2, name], [7, 2, U()], [13, 0, int(birth) * 1000000],
                                              [15, 0, 4], [16, 0, 100], [18, 0, 16388]]), ver=2, compress=True).hex()
    rate = struct.pack('<d', fps) + b'\x00' * 8
    time_b = qtb([qs('UniqueId', U()), qs('Timecode', info.get('tc') or '00:00:00:00'), qi('StartFrame', 0),
                  qi('NumFrames', int(info.get('frames') or 0)), qb('FrameRate', rate), qs('DbType', 'BtVideoTime')]).hex()
    w, h = int(info.get('w') or 1920), int(info.get('h') or 1080)
    res = struct.pack('>qq', w, h)
    framesize = 6237184 if codecname.startswith('dnxuncompressed') else 4163584
    geo = qtb([qs('UniqueId', U()), qb('StoredResolution2', res), qb('Resolution', res), qb('OutputResolution', res),
               qi('FrameSize', framesize), qs('DbType', 'BtGeometry')]).hex()
    if codecname.startswith('dnxuncompressed'):
        rad = '80100210022810880132980100d80100'
    else:
        rad = codec.pb_encode([[128, 0, 120], [2, 0, 2], [5, 0, 16], [17, 0, 50], [19, 0, 0], [27, 0, 0]]).hex()
    proxy = qtb([qs('UniqueId', U()), qs('DbType', 'BtVideoProxy'), qi('DataManagerID', 50)]).hex()
    vmeta = qtb([qs('UniqueId', U()), qs('DbType', 'BtVideoMetadata'), qs('ActualReelNumber', info.get('reel') or name)]).hex()
    xml = f'''<Element>
   <Sm2MpVideoClip DbId="{item_id}">
    <FieldsBlob>{fb}</FieldsBlob>
    <Name>{esc(name)}</Name>
    <MpFolder>{folder_id}</MpFolder>
    <UniqueMediaPoolItemId>{U()}</UniqueMediaPoolItemId>
    <MarkIn/>
    <MarkInVideo/>
    <MarkInAudio/>
    <MarkOut/>
    <MarkOutVideo/>
    <MarkOutAudio/>
    <CurPlayheadPosition/>
    <PinsBA/>
    <VirtualAudioTracksBA/>
    <MatteVec/>
    <AudioSource>AUDIO_SOURCE_EMBEDDED</AudioSource>
    <PTZRPresetType>0</PTZRPresetType>
    <SlateTC>-68719476737</SlateTC>
    <Video>
     <BtVideoInfo DbId="{bt_id}">
      <FieldsBlob/>
      <Clip>{clip}</Clip>
      <Time>{time_b}</Time>
      <Geometry>{geo}</Geometry>
      <Radiometry>{rad}</Radiometry>
      <Proxy>{proxy}</Proxy>
      <VideoMetadata>{vmeta}</VideoMetadata>
      <MediaMetadata/>
      <Type>STANDARD_CLIP</Type>
      <Eye>SUB_TRACK_MONO</Eye>
     </BtVideoInfo>
    </Video>
    <EmbeddedAudioVec/>
   </Sm2MpVideoClip>
  </Element>
'''
    return item_id, bt_id, xml


def ti_video_clip(c):
    inn = '<In>%d</In>' % c['in'] if c['in'] else '<In/>'
    eff = c.get('effects_hex') or effect_blob(c.get('effects'))
    effx = '<EffectFiltersBA>%s</EffectFiltersBA>' % eff if eff else '<EffectFiltersBA/>'
    mfps = c.get('fps') or FPS
    tmap = c.get('timemap_hex') or timemap(c['media_frames'], mfps)
    return f'''<Element>
      <Sm2TiVideoClip DbId="{U()}">
       <FieldsBlob>{c['fields_hex']}</FieldsBlob>
       <PrettyType/>
       <Name>{esc(c['name'])}</Name>
       <Start>{c['start']}</Start>
       <Duration>{c['duration']}</Duration>
       <LinkedItemSync/>
       <WasDisbanded>false</WasDisbanded>
       <MarkersBA/>
       <UiMemento>0</UiMemento>
       <Flags>0</Flags>
       <PriorityIndex>0</PriorityIndex>
       {effx}
       <ImportExportMetadataBA/>
       <RenderTextEnabled>true</RenderTextEnabled>
       <RenderTextGanged>true</RenderTextGanged>
       <RenderTextPrefixed>true</RenderTextPrefixed>
       {inn}
       <MixedFrameRateAlignment>0</MixedFrameRateAlignment>
       <MediaRef>{c['media_ref']}</MediaRef>
       <MediaStartTime>{fmtnum(c['media_start'])}</MediaStartTime>
       <MediaFilePath>{esc(c['path'])}</MediaFilePath>
       <MediaReelNumber>{esc(c.get('reel') or '')}</MediaReelNumber>
       <MediaFrameRate>{rate_hex(mfps)}</MediaFrameRate>
       <MediaTimemapBA>{tmap}</MediaTimemapBA>
       <LastChangedTime>0</LastChangedTime>
       <LastRenderedTime>0</LastRenderedTime>
       <IsMarkedForCaching>false</IsMarkedForCaching>
       <IsForceConformed>true</IsForceConformed>
       <MatchConflictState>0</MatchConflictState>
       <UseOppositeSrcForLeftEye>false</UseOppositeSrcForLeftEye>
       <UseOppositeSrcForRightEye>false</UseOppositeSrcForRightEye>
       <RenderCacheBA/>
       <CurrentSelectorIdx>0</CurrentSelectorIdx>
       <IsPreConformed>false</IsPreConformed>
       <PreConformMediaExtents>{c.get('preconform', '')}</PreConformMediaExtents>
       <MediaMetadata/>
       <ImgWidth>-1</ImgWidth>
       <ImgHeight>-1</ImgHeight>
       <Buffer/>
       <ThumbnailDirtyFlag>true</ThumbnailDirtyFlag>
      </Sm2TiVideoClip>
     </Element>
'''
