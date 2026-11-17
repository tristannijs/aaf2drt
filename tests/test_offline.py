"""Checks that need no Avid media: data files load, the mixer model and .drt template round-trip."""
import os
import struct
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import codec  # noqa: E402
import drtio  # noqa: E402
import flmodel  # noqa: E402
import gen  # noqa: E402

PER_CHANNEL = 1964   # bytes Resolve adds to the Fairlight model per audio channel


def test_mixer_model_grows_per_channel():
    base = len(flmodel.raw_model("m"))
    assert len(flmodel.raw_model("s")) == base + PER_CHANNEL
    assert len(flmodel.raw_model("ms")) == base + 2 * PER_CHANNEL
    assert len(flmodel.raw_model("mmmmmmmmmmmmssssss")) == base + 23 * PER_CHANNEL


def test_mixer_model_blob_is_qcompress():
    blob = flmodel.qt_blob("mms")
    n = struct.unpack(">I", blob[:4])[0]
    assert len(zlib.decompress(blob[4:])) == n


def test_template_roundtrip():
    files = drtio.read_drt(os.path.join(ROOT, "engine", "template.drt"))
    assert "project.xml" in files and "MediaPool/Master/MpFolder.xml" in files
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.drt")
        drtio.write_drt(files, p)
        assert drtio.read_drt(p) == files


def test_template_has_no_personal_paths():
    import re
    proj = drtio.read_drt(os.path.join(ROOT, "engine", "template.drt"))["project.xml"]
    m = re.search(r'<SM_Config DbId="[^"]+">\s*<FieldsBlob>([0-9a-f]+)</FieldsBlob>', proj)
    setup = dict((e[0], e[1][2]) for e in codec.qt_decode(m.group(1))[1])["SetupBA"]
    payload = codec.blob_decode(setup)["payload"]
    for path in re.findall(rb"/Users/[^\x00-\x1f\"]*", payload):
        assert path.startswith(b"/Users/Shared/"), path


def test_metadata_blob_decodes():
    h = gen.audio_metadata_blob("x.wav", ["A01", "A02"], b"<x/>", "take")
    q = codec.qt_decode(h)
    payload = codec.blob_decode(q[1][0][1][2])["payload"]
    assert b"A02" in payload and b"x.wav" in payload


def test_drop_frame_timecode():
    assert gen.tc_to_frames("01:00:00;00", 30) == 107892
    assert gen.tc_to_frames("00:10:00;00", 30) == 17982
    assert gen.tc_to_frames("00:01:00;02", 30) == 1800
    assert gen.tc_to_frames("01:00:00;00", 60) == 215784
    assert gen.tc_to_frames("01:00:00:00", 30) == 108000     # non-drop stays plain
    assert gen.tc_to_frames("01:00:00:00", 25) == 90000


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_dverb_params():
    import aafmodel
    head = bytes.fromhex('60000000010000006967694462727652') + bytes(40)
    # round 4: Room 2, Medium, -19.9 dB, mix 0 %, diffusion 20 %, HF 19.95 kHz, LP 200 Hz, pre-delay 300 ms
    d = aafmodel.dverb_params(head + bytes.fromhex('2aaaaaaa0000000099fe898e80000000b3d70a3e6147ae147f5c28f5800000007fffffff00000000'))
    assert d['algo'] == 'Room 2' and d['size'] == 'Medium'
    assert abs(d['input_db'] + 19.9) < 0.2 and d['mix'] < 0.5 and abs(d['diffusion'] - 20) < 0.5
    assert abs(d['hf_cut'] - 19950) < 10 and abs(d['lp'] - 200) < 1 and abs(d['predelay'] - 300) < 0.5
    # Dog Bark: Ambient, Large, 490 ms, mix 66 %
    d = aafmodel.dverb_params(head + bytes.fromhex('555555547fffffff2100000028ba2e8bd080ce1728f5c28f800000008000000080000000' + '00000000'))
    assert d['algo'] == 'Ambient' and d['size'] == 'Large' and abs(d['decay'] - 0.49) < 0.05 and abs(d['mix'] - 66) < 1


def test_clip_fx_reverb_roundtrip():
    import gen, zlib, struct
    blk = gen.clip_fx_reverb({'Dry/Wet': 0.25, 'T@-60dB': 0.5})
    raw = zlib.decompress(blk[4:])
    assert struct.unpack('>I', blk[:4])[0] == len(raw) and b'bmd:Reverb' in raw
    o, vals = 516, {}
    while raw[o:o + 64].split(b'\0')[0]:
        vals[raw[o:o + 64].split(b'\0')[0].decode()] = struct.unpack('<f', raw[o + 64:o + 68])[0]; o += 68
    assert abs(vals['Dry/Wet'] - 0.25) < 1e-6 and abs(vals['T@-60dB'] - 0.5) < 1e-6


def test_track_reverb_insert():
    """Model with Reverb inserts grows exactly as in Resolve 21.1's own exports (one on a stereo track: +2886 bytes,
    one on a mono track: +2873, both: +5146)."""
    import flmodel
    base = len(flmodel.raw_model('sm'))
    assert len(flmodel.raw_model('sm', reverbs={0: {}})) - base == 2886
    assert len(flmodel.raw_model('sm', reverbs={1: {}})) - base == 2873
    two = flmodel.raw_model('sm', reverbs={0: {}, 1: {'Dry/Wet': 0.3}})
    assert len(two) - base == 5146
    secs = [s[1] for s in flmodel._sections(two)[0]]
    assert secs.index('EffectPresets') == secs.index('EffectContext') + 1


def test_existing_wav_reused_only_for_same_segment():
    import numpy as np
    import aaf2drt
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.wav")
        s = np.zeros((4800, 2), dtype=np.int32)
        aaf2drt.write_bwf(p, s, 48000, 24, 123456, ["A", "B"], description="4.45/8/2")
        size = os.path.getsize(p)
        assert aaf2drt.wav_matches(p, size, 123456, "4.45/8/2")
        assert not aaf2drt.wav_matches(p, size, 999999, "4.45/8/2")   # same size, other source position
        assert not aaf2drt.wav_matches(p, size, 123456, "4.45/9/2")
        assert not aaf2drt.wav_matches(os.path.join(d, "missing.wav"), size, 123456, "4.45/8/2")


def test_juce_plugin_to_au_record():
    import gen, aafmodel, zlib, struct, plistlib
    xml = b'<?xml version="1.0"?><X a="1"/>\0'
    state = b'VC2!' + struct.pack('<I', len(xml)) + xml
    chunk = b'\xe8\0\0\0' + b'junk' * 70 + state + b'\0' * 8            # AAX chunk with the JUCE state inside
    assert aafmodel.juce_state(chunk) == state and aafmodel.juce_state(b'nothing') is None
    raw = zlib.decompress(gen.clip_fx([gen.au_record('1399090297', '1349280628', 'Soundly Place it', state)])[4:])
    assert raw[:4] == b'f\x88fw' and struct.unpack_from('<II', raw, 12) == (5, 1)
    ch, ln, kind = struct.unpack_from('<III', raw, 20)
    assert kind == 1 and raw[32:56] == b'au:1399090297:1349280628' and len(raw) == 20 + 12 + 412 + ln
    pl = plistlib.loads(raw[444:])
    assert pl['jucePluginState'] == state and pl['manufacturer'] == 1399090297 and pl['type'] == 0x61756678


def test_juce_vst3_insert():
    import flmodel, struct
    assert flmodel.juce_vst3_id(1399090297, 1349280628) == 'vst3:ABCDEF019182FAEB53646C79506C6374'  # Place it
    tpl = open(os.path.join(os.path.dirname(flmodel.__file__), 'fl_vst3juce.bin'), 'rb').read()
    i = tpl.find(b'VC2!'); n = struct.unpack_from('<I', tpl, i + 4)[0]
    assert flmodel.juce_vst3_state(tpl[i:i + 8 + n]) == tpl                      # rebuilds Resolve's own state
    d = flmodel.juce_vst3_state()
    assert struct.unpack_from('<I', d, 4)[0] == len(d) - 16 and b'<DEFAULT/>' in d
    base = len(flmodel.raw_model('m'))
    m = flmodel.raw_model('m', inserts={0: flmodel.plugin_insert('vst3:X', 'Plug', d)})
    secs = [s[1] for s in flmodel._sections(m)[0]]
    assert 'EffectPresets' in secs and len(m) > base


def test_multiple_track_inserts():
    """Several inserts on one track: one EffectPresets record per instance, byte 1 = first channel, byte 3 = slot
    (checked against Resolve 21.1, which shows them in slots 1, 2, 3 with their own settings); max 6 per track."""
    R = flmodel.reverb_insert
    P = flmodel.plugin_insert(flmodel.juce_vst3_id(1399090297, 1349280628), 'Place it', flmodel.juce_vst3_state())
    x = bytes(flmodel.raw_model('ms', inserts={0: [R({'Dry/Wet': 0.2}), P, R({})], 1: [R({})] * 8}))
    secs, _ = flmodel._sections(x)
    s = next(q for q in secs if q[1] == 'EffectPresets')
    body = x[s[0] + 48:s[0] + 48 + s[3]]
    n = struct.unpack_from('<I', body, 4)[0]
    assert n == 3 + 6
    o, heads = 8, []
    for _ in range(n):
        _, ln, kind = struct.unpack_from('<III', body, o)
        heads.append((body[o + 1], body[o + 3], kind))
        o += 2136 if kind == 2 else 424 + ln
    assert heads[:3] == [(0, 0, 2), (0, 1, 1), (0, 2, 2)]
    assert [h[:2] for h in heads[3:]] == [(1, k) for k in range(6)]


def test_channel_eq_vectors():
    """Fairlight channel EQ as per-channel vectors, values as Resolve 21.1 writes them (eq exports)."""
    bands = [{'on': 1, 'freq': 100, 'slope': 24},
             {'on': 1, 'type': 'high_shelf', 'freq': 2075, 'gain': 0, 'q': 6},
             {'on': 1, 'type': 'low_shelf', 'freq': 1000, 'gain': 6, 'q': 0.3},
             {'on': 1, 'type': 'notch', 'freq': 1223, 'gain': -12.5, 'q': 10.3},
             {'on': 1, 'type': 'bell', 'freq': 6048, 'gain': 0, 'q': 1.0},
             {'on': 1, 'freq': 8000, 'slope': 6}]
    v = flmodel.channel_eq(bands, -9.7)
    assert v[223] == 1 and v[224] == -97
    assert [v[241 + i] for i in range(6)] == [100, 2075, 1000, 1223, 6048, 8000]
    assert [v[249 + i] for i in range(6)] == [6, 3, 1, 4, 2, 5]
    assert [v[226 + i] for i in range(4)] == [0, 60, -125, 0]
    assert [v[234 + i] for i in range(4)] == [100, 15, 165, 25]
    assert v[1214] == 3 and v[1219] == 0 and v[820] == 119 and v[825] == 162
    x = bytes(flmodel.raw_model('ms', eqs={1: v}))
    i = x.find(struct.pack('<III', 1, 224, 3))
    assert struct.unpack_from('<3i', x, i + 12) == (0, -97, -97)
