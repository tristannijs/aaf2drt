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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
