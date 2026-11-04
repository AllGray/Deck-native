"""NSO module formats for the binary census: NSO0 header, MOD0, dynamic section, dynamic symbols, relocations, .eh_frame_hdr / .eh_frame.
Read-only. Follows nxnative's dm/tools/nso.py (loader checks), nso_code.py (eh_frame pointer encodings) and nso_syms.py (dynamic symbols),
kept self-contained here so the census can be re-run on its own."""
import hashlib, struct
from typing import NamedTuple

import lz4.block

SEG_NAMES = ('text', 'ro', 'data')

DT_NAMES = {0: 'NULL', 1: 'NEEDED', 2: 'PLTRELSZ', 3: 'PLTGOT', 4: 'HASH', 5: 'STRTAB', 6: 'SYMTAB', 7: 'RELA', 8: 'RELASZ', 9: 'RELAENT',
            10: 'STRSZ', 11: 'SYMENT', 12: 'INIT', 13: 'FINI', 14: 'SONAME', 15: 'RPATH', 16: 'SYMBOLIC', 17: 'REL', 18: 'RELSZ',
            19: 'RELENT', 20: 'PLTREL', 21: 'DEBUG', 22: 'TEXTREL', 23: 'JMPREL', 24: 'BIND_NOW', 25: 'INIT_ARRAY', 26: 'FINI_ARRAY',
            27: 'INIT_ARRAYSZ', 28: 'FINI_ARRAYSZ', 29: 'RUNPATH', 30: 'FLAGS', 32: 'PREINIT_ARRAY', 33: 'PREINIT_ARRAYSZ',
            35: 'RELRSZ', 36: 'RELR', 37: 'RELRENT', 0x6ffffef5: 'GNU_HASH', 0x6ffffff9: 'RELACOUNT', 0x6ffffffa: 'RELCOUNT',
            0x6ffffffb: 'FLAGS_1', 0x6ffffff0: 'VERSYM', 0x6ffffffe: 'VERNEED', 0x6fffffff: 'VERNEEDNUM'}

R_NAMES = {0: 'NONE', 257: 'ABS64', 258: 'ABS32', 260: 'PREL64', 1024: 'COPY', 1025: 'GLOB_DAT', 1026: 'JUMP_SLOT', 1027: 'RELATIVE',
           1028: 'TLS_DTPMOD64', 1029: 'TLS_DTPREL64', 1030: 'TLS_TPREL64', 1031: 'TLSDESC', 1032: 'IRELATIVE'}


class Module(NamedTuple):
    name: str
    image: bytes          # text + ro + data at their memory offsets (bss not included)
    segs: dict            # name -> (mem_off, size, file_size, compressed)
    bss: int
    build_id: bytes
    api_info: tuple       # (ro-relative offset, size)
    dynstr: tuple
    dynsym: tuple
    file_size: int


def load_nso(path, name=None):
    d = open(path, 'rb').read()
    if d[:4] != b'NSO0':
        raise ValueError(f'{path}: not NSO0')
    flags = struct.unpack_from('<I', d, 0xC)[0]
    segs, raw_segs = {}, []
    for i, n in enumerate(SEG_NAMES):
        fo, mo, sz = struct.unpack_from('<III', d, 0x10 + i * 0x10)
        csz = struct.unpack_from('<I', d, 0x60 + i * 4)[0]
        comp = bool(flags >> i & 1)
        raw = d[fo:fo + (csz if comp else sz)]
        if comp:
            raw = lz4.block.decompress(raw, uncompressed_size=sz)
        if len(raw) != sz:
            raise ValueError(f'{path}: {n} decompresses to {len(raw):#x}, header says {sz:#x}')
        if flags >> (3 + i) & 1 and hashlib.sha256(raw).digest() != d[0xA0 + 0x20 * i:0xC0 + 0x20 * i]:
            raise ValueError(f'{path}: {n} SHA-256 mismatch')
        segs[n] = (mo, sz, csz if comp else sz, comp)
        raw_segs.append((mo, raw))
    end = max(mo + len(r) for mo, r in raw_segs)
    img = bytearray(end)
    for mo, r in raw_segs:
        img[mo:mo + len(r)] = r
    bss = struct.unpack_from('<I', d, 0x3C)[0]
    api = struct.unpack_from('<II', d, 0x88)
    dstr = struct.unpack_from('<II', d, 0x90)
    dsym = struct.unpack_from('<II', d, 0x98)
    return Module(name or str(path), bytes(img), segs, bss, d[0x40:0x60], api, dstr, dsym, len(d))


class Mod0(NamedTuple):
    off: int
    dynamic: int
    bss_start: int
    bss_end: int
    ehh_start: int
    ehh_end: int
    module_object: int


def mod0(img):
    m0 = struct.unpack_from('<I', img, 4)[0]
    if img[m0:m0 + 4] != b'MOD0':
        raise ValueError('no MOD0')
    rel = struct.unpack_from('<6i', img, m0 + 4)
    return Mod0(m0, *(m0 + r for r in rel))


def dynamic(img, dyn_off):
    """[(tag, value)] up to DT_NULL"""
    out = []
    for i in range(8192):
        t, v = struct.unpack_from('<qQ', img, dyn_off + i * 16)
        if t == 0:
            break
        out.append((t, v))
    return out


class Sym(NamedTuple):
    index: int
    name: str
    value: int
    size: int
    bind: int     # 0 local, 1 global, 2 weak
    type: int     # 0 notype, 1 object, 2 func, 6 tls
    shndx: int


def symbols(img, mod):
    """Dynamic symbols (index 0 skipped). Count from the NSO header's dynsym extent (ro-relative), checked against DT_HASH when present."""
    tags = dict(dynamic(img, mod0(img).dynamic))
    symtab, strtab = tags[6], tags[5]
    n_hdr = mod.dynsym[1] // 24
    n_hash = struct.unpack_from('<I', img, tags[4] + 4)[0] if 4 in tags else None
    n = n_hdr or n_hash
    out = []
    for i in range(1, n):
        nm, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', img, symtab + i * 24)
        s = bytes(img[strtab + nm:img.index(b'\0', strtab + nm)]).decode('latin1')
        out.append(Sym(i, s, value, size, info >> 4, info & 0xF, shndx))
    return out, {'from_header': n_hdr, 'from_hash': n_hash}


class Rela(NamedTuple):
    offset: int
    type: int
    sym: int
    addend: int


def relocations(img, tags):
    """(rela list, jmprel list, relr address list)"""
    def rela(off, sz):
        return [Rela(o, i & 0xFFFFFFFF, i >> 32, a) for o, i, a in (struct.unpack_from('<QQq', img, off + k) for k in range(0, sz, 24))]
    ra = rela(tags[7], tags[8]) if 7 in tags else []
    jr = rela(tags[23], tags[2]) if 23 in tags else []
    relr = []
    if 36 in tags:   # DT_RELR
        base = 0
        for k in range(0, tags[35], 8):
            e = struct.unpack_from('<Q', img, tags[36] + k)[0]
            if e & 1 == 0:
                relr.append(e); base = e + 8
            else:
                b = e >> 1
                for j in range(63):
                    if b >> j & 1:
                        relr.append(base + j * 8)
                base += 63 * 8
    return ra, jr, relr


# ---- .eh_frame_hdr / .eh_frame ----

def _read_enc(img, o, enc, ehs, datarel=0):
    if enc == 0xFF:
        return None, 0
    fmt, app = enc & 0xF, enc & 0x70
    if fmt == 0x3: v, sz = struct.unpack_from('<I', img, o)[0], 4
    elif fmt == 0xB: v, sz = struct.unpack_from('<i', img, o)[0], 4
    elif fmt == 0x2: v, sz = struct.unpack_from('<H', img, o)[0], 2
    elif fmt == 0xA: v, sz = struct.unpack_from('<h', img, o)[0], 2
    elif fmt in (0x0, 0x4): v, sz = struct.unpack_from('<Q', img, o)[0], 8
    elif fmt == 0xC: v, sz = struct.unpack_from('<q', img, o)[0], 8
    elif fmt == 0x1: v, o2 = uleb(img, o); return v, o2 - o
    elif fmt == 0x9: v, o2 = sleb(img, o); return v, o2 - o
    else: raise ValueError(f'pointer encoding {enc:#x} at {o:#x}')
    if app == 0x10: v += o
    elif app == 0x30: v += datarel
    return v, sz


def uleb(img, o):
    r = sh = 0
    while True:
        b = img[o]; o += 1; r |= (b & 0x7F) << sh; sh += 7
        if not b & 0x80: return r, o


def sleb(img, o):
    r = sh = 0
    while True:
        b = img[o]; o += 1; r |= (b & 0x7F) << sh; sh += 7
        if not b & 0x80:
            if b & 0x40: r -= 1 << sh
            return r, o


def eh_frame_hdr(img, m):
    """{'version', 'eh_frame_ptr', 'fde_count', 'table': [(pc, fde_off)]}"""
    ehs = m.ehh_start
    ver, penc, cenc, tenc = img[ehs:ehs + 4]
    o = ehs + 4
    ptr, s = _read_enc(img, o, penc, ehs, ehs); o += s
    n, s = _read_enc(img, o, cenc, ehs, ehs); o += s
    table = []
    for _ in range(n or 0):
        a, s = _read_enc(img, o, tenc, ehs, ehs); o += s
        f, s = _read_enc(img, o, tenc, ehs, ehs); o += s
        table.append((a, f))
    return {'version': ver, 'enc': (penc, cenc, tenc), 'eh_frame_ptr': ptr, 'fde_count': n, 'table': table, 'end': o}


class Fde(NamedTuple):
    off: int
    cie: int
    start: int
    size: int
    lsda: int     # 0 when none


def walk_eh_frame(img, start, limit):
    """Walk .eh_frame from start until a zero terminator (or limit). Returns (cies {off: info}, fdes [Fde], end offset)."""
    cies, fdes = {}, []
    o = start
    while o + 4 <= limit:
        ln = struct.unpack_from('<I', img, o)[0]
        if ln == 0:
            return cies, fdes, o + 4
        hdr = 4
        if ln == 0xFFFFFFFF:
            ln = struct.unpack_from('<Q', img, o + 4)[0]; hdr = 12
        body = o + hdr
        cid = struct.unpack_from('<I', img, body)[0]
        if cid == 0:
            cies[o] = _parse_cie(img, o, body)
        else:
            cie_off = body - cid
            if cie_off not in cies:
                cies[cie_off] = _parse_cie(img, cie_off, cie_off + 4)
            c = cies[cie_off]
            p = body + 4
            st, s = _read_enc(img, p, c['fde_enc'], 0); p += s
            sz, s = _read_enc(img, p, c['fde_enc'] & 0xF, 0); p += s
            lsda = 0
            if c['aug'].startswith('z'):
                alen, p2 = uleb(img, p)
                if 'L' in c['aug'] and c['lsda_enc'] != 0xFF:
                    v, _ = _read_enc(img, p2, c['lsda_enc'], 0)
                    lsda = v or 0
                    if (c['lsda_enc'] & 0xF) in (0x3, 0xB) and v is not None and struct.unpack_from('<I', img, p2)[0] == 0:
                        lsda = 0
            fdes.append(Fde(o, cie_off, st, sz, lsda))
        o = body + ln
    return cies, fdes, o


def _parse_cie(img, off, body):
    p = body + 4
    ver = img[p]; p += 1
    z = img.index(b'\0', p)
    aug = bytes(img[p:z]).decode('latin1'); p = z + 1
    caf, p = uleb(img, p)
    daf, p = sleb(img, p)
    if ver == 1:
        ra = img[p]; p += 1
    else:
        ra, p = uleb(img, p)
    fde_enc, lsda_enc, pers = 0, 0xFF, None
    if aug.startswith('z'):
        _, p = uleb(img, p)
        for ch in aug[1:]:
            if ch == 'R': fde_enc = img[p]; p += 1
            elif ch == 'L': lsda_enc = img[p]; p += 1
            elif ch == 'P':
                pe = img[p]; p += 1
                pers, s = _read_enc(img, p, pe, 0); p += s
                if pe & 0x80 and pers is not None:   # indirect: pointer to the personality pointer
                    pers = ('indirect', pers)
            elif ch in ('S', 'B'): pass
            else: break
    return {'version': ver, 'aug': aug, 'code_align': caf, 'data_align': daf, 'ra': ra, 'fde_enc': fde_enc, 'lsda_enc': lsda_enc, 'personality': pers}


def module_name_from_ro(img, ro_off):
    """The 'module path' record at the start of ro: u32 0, u32 length, then the name (we only report its basename)."""
    z, ln = struct.unpack_from('<II', img, ro_off)
    if z != 0 or not 0 < ln < 0x200:
        return None
    s = bytes(img[ro_off + 8:ro_off + 8 + ln]).split(b'\0')[0].decode('latin1', 'replace')
    return s.replace('\\', '/').rsplit('/', 1)[-1]
