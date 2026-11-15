"""Lossless codecs for Resolve DRT blobs.

blob  : [u32 BE version][u32 BE payload-len][u8 flag] + payload   flag 0x81 = zstd, 0x80 = raw
qt    : Qt QVariantMap serialization  [u32 version=1][u32 count] + (QString key, QVariant value)*
pb    : protobuf, kept lossless as list of [field, wiretype, value]; wt2 values stay bytes
"""
import struct
try:
    import zstandard as _zs
    def _zdec(b): return _zs.ZstdDecompressor().decompress(b, max_output_size=1 << 26)
    def _zenc(b): return _zs.ZstdCompressor(level=3, write_content_size=True).compress(b)
except ImportError:                      # Python 3.14+: zstd in the standard library
    from compression import zstd as _zs
    def _zdec(b): return _zs.decompress(b)
    def _zenc(b): return _zs.compress(b, level=3)

# ---------------------------------------------------------------- blob
def blob_decode(b):
    if isinstance(b, str):
        b = bytes.fromhex(b)
    if len(b) < 9:
        return None
    ver, ln = struct.unpack('>II', b[:8])
    flag = b[8]
    body = b[9:]
    if flag == 0x81:
        payload = _zdec(body)
    elif flag == 0x80:
        payload = body
    else:
        return None
    return {'ver': ver, 'flag': flag, 'len': ln, 'payload': payload}


def blob_encode(payload, ver=2, compress=None):
    if compress is None:
        compress = len(payload) > 40
    if compress:
        body = _zenc(payload)
        flag = 0x81
    else:
        body = payload
        flag = 0x80
    # header length = flag byte + body (what follows the 8-byte header)
    return struct.pack('>II', ver, 1 + len(body)) + bytes([flag]) + body


# ---------------------------------------------------------------- protobuf (lossless)
def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7f) << s; s += 7
        if not c & 0x80:
            return r, i


def _enc_varint(v):
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        c = v & 0x7f; v >>= 7
        if v:
            out.append(c | 0x80)
        else:
            out.append(c); return bytes(out)


def pb_decode(b):
    out, i = [], 0
    while i < len(b):
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if f == 0:
            raise ValueError('field 0')
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v = b[i:i + 8]; i += 8
            if len(v) != 8: raise ValueError('short')
        elif wt == 5:
            v = b[i:i + 4]; i += 4
            if len(v) != 4: raise ValueError('short')
        elif wt == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]; i += n
            if len(v) != n: raise ValueError('short')
        else:
            raise ValueError('wiretype %d' % wt)
        out.append([f, wt, v])
    return out


def pb_try(b):
    try:
        return pb_decode(b)
    except (ValueError, IndexError):
        return None


def pb_encode(fields):
    out = bytearray()
    for f, wt, v in fields:
        out += _enc_varint((f << 3) | wt)
        if wt == 0:
            out += _enc_varint(v)
        elif wt in (1, 5):
            out += v
        elif wt == 2:
            if isinstance(v, list):
                v = pb_encode(v)
            elif isinstance(v, str):
                v = v.encode('utf-8')
            out += _enc_varint(len(v)) + v
    return bytes(out)


def pb_get(fields, f, default=None):
    for x in fields:
        if x[0] == f:
            return x[2]
    return default


def pb_set(fields, f, wt, v):
    for x in fields:
        if x[0] == f:
            x[1], x[2] = wt, v
            return
    fields.append([f, wt, v])


def d2b(v):
    return struct.pack('<d', v)


def b2d(b):
    return struct.unpack('<d', b)[0]


# ---------------------------------------------------------------- Qt QVariant
QT_BOOL, QT_INT, QT_UINT, QT_LL, QT_ULL, QT_DOUBLE, QT_MAP, QT_LIST, QT_STRING, QT_BYTES = 1, 2, 3, 4, 5, 6, 8, 9, 10, 12


def _qstr(b, i):
    n = struct.unpack('>I', b[i:i + 4])[0]; i += 4
    if n == 0xFFFFFFFF:
        return None, i
    return b[i:i + n].decode('utf-16-be'), i + n


def _enc_qstr(s):
    if s is None:
        return b'\xff\xff\xff\xff'
    e = s.encode('utf-16-be')
    return struct.pack('>I', len(e)) + e


def _qvar(b, i):
    t = struct.unpack('>I', b[i:i + 4])[0]; i += 4
    null = b[i]; i += 1
    if t == QT_BOOL:
        v = bool(b[i]); i += 1
    elif t == QT_INT:
        v = struct.unpack('>i', b[i:i + 4])[0]; i += 4
    elif t == QT_UINT:
        v = struct.unpack('>I', b[i:i + 4])[0]; i += 4
    elif t == QT_LL:
        v = struct.unpack('>q', b[i:i + 8])[0]; i += 8
    elif t == QT_ULL:
        v = struct.unpack('>Q', b[i:i + 8])[0]; i += 8
    elif t == QT_DOUBLE:
        v = struct.unpack('>d', b[i:i + 8])[0]; i += 8
    elif t == QT_STRING:
        v, i = _qstr(b, i)
    elif t == QT_BYTES:
        n = struct.unpack('>I', b[i:i + 4])[0]; i += 4
        if n == 0xFFFFFFFF:
            v = None
        else:
            v = b[i:i + n]; i += n
    elif t == QT_MAP:
        n = struct.unpack('>I', b[i:i + 4])[0]; i += 4
        v = []
        for _ in range(n):
            k, i = _qstr(b, i)
            vv, i = _qvar(b, i)
            v.append([k, vv])
    elif t == QT_LIST:
        n = struct.unpack('>I', b[i:i + 4])[0]; i += 4
        v = []
        for _ in range(n):
            vv, i = _qvar(b, i)
            v.append(vv)
    else:
        raise ValueError('qt type %d at %d' % (t, i))
    return [t, null, v], i


def _enc_qvar(var):
    t, null, v = var
    out = struct.pack('>I', t) + bytes([null])
    if t == QT_BOOL:
        out += bytes([1 if v else 0])
    elif t == QT_INT:
        out += struct.pack('>i', v)
    elif t == QT_UINT:
        out += struct.pack('>I', v)
    elif t == QT_LL:
        out += struct.pack('>q', v)
    elif t == QT_ULL:
        out += struct.pack('>Q', v)
    elif t == QT_DOUBLE:
        out += struct.pack('>d', v)
    elif t == QT_STRING:
        out += _enc_qstr(v)
    elif t == QT_BYTES:
        out += b'\xff\xff\xff\xff' if v is None else struct.pack('>I', len(v)) + v
    elif t == QT_MAP:
        out += struct.pack('>I', len(v))
        for k, vv in v:
            out += _enc_qstr(k) + _enc_qvar(vv)
    elif t == QT_LIST:
        out += struct.pack('>I', len(v))
        for vv in v:
            out += _enc_qvar(vv)
    return out


def qt_decode(b):
    """Top-level: [u32 version][u32 count] + entries. Returns (version, [[key, var], ...])."""
    if isinstance(b, str):
        b = bytes.fromhex(b)
    ver, n = struct.unpack('>II', b[:8])
    i, out = 8, []
    for _ in range(n):
        k, i = _qstr(b, i)
        v, i = _qvar(b, i)
        out.append([k, v])
    if i != len(b):
        raise ValueError('trailing %d bytes' % (len(b) - i))
    return ver, out


def qt_try(b):
    try:
        return qt_decode(b)
    except Exception:
        return None


def qt_encode(ver, entries):
    out = struct.pack('>II', ver, len(entries))
    for k, v in entries:
        out += _enc_qstr(k) + _enc_qvar(v)
    return out


def qt_get(entries, key, default=None):
    for k, v in entries:
        if k == key:
            return v[2]
    return default


def qt_set(entries, key, t, value):
    for e in entries:
        if e[0] == key:
            e[1] = [t, 0, value]
            return
    entries.append([key, [t, 0, value]])


# ---------------------------------------------------------------- pretty printer
def show_any(b, ind=0, maxlen=90):
    """Best-effort recursive display of an unknown blob (hex/bytes)."""
    pad = ' ' * ind
    if isinstance(b, str):
        b = bytes.fromhex(b)
    bl = blob_decode(b) if len(b) > 9 and b[:3] == b'\x00\x00\x00' and b[8] in (0x80, 0x81) else None
    if bl:
        print(pad + '[blob v%d flag %02x]' % (bl['ver'], bl['flag']))
        return show_any(bl['payload'], ind + 2)
    q = qt_try(b)
    if q:
        print(pad + '[qt v%d]' % q[0])
        _show_qt(q[1], ind + 2)
        return
    p = pb_try(b)
    if p:
        _show_pb(p, ind)
        return
    print(pad + 'raw ' + b[:maxlen].hex())


def _show_qt(entries, ind):
    pad = ' ' * ind
    for k, (t, null, v) in entries:
        if t == QT_BYTES and v:
            print(pad + '%s (bytes %d):' % (k, len(v)))
            show_any(v, ind + 2)
        elif t == QT_MAP:
            print(pad + '%s {map}' % k); _show_qt(v, ind + 2)
        else:
            print(pad + '%s = %r (t%d)' % (k, v, t))


def _show_pb(fields, ind):
    pad = ' ' * ind
    for f, wt, v in fields:
        if wt == 2:
            sub = pb_try(v) if v else None
            try:
                txt = v.decode('utf-8')
                printable = len(v) > 0 and all(31 < ord(c) < 0xfffe for c in txt)
            except UnicodeDecodeError:
                printable = False
            if printable and (sub is None or len(v) < 4 or txt.isprintable() and not any(ord(c) < 32 for c in txt) and sum(c.isalnum() for c in txt) > len(txt) * 0.6):
                print(pad + '%d: %r' % (f, txt))
            elif sub is not None and len(v):
                print(pad + '%d {' % f); _show_pb(sub, ind + 2); print(pad + '}')
            elif v[:4] == b'\x00\x00\x00\x01' and qt_try(v):
                print(pad + '%d [qt]:' % f); _show_qt(qt_decode(v)[1], ind + 2)
            else:
                print(pad + '%d: bytes %s' % (f, v[:60].hex()))
        elif wt == 1:
            print(pad + '%d: d %r' % (f, b2d(v)))
        elif wt == 5:
            print(pad + '%d: f %r' % (f, struct.unpack('<f', v)[0]))
        else:
            print(pad + '%d: %d' % (f, v))
