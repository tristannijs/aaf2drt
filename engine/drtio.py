"""Read/write .drt archives; helpers for UUID remapping and blob re-encoding."""
import re, uuid, zipfile, io
import codec

UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def read_drt(path):
    z = zipfile.ZipFile(path)
    return {n: z.read(n).decode('utf-8') for n in z.namelist()}


def write_drt(files, path):
    # Resolve's own archives: stored entries; keep the same order (project, mediapool, seq)
    order = sorted(files, key=lambda n: (0 if n == 'project.xml' else 1 if n.startswith('MediaPool') else 2, n))
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in order:
            z.writestr(n, files[n])


def remap_uuids(files, keep=()):
    """Give every UUID in the XML text (outside hex blobs) a fresh value, consistently across files."""
    mapping = {}
    def sub(m):
        u = m.group(0)
        if u in keep:
            return u
        if u not in mapping:
            mapping[u] = str(uuid.uuid4())
        return mapping[u]
    out = {}
    for n, s in files.items():
        out[n.replace(n.split('/')[-1], UUID_RE.sub(sub, n.split('/')[-1]))] = UUID_RE.sub(sub, s)
    return out, mapping


def reencode_blobs(files):
    """Decode + re-encode every zstd/raw protobuf blob with our own compressor (tests acceptance)."""
    def sub(m):
        tag, h = m.group(1), m.group(2)
        if len(h) % 2:          # e.g. a long decimal number, not hex data
            return m.group(0)
        b = bytes.fromhex(h)
        if len(b) > 9 and b[:3] == b'\x00\x00\x00' and b[8] in (0x80, 0x81):
            bl = codec.blob_decode(b)
            if bl and codec.pb_try(bl['payload']) is not None:
                nb = codec.blob_encode(bl['payload'], ver=bl['ver'], compress=(bl['flag'] == 0x81))
                return '<%s>%s</%s>' % (tag, nb.hex(), tag)
        return m.group(0)
    return {n: re.sub(r'<(\w+)>([0-9a-f]{18,})</\1>', sub, s) for n, s in files.items()}


def _replace_uuid_bytes(b, mapping):
    def u16(m):
        u = m.group(0)[1::2].decode()
        n = mapping.get(u)
        return ''.join('\x00' + c for c in n).encode('latin-1') if n else m.group(0)
    b = re.sub(rb'(?:\x00[0-9a-f]){8}\x00-(?:\x00[0-9a-f]){4}\x00-(?:\x00[0-9a-f]){4}\x00-(?:\x00[0-9a-f]){4}\x00-(?:\x00[0-9a-f]){12}', u16, b)
    b = re.sub(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
               lambda m: mapping.get(m.group(0).decode(), m.group(0).decode()).encode(), b)
    return b


def remap_all(files, mapping=None):
    """Remap UUIDs everywhere: XML text, inside raw Qt blobs and inside (compressed) blob payloads."""
    mapping = {} if mapping is None else mapping
    # 1) collect every uuid (text + blobs) so the mapping is complete
    def note(u):
        if u not in mapping:
            mapping[u] = str(uuid.uuid4())
    for n, s in files.items():
        for u in UUID_RE.findall(n): note(u)
        for u in UUID_RE.findall(re.sub(r'>[0-9a-f]{18,}<', '><', s)): note(u)
    def blobsub(m):
        tag, h = m.group(1), m.group(2)
        if len(h) % 2:          # e.g. a long decimal number, not hex data
            return m.group(0)
        b = bytes.fromhex(h)
        if len(b) > 9 and b[:3] == b'\x00\x00\x00' and b[8] in (0x80, 0x81):
            bl = codec.blob_decode(b)
            if bl:
                p = _replace_uuid_bytes(bl['payload'], mapping)
                if p != bl['payload']:
                    nb = codec.blob_encode(p, ver=bl['ver'], compress=(bl['flag'] == 0x81))
                    return '<%s>%s</%s>' % (tag, nb.hex(), tag)
                return m.group(0)
        nb = _replace_uuid_bytes(b, mapping)
        return '<%s>%s</%s>' % (tag, nb.hex(), tag)
    out = {}
    for n, s in files.items():
        s2 = re.sub(r'<(\w+)>([0-9a-f]{18,})</\1>', blobsub, s)
        s2 = re.sub(r'(?<![0-9a-f])' + UUID_RE.pattern, lambda m: mapping.get(m.group(0), m.group(0)), s2)
        out[UUID_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), n)] = s2
    return out, mapping
