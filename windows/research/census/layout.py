"""Census part 1: layout, MOD0, dynamic section, symbols, cross-module imports, relocations, .eh_frame FDEs, npdm SVC mask.
Usage: py -3 -I layout.py <ExeFS dir> <scratch dir>
Writes <scratch>/layout.json (counts and lists only, no code bytes) and <scratch>/fdes_<module>.json (function ranges, for insns.py)."""
import collections, json, os, re, struct, subprocess, sys

sys.dont_write_bytecode = True   # keep this folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nsofmt as F

MODULES = ('rtld', 'main', 'subsdk0', 'sdk')
LOAD_ORDER = ('main', 'subsdk0', 'sdk')       # Horizon's rtld resolves symbols over the loaded modules in this order
CXXFILT = r'C:\Program Files\LLVM\bin\llvm-cxxfilt.exe'


def demangle(names):
    names = list(names)
    if not names:
        return {}
    if os.path.exists(CXXFILT):
        p = subprocess.run([CXXFILT], input='\n'.join(names) + '\n', capture_output=True, text=True, encoding='latin1')
        out = p.stdout.split('\n')
        return dict(zip(names, out[:len(names)]))
    return {n: n for n in names}


def split_top(s, sep='::'):
    """Split s at sep where not inside <> or ()"""
    parts, depth, cur, i = [], 0, '', 0
    while i < len(s):
        c = s[i]
        if c in '<(':
            depth += 1
        elif c in '>)':
            depth -= 1
        if depth == 0 and s.startswith(sep, i):
            parts.append(cur); cur = ''; i += len(sep); continue
        cur += c; i += 1
    parts.append(cur)
    return parts


def qualname(dem):
    """Components of the qualified name of a demangled symbol"""
    for pre in ('vtable for ', 'typeinfo for ', 'typeinfo name for ', 'guard variable for ', 'VTT for ', 'construction vtable for ', 'non-virtual thunk to ', 'virtual thunk to ', 'covariant return thunk to '):
        if dem.startswith(pre):
            dem = dem[len(pre):]
    # cut the parameter list: the first '(' at template depth 0
    depth, cut = 0, len(dem)
    for i, c in enumerate(dem):
        if c == '<': depth += 1
        elif c == '>': depth -= 1
        elif c == '(' and depth == 0:
            if dem.startswith('operator', max(0, i - 8)) or dem[max(0, i - 10):i].endswith('operator()'):
                continue
            cut = i; break
    pre = dem[:cut].strip()
    k = pre.find('operator')
    head, tail = (pre[:k], pre[k:]) if k >= 0 else (pre, '')
    depth, sp = 0, -1
    for i, c in enumerate(head):   # drop a return type ("void nn::...", "nn::Result nn::err::...") at template depth 0
        if c == '<': depth += 1
        elif c == '>': depth -= 1
        elif c == ' ' and depth == 0: sp = i
    return split_top(head[sp + 1:] + tail)


LIBM = re.compile(r'(a?(sin|cos|tan)h?|atan2|exp2?|expm1|log(10|2|1p|b)?|pow|sqrt|cbrt|fmod|frexp|ldexp|modf|scalbn|hypot|floor|ceil|round|trunc|fabs|nan|erf|tgamma|lgamma|remainder|copysign|fmin|fmax|fma)[fl]?$')


def c_area(raw):
    if raw.startswith('nvn'): return 'C: nvn API'
    if raw.startswith('glslc'): return 'C: glslc API'
    if raw.startswith(('__cxa_', '_Unwind_', '__gxx_', '__dynamic_cast')): return 'C++ ABI / unwind'
    if raw in ('setjmp', 'longjmp', '_setjmp', '_longjmp', 'sigsetjmp', 'siglongjmp'): return 'C: setjmp/longjmp'
    if raw.startswith(('__nnmusl', '_nnmusl', '__nn')): return 'C: __nn* / nnmusl runtime'
    if raw.startswith('nnos'): return 'C: nnos* (nn::os C API)'
    if raw.startswith(('nnsocket', 'nnssl', 'inet_', 'hton', 'ntoh')): return 'C: nnsocket*/nnssl*/inet'
    if raw.startswith(('nnResult', 'nndetail')): return 'C: nnResult*/nndetail*'
    if raw.startswith('pthread_'): return 'C: pthread'
    if LIBM.match(raw): return 'C: libm'
    if re.match(r'(mem|str|wcs|mbs|to(lower|upper)$|is(al|di|sp|pr|cn|xd|up|lo|pu|gr|bl))', raw): return 'C: string/memory/ctype'
    if re.match(r'(f(open|close|read|write|seek|tell|flush|gets|puts|putc|printf|error|stat)|v?s?n?printf|vf?printf|sscanf|open(dir)?$|close(dir)?$|read(dir)?$|stat$|rewind|std(in|out|err)$)', raw): return 'C: stdio/files'
    if re.match(r'(clock|time$|gmtime|localtime|mktime|strftime|difftime|nanosleep|gettimeofday)', raw): return 'C: time'
    if re.match(r'(malloc|free|calloc|realloc|aligned_alloc|memalign|posix_memalign)$', raw): return 'C: malloc family'
    return 'C: other libc'


def area(raw, dem):
    if not raw.startswith('_Z'):
        return c_area(raw)
    c = qualname(dem)
    if c[0] == 'nn' and len(c) >= 3: return 'nn::' + c[1]
    if c[0] == 'nn': return 'nn (top level)'
    if c[0] == 'std': return 'std:: (libc++)'
    if c[0] == 'nv' and len(c) >= 2: return 'nv::'
    if c[0] in ('nvn', 'nvnTool'): return c[0] + '::'
    if c[0].startswith('nvn'): return 'nvn (C++-mangled)'
    if len(c) == 1: return 'global C++ (' + ('operator' if c[0].startswith('operator') else 'function') + ')'
    return c[0] + '::'


def subarea(raw, dem):
    if not raw.startswith('_Z'):
        return None
    c = qualname(dem)
    if c[0] == 'nn' and len(c) >= 4:
        return '::'.join(c[:3])
    return None


def npdm_svcs(path):
    d = open(path, 'rb').read()
    if d[:4] != b'META':
        return None
    aci_off, aci_sz, acid_off, acid_sz = struct.unpack_from('<IIII', d, 0x70)
    out = {}
    for tag, off in (('ACI0', aci_off), ('ACID', acid_off)):
        if tag == 'ACI0':
            kc_off, kc_sz = struct.unpack_from('<II', d, off + 0x30)
            sac_off, sac_sz = struct.unpack_from('<II', d, off + 0x28)
        else:   # the ACID magic follows the signature(s) and key: 0x200 bytes in the classic format, 0x300 in this game's npdm
            mg = d.index(b'ACID', off)
            kc_off, kc_sz = struct.unpack_from('<II', d, mg + 0x30)
            sac_off, sac_sz = struct.unpack_from('<II', d, mg + 0x28)
        svcs, kinds = set(), collections.Counter()
        for k in range(0, kc_sz, 4):
            w = struct.unpack_from('<I', d, off + kc_off + k)[0]
            ones = 0
            while w >> ones & 1: ones += 1
            kinds[ones] += 1
            if ones == 4:
                mask, idx = (w >> 5) & 0xFFFFFF, w >> 29
                svcs.update(idx * 24 + b for b in range(24) if mask >> b & 1)
        # service access control: entries (u8 ctrl: len-1 in bits 0-2, server bit 7), names
        sac = []
        p = off + sac_off
        while p < off + sac_off + sac_sz:
            ctrl = d[p]; ln = (ctrl & 7) + 1
            sac.append(('S ' if ctrl & 0x80 else '') + d[p + 1:p + 1 + ln].decode('latin1'))
            p += 1 + ln
        out[tag] = {'svcs': sorted(svcs), 'kc_kinds': dict(kinds), 'services': sac}
    return out


def api_info(img, mod):
    ro = mod.segs['ro'][0]
    off, sz = mod.api_info
    raw = bytes(img[ro + off:ro + off + sz])
    items = [s.decode('latin1') for s in raw.split(b'\0') if s]
    return items


def fde_stats(img, mod, m0):
    text_end = mod.segs['text'][1]
    hdr = F.eh_frame_hdr(img, m0)
    cies, fdes, end = F.walk_eh_frame(img, hdr['eh_frame_ptr'], len(img))
    ranges = sorted((f.start, f.start + f.size) for f in fdes)
    hdr_starts = sorted(a for a, _ in hdr['table'])
    walk_starts = sorted(f.start for f in fdes)
    sizes = sorted(f.size for f in fdes)
    # union coverage within text
    cov, last_end, gaps, overlaps = 0, 0, [], 0
    for s, e in ranges:
        if s >= text_end: break
        e = min(e, text_end)
        if s > last_end:
            gaps.append((last_end, s))
        elif s < last_end:
            overlaps += 1
        cov += max(0, e - max(s, last_end))
        last_end = max(last_end, e)
    if last_end < text_end:
        gaps.append((last_end, text_end))
    def pct(p): return sizes[min(len(sizes) - 1, int(p * len(sizes)))] if sizes else 0
    buckets = [(0, 16), (17, 32), (33, 64), (65, 128), (129, 256), (257, 512), (513, 1024), (1025, 4096), (4097, 16384), (16385, 65536), (65537, 1 << 40)]
    hist = [[lo, hi, sum(1 for x in sizes if lo <= x <= hi)] for lo, hi in buckets]
    pers = collections.Counter()
    for c in cies.values():
        pers[(c['aug'], str(c['personality']))] += 0
    for f in fdes:
        c = cies[f.cie]
        pers[(c['aug'], str(c['personality']))] += 1
    out = {
        'eh_frame_hdr': [m0.ehh_start, m0.ehh_end], 'eh_frame': [hdr['eh_frame_ptr'], end], 'hdr_version': hdr['version'], 'hdr_enc': hdr['enc'],
        'hdr_fde_count': hdr['fde_count'], 'walk_fde_count': len(fdes), 'cie_count': len(cies),
        'hdr_matches_walk': hdr_starts == walk_starts,
        'fdes_with_lsda': sum(1 for f in fdes if f.lsda), 'zero_size_fdes': sum(1 for f in fdes if f.size == 0),
        'fdes_outside_text': sum(1 for f in fdes if f.start >= text_end or f.start < 0),
        'duplicate_starts': len(walk_starts) - len(set(walk_starts)), 'overlapping_ranges': overlaps,
        'cie_augmentations': [[a, p, n] for (a, p), n in pers.most_common()],
        'size_total': sum(sizes), 'size_mean': (sum(sizes) / len(sizes)) if sizes else 0,
        'size_min': sizes[0] if sizes else 0, 'size_p10': pct(.10), 'size_p25': pct(.25), 'size_median': pct(.5), 'size_p75': pct(.75),
        'size_p90': pct(.90), 'size_p99': pct(.99), 'size_max': sizes[-1] if sizes else 0, 'size_top10': sizes[-10:][::-1],
        'size_hist': hist,
        'text_size': text_end, 'covered_bytes': cov, 'covered_pct': 100.0 * cov / text_end,
        'gap_count': len(gaps), 'gap_bytes': sum(e - s for s, e in gaps),
        'gaps_ge_64': sum(1 for s, e in gaps if e - s >= 64), 'gaps_ge_4k': sum(1 for s, e in gaps if e - s >= 4096),
        'largest_gaps': sorted(([s, e - s] for s, e in gaps), key=lambda g: -g[1])[:12],
    }
    return out, ranges, gaps, fdes


def main():
    exefs, scratch = sys.argv[1], sys.argv[2]
    os.makedirs(scratch, exist_ok=True)
    mods, imgs, res = {}, {}, {}
    for name in MODULES:
        m = F.load_nso(os.path.join(exefs, name), name)
        mods[name], imgs[name] = m, m.image
    syms_by = {}
    for name in MODULES:
        m, img = mods[name], imgs[name]
        r = {'file_size': m.file_size, 'build_id': m.build_id[:0x10].hex(), 'bss': m.bss,
             'segments': {k: {'mem_off': v[0], 'size': v[1], 'stored': v[2], 'compressed': v[3]} for k, v in m.segs.items()}}
        m0 = F.mod0(img)
        r['mod0'] = m0._asdict()
        r['module_name'] = F.module_name_from_ro(img, m.segs['ro'][0])
        tags = F.dynamic(img, m0.dynamic)
        r['dynamic'] = [[F.DT_NAMES.get(t, hex(t)), v] for t, v in tags]
        td = dict(tags)
        syms, cnt = F.symbols(img, m)
        syms_by[name] = syms
        r['dynsym_count'] = cnt
        imp = [s for s in syms if s.shndx == 0 and s.name]
        exp = [s for s in syms if s.shndx != 0 and s.bind in (1, 2) and s.name]
        loc = [s for s in syms if s.shndx != 0 and s.bind == 0]
        r['imports'] = len(imp); r['imports_weak'] = sum(1 for s in imp if s.bind == 2)
        r['imports_by_type'] = dict(collections.Counter({0: 'notype', 1: 'object', 2: 'func', 6: 'tls'}.get(s.type, str(s.type)) for s in imp))
        r['exports'] = len(exp); r['exports_by_type'] = dict(collections.Counter({0: 'notype', 1: 'object', 2: 'func', 6: 'tls'}.get(s.type, str(s.type)) for s in exp))
        r['exports_weak'] = sum(1 for s in exp if s.bind == 2)
        r['local_defined'] = len(loc)
        # relocations
        ra, jr, relr = F.relocations(img, td)
        segs = m.segs; text_end = segs['text'][1]
        def where(a):
            for k in ('text', 'ro', 'data'):
                mo, sz = segs[k][0], segs[k][1]
                if mo <= a < mo + sz: return k
            if m0.bss_start <= a < m0.bss_end + 0x100000: return 'bss'
            return 'outside'
        rc = collections.Counter(F.R_NAMES.get(x.type, str(x.type)) for x in ra)
        jc = collections.Counter(F.R_NAMES.get(x.type, str(x.type)) for x in jr)
        relr_tgts = [struct.unpack_from('<Q', img, a)[0] for a in relr]   # RELR: the implicit addend is the word at the location
        rel_all = [x.addend for x in ra if x.type == 1027] + relr_tgts
        rel_tgt = collections.Counter(where(a) for a in rel_all)
        rel_text = sorted({a for a in rel_all if a < text_end})
        r['relr_where'] = dict(collections.Counter(where(a) for a in relr))
        r['rela'] = dict(rc); r['jmprel'] = dict(jc); r['relr'] = len(relr)
        r['relative_targets'] = dict(rel_tgt)
        r['relative_text_targets_distinct'] = len(rel_text)
        r['relocs_where'] = dict(collections.Counter(where(x.offset) for x in ra + jr))
        # symbol-bound relocs against imports
        imp_idx = {s.index for s in imp}
        r['rela_against_imports'] = dict(collections.Counter(F.R_NAMES.get(x.type, str(x.type)) for x in ra + jr if x.sym in imp_idx))
        r['plt_entries'] = len(jr)
        # FDEs
        fs, ranges, gaps, fdes = fde_stats(img, m, m0)
        r['fde'] = fs
        starts = {s for s, _ in ranges}
        r['relative_text_targets_at_fde_start'] = sum(1 for a in rel_text if a in starts)
        exp_func = [s for s in exp if s.type == 2 and s.value < text_end]
        r['exported_funcs_in_text'] = len(exp_func)
        r['exported_funcs_at_fde_start'] = sum(1 for s in exp_func if s.value in starts)
        r['api_info_entries'] = api_info(img, m)
        with open(os.path.join(scratch, f'fdes_{name}.json'), 'w') as f:
            json.dump({'text_end': text_end, 'ranges': ranges, 'lsda': [x.start for x in fdes if x.lsda],
                       'plt': [x.offset for x in jr], 'rel_text': rel_text,
                       'exports_text': sorted({s.value for s in exp_func}),
                       'segs': {k: [v[0], v[1]] for k, v in segs.items()}, 'bss': [m0.bss_start, m0.bss_end]}, f)
        res[name] = r
    # cross-module resolution
    exp_maps = {n: {s.name for s in syms_by[n] if s.shndx != 0 and s.bind in (1, 2) and s.name} for n in MODULES}
    cross = {}
    all_dem_needed = set()
    for name in MODULES:
        imp = [s for s in syms_by[name] if s.shndx == 0 and s.name]
        prov = collections.Counter()
        unresolved = []
        per_provider = collections.defaultdict(list)
        for s in imp:
            p = next((n for n in LOAD_ORDER if n != name and s.name in exp_maps[n]), None)
            if p is None and name == 'rtld':
                p = None
            if p is None:
                unresolved.append([s.name, 'weak' if s.bind == 2 else 'global'])
            else:
                prov[p] += 1; per_provider[p].append(s)
        cross[name] = {'by_provider': dict(prov), 'unresolved': unresolved, 'unresolved_count': len(unresolved)}
        all_dem_needed |= {s.name for v in per_provider.values() for s in v}
        cross[name]['_pp'] = per_provider
    # duplicate definitions across modules
    dup = collections.Counter()
    for a in LOAD_ORDER:
        for b in LOAD_ORDER:
            if a < b: dup[a + '&' + b] = len(exp_maps[a] & exp_maps[b])
    dem = demangle(sorted(all_dem_needed | exp_maps['subsdk0']))
    for name in MODULES:
        pp = cross[name].pop('_pp')
        cross[name]['areas'] = {}
        for p, lst in pp.items():
            ac = collections.Counter(); sub = collections.Counter(); ex = collections.defaultdict(list); kinds = collections.Counter()
            for s in lst:
                a = area(s.name, dem.get(s.name, s.name)); ac[a] += 1
                kinds[(a, {0: 'notype', 1: 'object', 2: 'func', 6: 'tls'}.get(s.type, str(s.type)))] += 1
                sa = subarea(s.name, dem.get(s.name, s.name))
                if sa: sub[sa] += 1
                if len(ex[a]) < 6: ex[a].append(dem.get(s.name, s.name)[:110])
            cross[name]['areas'][p] = {'areas': ac.most_common(), 'subareas': sub.most_common(60), 'examples': dict(ex),
                                       'objects': [[a, n] for (a, t), n in kinds.items() if t == 'object']}
    res['_cross'] = cross
    res['_dup_exports'] = dict(dup)
    res['_subsdk0_exports_areas'] = collections.Counter(area(n, dem.get(n, n)) for n in exp_maps['subsdk0']).most_common()
    res['_npdm'] = npdm_svcs(os.path.join(exefs, 'main.npdm'))
    with open(os.path.join(scratch, 'layout.json'), 'w') as f:
        json.dump(res, f, indent=1, default=str)
    # brief console summary
    for name in MODULES:
        r = res[name]
        print(f"== {name}: text {r['segments']['text']['size']:#x} ro {r['segments']['ro']['size']:#x} data {r['segments']['data']['size']:#x} bss {r['bss']:#x}")
        print(f"   dynsym {r['dynsym_count']} imports {r['imports']} exports {r['exports']} rela {r['rela']} jmprel {r['jmprel']} relr {r['relr']}")
        fd = r['fde']
        print(f"   FDEs hdr {fd['hdr_fde_count']} walk {fd['walk_fde_count']} CIEs {fd['cie_count']} lsda {fd['fdes_with_lsda']} cov {fd['covered_pct']:.2f}% gaps {fd['gap_count']} ({fd['gap_bytes']} B)")
        print(f"   imports by provider {cross[name]['by_provider']} unresolved {cross[name]['unresolved_count']}")


if __name__ == '__main__':
    main()
