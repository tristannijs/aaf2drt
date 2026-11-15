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
