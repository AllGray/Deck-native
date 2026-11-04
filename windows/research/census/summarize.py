"""Census part 6: classify the counters and write markdown tables.
Usage: py -3 -I summarize.py <ExeFS dir> <scratch dir> [--classes]
Reads <scratch>/layout.json, insns_*.json, diag_*.json, xcheck_*.json, crosscheck.json; writes <scratch>/tables.md.
--classes prints every (class, mnemonic) pair seen, to audit the classification."""
import bisect, collections, json, os, re, subprocess, sys

sys.dont_write_bytecode = True   # keep this folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nsofmt as F

MODS = ('main', 'sdk', 'subsdk0', 'rtld')
CXXFILT = r'C:\Program Files\LLVM\bin\llvm-cxxfilt.exe'

BRANCH = {'b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz', 'braa', 'brab', 'braaz', 'brabz', 'blraa', 'blrab', 'blraaz', 'blrabz',
          'retaa', 'retab', 'eret', 'eretaa', 'eretab'}
EXC = {'svc', 'hvc', 'smc', 'brk', 'hlt', 'dcps1', 'dcps2', 'dcps3'}
BARRIER = {'dmb', 'dsb', 'isb', 'sb', 'ssbb', 'pssbb'}
HINT = {'nop', 'yield', 'wfe', 'wfi', 'sev', 'sevl', 'hint', 'bti', 'paciasp', 'pacibsp', 'autiasp', 'autibsp', 'paciaz', 'pacibz', 'autiaz', 'autibz',
        'pacia1716', 'pacib1716', 'autia1716', 'autib1716', 'xpaclri', 'esb', 'psb', 'tsb', 'csdb', 'dgh', 'clrbhb', 'chkfeat', 'gcsb', 'wfet', 'wfit'}
PAC_DP = {'pacia', 'pacib', 'pacda', 'pacdb', 'autia', 'autib', 'autda', 'autdb', 'paciza', 'pacizb', 'pacdza', 'pacdzb', 'autiza', 'autizb', 'autdza',
          'autdzb', 'xpaci', 'xpacd', 'pacga'}
CRYPTO = re.compile(r'^(aes(e|d|mc|imc)|sha(1[cpmh]|1su[01]|256h2?|256su[01]|512h2?|512su[01])|eor3|bcax|rax1|xar|sm3.*|sm4.*)$')
DOT = re.compile(r'^(sdot|udot|usdot|sudot|smmla|ummla|usmmla|bfdot|bfmmla|bfmlal[bt]|bfcvtn2?|bfcvt)$')
HALF_CONV = {'fcvt', 'fcvtn', 'fcvtn2', 'fcvtl', 'fcvtl2', 'fcvtxn', 'fcvtxn2'}
EXCL = re.compile(r'^(ld(a)?xr[bh]?|st(l)?xr[bh]?|ld(a)?xp|st(l)?xp)$')
ACQREL = re.compile(r'^(ldar[bh]?|stlr[bh]?|ldapr[bh]?|ldapur(s?[bhw])?|stlur[bh]?|ldlar[bh]?|stllr[bh]?)$')
LSE = re.compile(r'^(casp?(a|l|al)?[bh]?|swp(a|l|al)?[bh]?|ld(add|clr|eor|set|smax|smin|umax|umin)(a|l|al)?[bh]?|st(add|clr|eor|set|smax|smin|umax|umin)l?[bh]?)$')
MTE = re.compile(r'^(irg|gmi|subp|subps|addg|subg|stg|stzg|st2g|stz2g|ldg|stgp|ldgm|stgm|stzgm)$')
STRUCT = re.compile(r'^(ld[1-4]r?|st[1-4])$')


def is_fp16_shape(sig):
    t = sig.split(',') if sig else []
    return 'h' in t or any(x in ('v.4h', 'v.8h', 'v.2h', 'v.h') for x in t)


def cls(mn, g, sig):
    """(class, subclass)"""
    if mn == 'udf': return 'UDF (padding / trap)', mn
    if mn in BRANCH or mn.startswith('b.') or mn.startswith('bc.'): return 'Branch', mn
    if mn in EXC: return 'Exception (SVC/BRK)', mn
    if mn in BARRIER: return 'Barrier', mn
    if mn in HINT: return 'Hint (NOP/PAC/BTI)', mn
    if mn in ('mrs', 'msr'): return 'System register (MRS/MSR)', mn
    if mn in ('dc', 'ic', 'tlbi', 'at', 'sys', 'sysl', 'cfinv', 'xaflag', 'axflag', 'rmif', 'setf8', 'setf16'): return 'Cache / sys / flag ops', mn
    if mn == 'clrex' or EXCL.match(mn): return 'Exclusive (LDXR/STXR)', mn
    if g in ('sve', 'sme/rsvd'): return 'SVE / SME', mn
    if g in ('ls', 'lsv'):
        if ACQREL.match(mn): return 'Load-acquire / store-release', mn
        if LSE.match(mn): return 'LSE atomic', mn
        if mn in ('ldraa', 'ldrab'): return 'PAC load (LDRAA/LDRAB)', mn
        if MTE.match(mn): return 'MTE', mn
        if mn in ('prfm', 'prfum'): return 'Load/store', 'prefetch'
        if g == 'lsv':
            if STRUCT.match(mn): return 'Load/store', 'NEON structure ld/st'
            pair = mn in ('ldp', 'stp', 'ldnp', 'stnp')
            return 'Load/store', ('SIMD&FP pair ' if pair else 'SIMD&FP single ') + ('load' if mn.startswith('ld') else 'store')
        pair = mn in ('ldp', 'stp', 'ldnp', 'stnp', 'ldpsw')
        return 'Load/store', ('GPR pair ' if pair else 'GPR single ') + ('load' if mn.startswith('ld') else 'store')
    if g in ('imm', 'reg'):
        if mn.startswith('crc32'): return 'CRC32', mn
        if mn in PAC_DP: return 'PAC (data processing)', mn
        return 'Integer ALU', mn
    if g.startswith('fp'):
        if CRYPTO.match(mn) or (mn in ('pmull', 'pmull2') and '1q' in sig): return 'Crypto (AES/SHA/PMULL)', mn
        if DOT.match(mn): return 'Dot product / matrix', mn
        if (mn.startswith('f') or mn in ('scvtf', 'ucvtf')) and mn not in HALF_CONV and is_fp16_shape(sig):
            return 'FP16 arithmetic (v8.2)', mn
        if g.startswith('fp:scalar'): return 'Scalar FP', mn
        return 'AdvSIMD (NEON)', mn
    return 'Other', mn


def pct(a, b):
    return '%.2f%%' % (100.0 * a / b) if b else '-'


def fmt(n):
    return f'{n:,}'


def table(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '|'.join('---:' if i else '---' for i in range(len(head))) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(x) for x in r) + ' |')
    return '\n'.join(out)


def sdk_namer(exefs):
    m = F.load_nso(os.path.join(exefs, 'sdk'), 'sdk')
    syms, _ = F.symbols(m.image, m)
    fn = sorted((s.value, s.size, s.name) for s in syms if s.shndx and s.type == 2 and s.size)
    starts = [x[0] for x in fn]
    def name_of(a):
        i = bisect.bisect_right(starts, a) - 1
        if i >= 0 and fn[i][0] <= a < fn[i][0] + fn[i][1]: return fn[i][2]
        return None
    return name_of


SVC_NAMES = {0x01: 'SetHeapSize', 0x02: 'SetMemoryPermission', 0x03: 'SetMemoryAttribute', 0x04: 'MapMemory', 0x05: 'UnmapMemory', 0x06: 'QueryMemory',
             0x07: 'ExitProcess', 0x08: 'CreateThread', 0x09: 'StartThread', 0x0A: 'ExitThread', 0x0B: 'SleepThread', 0x0C: 'GetThreadPriority',
             0x0D: 'SetThreadPriority', 0x0E: 'GetThreadCoreMask', 0x0F: 'SetThreadCoreMask', 0x10: 'GetCurrentProcessorNumber', 0x11: 'SignalEvent',
             0x12: 'ClearEvent', 0x13: 'MapSharedMemory', 0x14: 'UnmapSharedMemory', 0x15: 'CreateTransferMemory', 0x16: 'CloseHandle', 0x17: 'ResetSignal',
             0x18: 'WaitSynchronization', 0x19: 'CancelSynchronization', 0x1A: 'ArbitrateLock', 0x1B: 'ArbitrateUnlock', 0x1C: 'WaitProcessWideKeyAtomic',
             0x1D: 'SignalProcessWideKey', 0x1E: 'GetSystemTick', 0x1F: 'ConnectToNamedPort', 0x20: 'SendSyncRequestLight', 0x21: 'SendSyncRequest',
             0x22: 'SendSyncRequestWithUserBuffer', 0x23: 'SendAsyncRequestWithUserBuffer', 0x24: 'GetProcessId', 0x25: 'GetThreadId', 0x26: 'Break',
             0x27: 'OutputDebugString', 0x28: 'ReturnFromException', 0x29: 'GetInfo', 0x2C: 'MapPhysicalMemory', 0x2D: 'UnmapPhysicalMemory',
             0x32: 'SetThreadActivity', 0x33: 'GetThreadContext3', 0x34: 'WaitForAddress', 0x35: 'SignalToAddress', 0x36: 'SynchronizePreemptionState',
             0x40: 'CreateSession', 0x41: 'AcceptSession', 0x43: 'ReplyAndReceive', 0x44: 'ReplyAndReceiveWithUserBuffer', 0x45: 'CreateEvent',
             0x4B: 'CreateCodeMemory', 0x50: 'CreateSharedMemory', 0x51: 'MapTransferMemory', 0x52: 'UnmapTransferMemory', 0x70: 'CreatePort', 0x72: 'ConnectToPort'}


def svc_callers(exefs, scratch, svc_sites):
    """{svc site: (stub start, number of direct B/BL callers, [(caller export, count)])}: the SVC stubs are unexported, so each is named by the exports that call it"""
    from array import array
    m = F.load_nso(os.path.join(exefs, 'sdk'), 'sdk')
    syms, _ = F.symbols(m.image, m)
    fn = sorted((s.value, s.size, s.name) for s in syms if s.shndx and s.type == 2 and s.size)
    fst = [x[0] for x in fn]
    info = json.load(open(os.path.join(scratch, 'fdes_sdk.json')))
    R = [tuple(r) for r in info['ranges']]; rs = [r[0] for r in R]
    w = array('I'); w.frombytes(bytes(m.image[:info['text_end'] & ~3]))
    callers = collections.defaultdict(list)
    for i, x in enumerate(w):
        if x & 0x7C000000 == 0x14000000:
            off = x & 0x3FFFFFF; off = off - (1 << 26) if off & (1 << 25) else off
            callers[i * 4 + off * 4].append(i * 4)
    out = {}
    for a in svc_sites:
        j = bisect.bisect_right(rs, a) - 1
        st = R[j][0] if j >= 0 and R[j][0] <= a < R[j][1] else a
        c = collections.Counter()
        for x in callers.get(st, []):
            k = bisect.bisect_right(fst, x) - 1
            c[fn[k][2] if k >= 0 and fn[k][0] <= x < fn[k][0] + fn[k][1] else None] += 1
        out[a] = (st, len(callers.get(st, [])), c.most_common(3))
    return out


def short(d):
    """A demangled name without its parameter list (keeping '(anonymous namespace)')"""
    if not d: return '(unexported)'
    t = d.replace('(anonymous namespace)', '{anon}').split('(')[0].replace('{anon}', '(anonymous namespace)')
    return t[:80]


def demangle(names):
    names = [n for n in names if n]
    if not names: return {}
    p = subprocess.run([CXXFILT], input='\n'.join(names) + '\n', capture_output=True, text=True, encoding='latin1')
    return dict(zip(names, p.stdout.split('\n')))


def main():
    exefs, scratch = sys.argv[1:3]
    L = json.load(open(os.path.join(scratch, 'layout.json')))
    I = {m: json.load(open(os.path.join(scratch, f'insns_{m}.json'))) for m in MODS}
    D = {m: json.load(open(os.path.join(scratch, f'diag_{m}.json'))) for m in MODS}
    X = {m: json.load(open(os.path.join(scratch, f'xcheck_{m}.json'))) for m in MODS}
    C = json.load(open(os.path.join(scratch, 'crosscheck.json')))
    out = []
    W = out.append

    # ---------- classification ----------
    K = {}
    for m in MODS:
        k = collections.Counter(); sub = collections.Counter(); mnc = collections.Counter(); neon = collections.Counter(); neon_shape = collections.defaultdict(collections.Counter)
        scal = collections.Counter(); special = collections.defaultdict(collections.Counter)
        for mn, g, sig, n in I[m]['keys']:
            c, s = cls(mn, g, sig)
            k[c] += n; sub[(c, s)] += n; mnc[mn] += n
            if c == 'AdvSIMD (NEON)':
                neon[mn] += n; neon_shape[mn][sig] += n
            if c == 'Scalar FP': scal[mn] += n
            if c in ('FP16 arithmetic (v8.2)', 'Dot product / matrix', 'Crypto (AES/SHA/PMULL)', 'CRC32', 'Exclusive (LDXR/STXR)', 'LSE atomic',
                     'Load-acquire / store-release', 'Hint (NOP/PAC/BTI)', 'PAC (data processing)', 'PAC load (LDRAA/LDRAB)', 'SVE / SME', 'MTE', 'Other',
                     'Cache / sys / flag ops', 'Barrier'):
                special[c][mn] += n
        K[m] = dict(k=k, sub=sub, mnc=mnc, neon=neon, neon_shape=neon_shape, scal=scal, special=special, total=sum(k.values()))
    if '--classes' in sys.argv:
        allp = collections.Counter()
        for m in MODS:
            for (c, s), n in K[m]['sub'].items(): allp[(c, s)] += n
        for (c, s), n in sorted(allp.items()): print(f'{c:34s} {s:24s} {n}')
        return

    # ---------- layout ----------
    W('### Layout\n')
    rows = []
    for m in MODS:
        r = L[m]; s = r['segments']
        rows.append([m, r['build_id'][:16], fmt(r['file_size']), '%s (%.2f MiB)' % (hex(s['text']['size']), s['text']['size'] / 2**20),
                     '%s (%.2f MiB)' % (hex(s['ro']['size']), s['ro']['size'] / 2**20), '%s (%.2f MiB)' % (hex(s['data']['size']), s['data']['size'] / 2**20),
                     '%s (%.2f MiB)' % (hex(r['bss']), r['bss'] / 2**20), 'LZ4 x3' if all(v['compressed'] for v in s.values()) else 'mixed'])
    W(table(['module', 'build id (16 B)', 'file bytes', 'text', 'rodata', 'data', 'bss', 'compression'], rows))
    W('')
    rows = []
    for m in MODS:
        r = L[m]; md = r['mod0']; dyn = dict((a, b) for a, b in r['dynamic'][::-1])
        names = [a for a, b in r['dynamic']]
        rows.append([m, hex(md['off']), hex(md['dynamic']), '%s..%s' % (hex(md['bss_start']), hex(md['bss_end'])), '%s..%s' % (hex(md['ehh_start']), hex(md['ehh_end'])),
                     hex(md['module_object']), len(r['dynamic']), ', '.join(sorted(set(n for n in names if n in ('NEEDED', 'SONAME', 'RELR', 'GNU_HASH', 'HASH', 'BIND_NOW', 'TEXTREL', 'FLAGS_1', 'INIT', 'FINI', 'INIT_ARRAY', 'DEBUG')))) or '-'])
    W(table(['module', 'MOD0 at', '.dynamic', 'bss (MOD0)', '.eh_frame_hdr', 'module object', 'dyn tags', 'notable tags'], rows))
    W('')
    rows = []
    for m in MODS:
        r = L[m]
        rows.append([m, fmt(r['dynsym_count']['from_header']), fmt(r['imports']), ', '.join(f'{k} {v}' for k, v in sorted(r['imports_by_type'].items())),
                     fmt(r['exports']), ', '.join(f'{k} {v}' for k, v in sorted(r['exports_by_type'].items())), fmt(r['exports_weak']), fmt(r['plt_entries'])])
    W(table(['module', 'dynsym entries', 'imports (undefined)', 'import types', 'exports (global+weak)', 'export types', 'weak exports', 'PLT entries (JUMP_SLOT)'], rows))
    W('')
    rows = []
    for m in MODS:
        r = L[m]
        rel = dict(r['rela']); jr = r['jmprel']
        rows.append([m, ', '.join(f'{k} {fmt(v)}' for k, v in sorted(rel.items())) or '-', ', '.join(f'{k} {fmt(v)}' for k, v in jr.items()) or '-', fmt(r['relr']),
                     ', '.join(f'{k} {fmt(v)}' for k, v in sorted(r['relative_targets'].items(), key=lambda kv: -kv[1])),
                     fmt(r['relative_text_targets_distinct']), fmt(r['relative_text_targets_at_fde_start']),
                     ', '.join(f'{k} {fmt(v)}' for k, v in r['rela_against_imports'].items()) or '-'])
    W(table(['module', 'RELA', 'JMPREL', 'RELR (packed relative)', 'relative targets by segment', 'distinct text targets', '... at an FDE start', 'symbol relocs against imports'], rows))
    W('')
    # cross-module imports
    W('### Imports between modules (resolved over the load order main, subsdk0, sdk)\n')
    rows = []
    for m in MODS:
        cr = L['_cross'][m]
        rows.append([m, fmt(L[m]['imports'])] + [fmt(cr['by_provider'].get(p, 0)) for p in ('main', 'subsdk0', 'sdk')] +
                    [', '.join('%s (%s)' % (n, b) for n, b in cr['unresolved']) or '-'])
    W(table(['importer', 'imports', 'from main', 'from subsdk0', 'from sdk', 'unresolved'], rows))
    W('')
    W('Symbols defined by more than one module: ' + ', '.join(f'{k} {v}' for k, v in L['_dup_exports'].items()) + '.\n')
    a = L['_cross']['main']['areas']['sdk']
    W('#### What main imports from sdk, by namespace (demangled with llvm-cxxfilt)\n')
    rows = [[k, v, '; '.join(x[:70] for x in a['examples'].get(k, [])[:2])] for k, v in a['areas']]
    W(table(['namespace / group', 'imports', 'examples'], rows))
    W('')
    W('Largest second-level groups: ' + ', '.join(f'{k} {v}' for k, v in a['subareas'][:16]) + '. Data objects imported: ' +
      ', '.join(f'{k} {v}' for k, v in a['objects']) + '.\n')
    for imp, prov in (('main', 'subsdk0'), ('subsdk0', 'sdk'), ('subsdk0', 'main'), ('sdk', 'main'), ('rtld', 'sdk')):
        aa = L['_cross'][imp]['areas'].get(prov)
        if aa:
            W(f'- {imp} from {prov}: ' + ', '.join(f'{k} {v}' for k, v in aa['areas']) + '.')
    W('')
    W('API info (middleware list in rodata): ' + '; '.join(f'{m}: ' + ', '.join(x.replace('SDK MW+Nintendo+', '') for x in L[m]['api_info_entries']) for m in ('main', 'subsdk0', 'sdk')) + '.\n')
    npd = L['_npdm']['ACI0']
    W('main.npdm (ACI0 = ACID) allows SVCs: ' + ', '.join(hex(x) for x in npd['svcs']) + f'; {len(npd["services"])} service names in its access control.\n')

    # ---------- functions ----------
    W('### Functions from .eh_frame\n')
    rows = []
    for m in MODS:
        f = L[m]['fde']; dg = D[m]['gap_summary']
        rows.append([m, 'yes / yes', fmt(f['hdr_fde_count']), 'yes' if f['hdr_matches_walk'] and f['hdr_fde_count'] == f['walk_fde_count'] else 'NO', f['cie_count'],
                     ', '.join(f'{a} {fmt(n)}' for a, p, n in f['cie_augmentations']), fmt(f['fdes_with_lsda']),
                     '%.2f%%' % f['covered_pct'], fmt(f['gap_bytes']), fmt(dg.get('padding only bytes', 0)), fmt(dg.get('PLT bytes', 0)), fmt(dg.get('code bytes', 0))])
    W(table(['module', '.eh_frame_hdr / .eh_frame', 'FDEs', 'hdr table = walk', 'CIEs', 'FDEs per CIE augmentation', 'FDEs with LSDA', 'text covered by FDEs', 'uncovered bytes', '... padding (UDF #0)', '... PLT', '... real code'], rows))
    W('')
    rows = []
    for m in MODS:
        f = L[m]['fde']
        rows.append([m, fmt(f['walk_fde_count']), '%.0f' % f['size_mean'], f['size_p10'], f['size_p25'], f['size_median'], f['size_p75'], f['size_p90'], f['size_p99'], fmt(f['size_max'])])
    W(table(['module', 'functions', 'mean bytes', 'p10', 'p25', 'median', 'p75', 'p90', 'p99', 'max'], rows))
    W('')
    hist_rows = []
    for i, (lo, hi, _) in enumerate(L['main']['fde']['size_hist']):
        label = f'{lo}-{hi}' if hi < 1 << 30 else f'>{lo - 1}'
        hist_rows.append([label + ' B'] + ['%s (%s)' % (fmt(L[m]['fde']['size_hist'][i][2]), pct(L[m]['fde']['size_hist'][i][2], L[m]['fde']['walk_fde_count'])) for m in MODS])
    W(table(['function size'] + list(MODS), hist_rows))
    W('')
    rows = []
    for m in MODS:
        r = I[m]; dg = D[m]
        rows.append([m, fmt(r['bl_targets'].get('FDE start', 0)), fmt(r['bl_targets'].get('PLT stub (import)', 0)), fmt(r['bl_targets'].get('gap (code without FDE)', 0)),
                     fmt(r['bl_targets'].get('inside an FDE (not its start)', 0)), fmt(r['bl_distinct_targets']), fmt(r['fde_starts_never_bl_or_tail']),
                     fmt(r['indirect_target_candidates']), fmt(r['fde_starts_never_bl_tail_or_reloc']), fmt(dg['rel_text_into_gap_code'])])
    W(table(['module', 'BL -> FDE start', 'BL -> PLT', 'BL -> code without FDE', 'BL -> middle of an FDE', 'distinct BL targets', 'FDE starts never BL/B-targeted', 'address-taken FDE starts (relocs, adr/adrp+add, exports)', 'FDE starts with no reference found', 'reloc targets into FDE-less code'], rows))
    W('')
    for m in MODS:
        for g in D[m]['gaps']:
            if not g['plt']:
                W(f'- {m}: FDE-less code at {g["start"]}, {fmt(g["size"])} bytes: {fmt(g["real_insns"])} instructions, {g["rets"]} returns, {g["pac_prologues"]} PAC prologues, {g["bl_targets_in_gap"]} distinct BL targets inside.')
    W('')

    # ---------- instruction classes ----------
    W('### Instruction classes\n')
    classes = ['Integer ALU', 'Load/store', 'Branch', 'Scalar FP', 'AdvSIMD (NEON)', 'FP16 arithmetic (v8.2)', 'Dot product / matrix', 'Crypto (AES/SHA/PMULL)', 'CRC32',
               'Exclusive (LDXR/STXR)', 'LSE atomic', 'Load-acquire / store-release', 'Barrier', 'System register (MRS/MSR)', 'Cache / sys / flag ops', 'Exception (SVC/BRK)',
               'Hint (NOP/PAC/BTI)', 'PAC (data processing)', 'PAC load (LDRAA/LDRAB)', 'MTE', 'SVE / SME', 'UDF (padding / trap)', 'Other']
    rows = []
    for c in classes:
        rows.append([c] + ['%s (%s)' % (fmt(K[m]['k'].get(c, 0)), pct(K[m]['k'].get(c, 0), K[m]['total'])) for m in MODS])
    rows.append(['**decoded total**'] + ['**%s**' % fmt(K[m]['total']) for m in MODS])
    rows.append(['undecodable words'] + [fmt(I[m]['undecodable_total']) for m in MODS])
    rows.append(['text words'] + [fmt(I[m]['text_end'] // 4) for m in MODS])
    W(table(['class'] + list(MODS), rows))
    W('')
    # branches
    def bsub(m, pred):
        return sum(n for mn, n in K[m]['mnc'].items() if pred(mn))
    brow = [('B', lambda x: x == 'b'), ('B.cond', lambda x: x.startswith('b.')), ('CBZ/CBNZ', lambda x: x in ('cbz', 'cbnz')), ('TBZ/TBNZ', lambda x: x in ('tbz', 'tbnz')),
            ('BL', lambda x: x == 'bl'), ('BR', lambda x: x == 'br'), ('BLR', lambda x: x == 'blr'), ('RET', lambda x: x == 'ret'), ('RETAB', lambda x: x == 'retab'),
            ('RETAA', lambda x: x == 'retaa'), ('BRAA/BRAB/BRAAZ/BRABZ', lambda x: x in ('braa', 'brab', 'braaz', 'brabz')), ('BLRAA/BLRAB/BLRAAZ/BLRABZ', lambda x: x in ('blraa', 'blrab', 'blraaz', 'blrabz')),
            ('ERET*', lambda x: x.startswith('eret'))]
    W(table(['branch'] + list(MODS), [[k] + [fmt(bsub(m, p)) for m in MODS] for k, p in brow]))
    W('')
    # loads / stores
    lsk = ['GPR single load', 'GPR single store', 'GPR pair load', 'GPR pair store', 'SIMD&FP single load', 'SIMD&FP single store', 'SIMD&FP pair load', 'SIMD&FP pair store', 'NEON structure ld/st', 'prefetch']
    rows = [[k] + [fmt(K[m]['sub'].get(('Load/store', k), 0)) for m in MODS] for k in lsk]
    for mode in ('base + immediate', 'register offset', 'pre-index writeback', 'post-index writeback'):
        rows.append(['addressing: ' + mode] + [fmt(I[m]['ls_mode'].get(mode, 0)) for m in MODS])
    rows.append(['PC-relative literal loads'] + [fmt(sum(I[m]['literal_loads'].values())) for m in MODS])
    W(table(['load/store'] + list(MODS), rows))
    W('')
    # NEON top 40
    allneon = collections.Counter()
    for m in MODS: allneon.update(K[m]['neon'])
    rows = []
    for mn, n in allneon.most_common(40):
        shapes = collections.Counter()
        for m in MODS: shapes.update(K[m]['neon_shape'].get(mn, {}))
        top = ', '.join(f'{s or "-"} {fmt(c)}' for s, c in shapes.most_common(3))
        rows.append([mn, fmt(n)] + [fmt(K[m]['neon'].get(mn, 0)) for m in ('main', 'sdk', 'subsdk0')] + [top])
    W('#### Top 40 AdvSIMD mnemonics (all modules)\n')
    W(table(['mnemonic', 'total', 'main', 'sdk', 'subsdk0', 'commonest register shapes'], rows))
    W('')
    W(f'Distinct AdvSIMD mnemonics: main {len(K["main"]["neon"])}, sdk {len(K["sdk"]["neon"])}, subsdk0 {len(K["subsdk0"]["neon"])}.\n')
    sc = collections.Counter()
    for m in MODS: sc.update(K[m]['scal'])
    W('Scalar FP, top 30 (all modules): ' + ', '.join(f'{k} {fmt(v)}' for k, v in sc.most_common(30)) + '.\n')
    for c in ('FP16 arithmetic (v8.2)', 'Dot product / matrix', 'Crypto (AES/SHA/PMULL)', 'CRC32', 'Exclusive (LDXR/STXR)', 'LSE atomic', 'Load-acquire / store-release',
              'Barrier', 'Hint (NOP/PAC/BTI)', 'PAC (data processing)', 'PAC load (LDRAA/LDRAB)', 'Cache / sys / flag ops', 'SVE / SME', 'MTE', 'Other'):
        parts = []
        for m in MODS:
            sp = K[m]['special'].get(c)
            if sp: parts.append(f'{m}: ' + ', '.join(f'{k} {fmt(v)}' for k, v in sp.most_common()))
        W(f'- **{c}**: ' + ('; '.join(parts) if parts else 'none') + '.')
    W('')
    W('Barriers by option: ' + '; '.join(f'{m}: ' + ', '.join(f'{a} {b}'.strip() + f' {n}' for a, b, n in I[m]['barrier']) for m in MODS if I[m]['barrier']) + '.\n')
    W('UDF by immediate and placement: ' + '; '.join(f'{m}: ' + ', '.join(f'{b} {a} {fmt(n)}' for a, b, n in D[m]['udf']) for m in MODS if D[m]['udf']) + '.\n')

    # ---------- system registers / SVC ----------
    name_of = sdk_namer(exefs)
    W('### System registers and SVCs\n')
    rows = []
    for m in MODS:
        for a, b, n in I[m]['sysreg']:
            rows.append([m, a, b, n])
    W(table(['module', 'op', 'register', 'sites'], rows))
    W('')
    svc_sites = I['sdk']['sites'].get('svc', [])
    raw_names = {}
    for a in [x for k in ('msr fpcr', 'mrs fpcr', 'msr fpsr', 'mrs fpsr', 'msr nzcv', 'msr tpidr_el0', 'mrs tpidr_el0', 'mrs ctr_el0', 'dc civac', 'ic ivau') for x in I['sdk']['sites'].get(k, [])]:
        raw_names[a] = name_of(a)
    sc = svc_callers(exefs, scratch, svc_sites)
    dem = demangle(sorted({v for v in raw_names.values() if v} | {n for v in sc.values() for n, _ in v[2] if n}))
    import struct
    sdkimg = F.load_nso(os.path.join(exefs, 'sdk'), 'sdk').image
    rows = []
    allowed = set(npd['svcs'])
    for a in sorted(svc_sites, key=lambda a: (struct.unpack_from('<I', sdkimg, a)[0] >> 5) & 0xFFFF):
        num = (struct.unpack_from('<I', sdkimg, a)[0] >> 5) & 0xFFFF
        st, ncall, top = sc[a]
        who = '; '.join('%s x%d' % (short(dem.get(n, n)), c) for n, c in top[:2]) or '-'
        rows.append([hex(num), SVC_NAMES.get(num, '(not in the Switch 1 table)'), hex(a), 'yes' if num in allowed else '**no**', ncall, who])
    W(table(['SVC', 'Horizon name', 'sdk site', 'in npdm', 'direct callers of its stub', 'calling sdk exports (top 2)'], rows))
    W('')
    rt = I['rtld']
    W('rtld SVCs: ' + ', '.join(f'{hex(n)} x{c}' for n, c in rt['svc']) + '.\n')
    rows = []
    for k in ('mrs fpcr', 'msr fpcr', 'mrs fpsr', 'msr fpsr', 'msr nzcv', 'msr tpidr_el0', 'mrs tpidr_el0', 'mrs ctr_el0', 'dc civac', 'ic ivau'):
        for a in I['sdk']['sites'].get(k, []):
            nm = raw_names.get(a)
            rows.append([k, hex(a), (dem.get(nm, nm) or '(unexported helper)')[:90]])
    W(table(['sdk site', 'offset', 'containing export'], rows))
    W('')

    # ---------- indirect control flow ----------
    W('### Indirect control flow\n')
    keys = []
    for m in MODS:
        for k, _ in I[m]['br']:
            if k not in keys: keys.append(k)
    W(table(['BR class'] + list(MODS), [[k] + [fmt(dict(I[m]['br']).get(k, 0)) for m in MODS] for k in keys] + [['**total BR**'] + [fmt(sum(n for _, n in I[m]['br'])) for m in MODS]]))
    W('')
    keys = []
    for m in MODS:
        for k, _ in I[m]['blr']:
            if k not in keys: keys.append(k)
    W(table(['BLR class'] + list(MODS), [[k] + [fmt(dict(I[m]['blr']).get(k, 0)) for m in MODS] for k in keys] + [['**total BLR**'] + [fmt(sum(n for _, n in I[m]['blr'])) for m in MODS]]))
    W('')
    W('Commonest vtable slot offsets (main): ' + ', '.join(f'+{a:#x} {fmt(n)}' for a, n in I['main']['vt_slots_top'][:12]) + '.\n')
    rows = []
    for m in MODS:
        r = I[m]; jc = r['jt_check']
        rows.append([m, fmt(sum(n for k, n in r['br'] if k.startswith('jump table'))), ', '.join(f'{k} {fmt(n)}' for k, n in r['jt_kinds']) or '-',
                     fmt(jc.get('tables decoded', 0)), fmt(jc.get('entries', 0)), fmt(jc.get('entries landing inside the function', 0)), fmt(jc.get('tables with entries outside the function', 0)),
                     fmt(jc.get('tables without decoded bound/base', 0))])
    W(table(['module', 'jump-table BRs', 'shapes (entry load, anchor, table segment)', 'tables decoded', 'entries', 'entries inside own function', 'tables with outliers', 'bound or base not found'], rows))
    W('')
    rows = []
    for m in MODS:
        t = I[m]['tail']
        rows.append([m, fmt(t.get('b to another FDE start (tail call)', 0)), fmt(I[m]['tail_distinct_fde_targets']), fmt(t.get('b to PLT stub (tail call to import)', 0)),
                     fmt(sum(v for k, v in t.items() if k.startswith('b out of function'))), fmt(sum(v for k, v in t.items() if k.startswith('conditional branch leaving'))),
                     fmt(I[m]['misc'].get('b_intra', 0))])
    W(table(['module', 'B to another FDE start (tail call)', 'distinct tail-call targets', 'B to PLT (tail call to import)', 'B leaving function elsewhere', 'conditional branch leaving function', 'B within function'], rows))
    W('')
    W('Code addresses formed in code: ' + '; '.join(f'{m}: ' + ', '.join(f'{k} {fmt(v)}' for k, v in sorted(I[m]['adr'].items()) if 'text' in k) for m in MODS) + '.\n')

    # ---------- TLS ----------
    W('### Thread pointer use\n')
    rows = []
    for m in MODS:
        for a, b, n in I[m]['tls']:
            rows.append([m, a, b, n])
    W(table(['module', 'register read', 'first use of the result', 'sites'], rows))
    W('')

    # ---------- semantic hazards ----------
    W('### Instructions whose semantics differ from x86-64\n')
    def cnt(m, pred): return sum(n for mn, n in K[m]['mnc'].items() if pred(mn))
    hz = [('SDIV/UDIV (ARM: x/0 = 0, no trap)', lambda x: x in ('sdiv', 'udiv')),
          ('float->int FCVTZS/FCVTZU/FCVTAS/FCVTMS/FCVTNS/FCVTPS... (saturating)', lambda x: re.match(r'fcvt[zamnp][su]$', x) is not None),
          ('FMADD/FMSUB/FNMADD/FNMSUB (fused)', lambda x: x in ('fmadd', 'fmsub', 'fnmadd', 'fnmsub')),
          ('FMLA/FMLS (vector fused)', lambda x: x in ('fmla', 'fmls')),
          ('FMIN/FMAX/FMINNM/FMAXNM (+P/V forms)', lambda x: re.match(r'f(min|max)(nm)?(p|v)?$', x) is not None),
          ('FRINT* (explicit rounding)', lambda x: x.startswith('frint')),
          ('FRECPE/FRSQRTE (estimates)', lambda x: x in ('frecpe', 'frsqrte', 'urecpe', 'ursqrte', 'frecpx')),
          ('FRECPS/FRSQRTS (Newton steps)', lambda x: x in ('frecps', 'frsqrts')),
          ('FSQRT / FDIV', lambda x: x in ('fsqrt', 'fdiv')),
          ('RBIT', lambda x: x == 'rbit'), ('CLZ / CLS', lambda x: x in ('clz', 'cls')), ('UMULH/SMULH', lambda x: x in ('umulh', 'smulh')),
          ('ADC/SBC/ADCS/SBCS/NGC (carry: ARM C = NOT borrow)', lambda x: x in ('adc', 'sbc', 'adcs', 'sbcs', 'ngc', 'ngcs')),
          ('flag setters (CMP/CMN/TST/ADDS/SUBS/ANDS/BICS/NEGS)', lambda x: x in ('cmp', 'cmn', 'tst', 'adds', 'subs', 'ands', 'bics', 'negs')),
          ('CCMP/CCMN/FCCMP (conditional compare)', lambda x: x in ('ccmp', 'ccmn', 'fccmp', 'fccmpe')),
          ('FCMP/FCMPE', lambda x: x in ('fcmp', 'fcmpe')),
          ('flag consumers CSEL/CSINC/CSINV/CSNEG/CSET/CSETM/CINC/CINV/CNEG/FCSEL', lambda x: x in ('csel', 'csinc', 'csinv', 'csneg', 'cset', 'csetm', 'cinc', 'cinv', 'cneg', 'fcsel')),
          ('B.cond', lambda x: x.startswith('b.'))]
    W(table(['instruction group'] + list(MODS), [[k] + [fmt(cnt(m, p)) for m in MODS] for k, p in hz]))
    W('')

    # ---------- checks ----------
    W('### Cross-checks\n')
    rows = []
    for m in MODS:
        x = X[m]
        rows.append([m, fmt(x['words']), fmt(x['capstone_decoded']), fmt(x['llvm_decoded']), fmt(x['capstone_undecodable']), fmt(x['llvm_unknown']),
                     f"{x['same_count_mnemonics']} / {len(set(K[m]['mnc']))}", ', '.join(f'{a} {b}/{c}' for a, b, c in x['differences']) or 'none'])
    W(table(['module', 'text words', 'capstone 5 decoded', 'llvm-objdump 23 decoded', 'capstone undecodable', 'llvm <unknown>', 'mnemonics with identical counts', 'differences (capstone/llvm)'], rows))
    W('')
    rows = []
    for m in ('main', 'sdk', 'subsdk0'):
        for k, v in C[m]['kinds'].items():
            rows.append([m, k, fmt(v['census']), fmt(v['rewriter']), fmt(v['both']), len(v['census_only']), len(v['rewriter_only'])])
    W(table(['module', 'site kind', 'census', 'rewriter (.sites.tsv + .gates.tsv)', 'same address', 'census only', 'rewriter only'], rows))
    W('')
    for m in ('main', 'sdk', 'subsdk0'):
        c = C[m]
        W(f'- {m}: rewriter gates {c["gate_kinds"]}; other sites.tsv rows {c["tsv_other_rows"]}; .changed.tsv {fmt(c["changed_rows"])} rows / {fmt(c["changed_words"])} words; '
          f'SVC numbers identical: {c["svc_numbers_rewriter"] == c["svc_numbers_census"]}; system-register accesses the rewriter leaves as they are: '
          + (', '.join(f'{a} {b} x{n}' for a, b, n in c['sysregs_not_rewritten']) or 'none') + '; cache maintenance: ' + (', '.join(f'{a} {b} x{n}' for a, b, n in c['cache_ops']) or 'none') + '.')
    W('')
    with open(os.path.join(scratch, 'tables.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
