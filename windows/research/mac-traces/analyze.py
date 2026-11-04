"""Summarise one traced Mac session (hle_trace.c output, copied to _local/mac-traces/<tag>/) against the census function map.
Usage: py -3 -I analyze.py <run dir> [ExeFS dir] [census dir]
Reads <run>/trace/*, <run>/phases.tsv, the census fdes_<module>.json and the game's NSOs (symbols, PLT stubs, setjmp call sites).
Writes <run>/summary.md (numbers for research/05-mac-traces.md) and <run>/executed_<module>.txt (executed FDE starts)."""
import bisect, collections, json, os, struct, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'census'))
import nsofmt  # noqa: E402

RUN = sys.argv[1]
EXEFS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, '..', 'installer-test-win', 'build201', 'extract', 'ExeFS')
CENSUS = sys.argv[3] if len(sys.argv) > 3 else os.path.join(ROOT, '_local', 'census')
TR = os.path.join(RUN, 'trace')
CXXFILT = r'C:\Program Files\LLVM\bin\llvm-cxxfilt.exe'
OUT = []


def W(s=''):
    OUT.append(s)


def tsv(name, path=TR):
    p = os.path.join(path, name)
    if not os.path.exists(p):
        return []
    rows = [l.rstrip('\n').split('\t') for l in open(p, encoding='utf-8', errors='replace')]
    return rows[1:]


def demangle(names):
    names = sorted(set(n for n in names if n))
    if not names or not os.path.exists(CXXFILT):
        return {n: n for n in names}
    p = subprocess.run([CXXFILT], input='\n'.join(names) + '\n', capture_output=True, text=True, encoding='latin1')
    return dict(zip(names, p.stdout.split('\n')))


# ---------- modules, functions, symbols ----------
MODS = {}
for r in tsv('modules.tsv'):
    MODS[r[0]] = dict(base=int(r[1], 16), size=int(r[2], 16), text=int(r[3], 16), patch_off=int(r[4]))


class Mod:
    def __init__(self, name):
        self.name = name
        f = json.load(open(os.path.join(CENSUS, f'fdes_{name}.json')))
        rg = sorted(f['ranges'])
        self.starts = [s for s, _ in rg]; self.ends = [e for _, e in rg]; self.startset = set(self.starts)
        self.lsda = set(f.get('lsda', []))
        nso = nsofmt.load_nso(os.path.join(EXEFS, name), name)
        self.img = nso.image; self.nso = nso
        self.text = MODS[name]['text']
        self.words = np.frombuffer(bytes(self.img[:self.text & ~3]), dtype='<u4')
        w = self.words
        st = np.nonzero((w[:-3] & 0x9F00001F == 0x90000010) & (w[1:-2] & 0xFFC003FF == 0xF9400211) & (w[2:-1] & 0xFFC003FF == 0x91000210) & (w[3:] == 0xD61F0220))[0]
        self.plt_stub = set(int(i) * 4 for i in st)
        self.plt_words = set(o + k for o in self.plt_stub for k in (0, 4, 8, 12))
        syms, _ = nsofmt.symbols(self.img, nso)
        self.syms = syms
        self.fname = {}
        for s in syms:
            if s.type == 2 and s.shndx and s.value < self.text and (s.value not in self.fname or (s.bind == 1 and len(s.name) < len(self.fname[s.value]))):
                self.fname[s.value] = s.name

    def fde(self, off):
        i = bisect.bisect_right(self.starts, off) - 1
        return i if i >= 0 and off < self.ends[i] else None

    def func_name(self, off):
        i = self.fde(off)
        if i is None:
            return f'{self.name}+{off:#x} (no FDE{", PLT" if off in self.plt_words else ""})'
        s = self.starts[i]
        n = self.fname.get(s)
        return n if n else f'{self.name}+{s:#x}'

    def stub_target_names(self):
        """PLT stub offset -> imported symbol name"""
        tags = dict(nsofmt.dynamic(self.img, nsofmt.mod0(self.img).dynamic))
        _, jr, _ = nsofmt.relocations(self.img, tags)
        byidx = {s.index: s.name for s in self.syms}
        got = {r.offset: byidx.get(r.sym, '?') for r in jr}
        out = {}
        for s in self.plt_stub:
            a, l = int(self.words[s // 4]), int(self.words[s // 4 + 1])
            immlo, immhi = (a >> 29) & 3, (a >> 5) & 0x7FFFF
            page = ((immhi << 2 | immlo) << 12)
            if page & (1 << 32):
                page -= 1 << 33
            g = ((s & ~0xFFF) + page) + ((l >> 10) & 0xFFF) * 8
            out[s] = got.get(g, '?')
        return out


M = {n: Mod(n) for n in MODS}


def locate(a):
    for n, d in MODS.items():
        if d['base'] <= a < d['base'] + d['size']:
            return n, a - d['base']
        if d['patch_off'] < 0 and d['base'] + d['patch_off'] <= a < d['base']:
            return n, a - d['base']
    return None, a


def where(a):
    n, o = locate(a)
    if n is None:
        return 'host' if a else '0'
    if o >= MODS[n]['text'] or o < 0:
        return f'{n}+{o:#x} (patch area)'
    return M[n].func_name(o)


# ---------- phases ----------
PH = []
for r in tsv('phases.tsv', RUN):
    if r[1].startswith('phase '):
        PH.append((float(r[0]), r[1][6:]))
END = max((float(r[0]) for r in tsv('phases.tsv', RUN) if r[1] == 'flush'), default=1e9)
PHASES = [(0.0, 'boot')] + PH


def phase(ms):
    s = ms / 1000.0; name = 'boot'
    for t, n in PHASES:
        if s >= t:
            name = n
    return name


PNAMES = [n for _, n in PHASES]
W(f'# Trace summary: {os.path.basename(os.path.abspath(RUN))}\n')
W('Phases (s since host start): ' + ', '.join(f'{n} {t:.1f}' for t, n in PHASES) + f', flush {END:.1f}\n')


def logrecs(name, fmt):
    p = os.path.join(TR, name)
    if not os.path.exists(p):
        return []
    sz = struct.calcsize(fmt); d = open(p, 'rb').read()
    return [struct.unpack_from(fmt, d, i) for i in range(0, len(d) - sz + 1, sz)]


# ---------- info ----------
W('## Tick frequency\n')
for r in tsv('info.tsv'):
    W(f'- {r[0]}: {r[1]}')
W()

# ---------- FPCR ----------
def fpcr_str(v):
    rm = ['RN', 'RP', 'RM', 'RZ'][(v >> 22) & 3]
    bits = [n for b, n in ((26, 'AHP'), (25, 'DN'), (24, 'FZ'), (19, 'FZ16')) if v >> b & 1]
    traps = v & 0x9F00
    return f'{v:#x} ({rm}{", " + ", ".join(bits) if bits else ""}{f", traps {traps:#x}" if traps else ""})'


W('## FPCR\n')
g = collections.Counter(); gth = collections.defaultdict(collections.Counter)
for th, fp, n in tsv('gate_fpcr.tsv'):
    g[int(fp, 16)] += int(n); gth[int(fp, 16)][th] += int(n)
W('| source | events | distinct FPCR values |\n|---|---:|---|')
W(f'| gates (every svc, counter read and spin wait, all guest threads) | {sum(g.values()):,} | ' + '; '.join(f'{fpcr_str(v)} ×{n:,} on {len(gth[v])} threads' for v, n in g.most_common()) + ' |')
c = collections.Counter({int(r[0], 16): int(r[1]) for r in tsv('cov_fpcr.tsv')})
W(f'| first execution of each traced word (coverage traps) | {sum(c.values()):,} | ' + '; '.join(f'{fpcr_str(v)} ×{n:,}' for v, n in c.most_common()) + ' |')
st = collections.Counter(); nthr = 0; guest_thr = 0
for r in tsv('threads.tsv'):
    if int(r[3]) > 0:
        guest_thr += 1
    for kv in r[4:]:
        v, n = kv.split(':'); st[int(v, 16)] += int(n)
W(f'| sampler (thread state of threads stopped in guest code) | {sum(st.values()):,} | ' + '; '.join(f'{fpcr_str(v)} ×{n:,}' for v, n in st.most_common()) + f' (threads seen in guest code: {guest_thr}) |')
PR = {int(r[0]): r for r in tsv('probes.tsv')}
precs = logrecs('probe_log.bin', '<IHHIIQQQ')
pf = collections.Counter(r[3] for r in precs)
W(f'| probe hits (fpcr at each probed function or site) | {len(precs):,} | ' + '; '.join(f'{fpcr_str(v)} ×{n:,}' for v, n in pf.most_common()) + ' |')
W()
W('Every MRS/MSR of FPCR and FPSR in sdk (27 sites), probed:\n')
W('| site | instruction | hits | values |\n|---|---|---:|---|')
pv = collections.defaultdict(collections.Counter)
for r in precs:
    pv[r[1]][r[7]] += 1
for i, r in sorted(PR.items()):
    ins = int(r[4], 16)
    if (ins & 0xFFDFFFC0) == 0xD51B4400:
        kind = ('msr fpcr' if not (ins >> 5) & 1 else 'msr fpsr') if not (ins >> 21) & 1 else ('mrs fpcr' if not (ins >> 5) & 1 else 'mrs fpsr')
        nm = M['sdk'].func_name(int(r[3], 16))
        W(f'| sdk+{int(r[3], 16):#x} {demangle([nm]).get(nm, nm)} | {kind} | {int(r[5]):,} | ' + ', '.join(f'{v:#x}×{n}' for v, n in pv[i].most_common(6)) + ' |')
W()

# ---------- gates (counter reads) ----------
W('## Gates\n')
gk = collections.Counter(); rows = []
for mod, off, kind, arg, n in tsv('gates.tsv'):
    gk[kind] += int(n); rows.append((int(n), mod, int(off, 16), kind, arg))
dur = END
W('Calls by kind over the session (' + f'{dur:.0f} s): ' + ', '.join(f'{k} {n:,} ({n / dur:,.0f}/s)' for k, n in gk.most_common()) + '\n')
cnt = [r for r in rows if r[3] in ('cntpct', 'cntvct')]
names = demangle([M[r[1]].func_name(r[2]) for r in cnt if r[1] in M])
W(f'Counter-read sites executed: {len(cnt)} of 43 rewritten.\n')
RW = os.path.join(os.path.dirname(os.path.abspath(RUN)), 'rewriter')
allc = [(int(r[3], 16), r[1]) for r in tsv('sdk.gates.tsv', RW) if r and r[1] in ('cntpct', 'cntvct')] if os.path.exists(RW) else []
if allc:
    used = set(r[2] for r in cnt if r[1] == 'sdk')
    fns = collections.Counter(M['sdk'].func_name(o) for o, _ in allc)
    dn = demangle(fns)
    W('All 43 rewritten counter reads by function (sites executed / sites): ' + '; '.join(
        f'{dn.get(f, f)} {sum(1 for o, _ in allc if M["sdk"].func_name(o) == f and o in used)}/{c}' for f, c in fns.most_common()) + '\n')
W('| site | function | reads | per s |\n|---|---|---:|---:|')
for n, mod, off, kind, arg in sorted(cnt, reverse=True)[:20]:
    nm = M[mod].func_name(off) if mod in M else '?'
    W(f'| {mod}+{off:#x} | {names.get(nm, nm)} | {n:,} | {n / dur:,.0f} |')
W()
secs = tsv('secs.tsv')
if secs:
    by = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for r in secs:
        p = phase(int(r[0]) * 1000 + 500)
        for k in range(6):
            by[p][k] += int(r[1 + k])
    W('| phase | counter reads | svcs | other gates | samples in guest text | in patch areas | outside the modules |\n|---|---:|---:|---:|---:|---:|---:|')
    for p in PNAMES:
        if p in by:
            W(f'| {p} | ' + ' | '.join(f'{v:,}' for v in by[p]) + ' |')
    W()

# ---------- probes: exceptions, setjmp/longjmp, tick frequency ----------
W('## Probes\n')
byp = collections.defaultdict(list)
for r in precs:
    byp[r[1]].append(r)
W('| probe | hits | ' + ' | '.join(PNAMES) + ' |\n|---|---:|' + '---:|' * len(PNAMES))
for i, r in sorted(PR.items()):
    if r[1].startswith('sdk+'):
        continue
    ph = collections.Counter(phase(x[0] - 1) for x in byp[i])
    W(f'| {r[1]} | {int(r[5]):,} | ' + ' | '.join(f'{ph.get(p, 0):,}' for p in PNAMES) + ' |')
W()
TT = {int(r[0], 16): r[1] for r in tsv('throw_types.tsv')}
for i, r in sorted(PR.items()):
    if r[1].startswith('sdk+') or not byp[i]:
        continue
    cl = collections.Counter(where(x[5]) for x in byp[i])
    c2 = collections.Counter((where(x[5]), where(x[6])) for x in byp[i])
    dn = demangle(list(cl) + [b for _, b in c2])
    W(f'### {r[1]}: {len(byp[i]):,} hits from {len(cl)} callers, {len(set(x[2] for x in byp[i]))} threads\n')
    W('| caller (lr) | its caller | hits |\n|---|---|---:|')
    for (a, b), n in c2.most_common(12):
        W(f'| {dn.get(a, a)} | {dn.get(b, b)} | {n:,} |')
    if r[1] == '__cxa_throw':
        ty = collections.Counter(TT.get(x[7], f'{x[7]:#x}') for x in byp[i])
        W('\nTypes thrown: ' + ', '.join(f'{t} ×{n}' for t, n in ty.most_common()))
    W()

# static call sites: BL / B to a PLT stub that imports the name, or straight to the module's own definition
WANT = ('setjmp', '_setjmp', 'longjmp', '_longjmp', '__cxa_throw', '__cxa_rethrow', '_Unwind_RaiseException', '_Unwind_Resume')
W('Static call sites (BL / B to the PLT stub that imports the name, or to the module\'s own definition), and how many of the calling functions ran:\n')
W('| module | callee | BL sites | B (tail) sites | calling functions | of which executed |\n|---|---|---:|---:|---:|---:|')
EXEC = {}
for n, m in M.items():
    EXEC[n] = set()
covr = logrecs('cov_log.bin', '<IHBBQ')
for ms1, tid, kind, _, at in covr:
    n_, o_ = locate(at)
    if n_ in M and o_ in M[n_].startset:
        EXEC[n_].add(o_)
STATIC_CALLERS = {}
for n, m in M.items():
    st = m.stub_target_names()
    defs = {s.name: s.value for s in m.syms if s.shndx and s.value and s.name in WANT}
    w = m.words; idx = np.arange(len(w), dtype=np.int64)
    isbl = (w & 0xFC000000) == 0x94000000; isb = (w & 0xFC000000) == 0x14000000
    imm = ((w & 0x3FFFFFF).astype(np.int64) ^ 0x2000000) - 0x2000000
    tg = idx * 4 + imm * 4
    for name in WANT:
        tgts = [s for s, nm in st.items() if nm == name] + ([defs[name]] if name in defs else [])
        if not tgts:
            continue
        sel = np.isin(tg, tgts)
        bl = idx[isbl & sel] * 4; b = idx[isb & sel] * 4
        fn = set(m.starts[m.fde(int(o))] for o in list(bl) + list(b) if m.fde(int(o)) is not None)
        STATIC_CALLERS[(n, name)] = sorted(fn)
        if len(bl) + len(b):
            W(f'| {n} | {name} | {len(bl)} | {len(b)} | {len(fn)} | {len(fn & EXEC[n])} |')
W()
for name in ('setjmp', 'longjmp'):
    for (n, nm), fns in STATIC_CALLERS.items():
        if nm == name and fns:
            dn = demangle(M[n].func_name(f) for f in fns)
            W(f'- {name} is called from {n}: ' + ', '.join(f'{dn.get(M[n].func_name(f), M[n].func_name(f))}{" (ran)" if f in EXEC[n] else ""}' for f in fns[:10]))
W()

# ---------- coverage ----------
W('## Coverage\n')
cov = logrecs('cov_log.bin', '<IHBBQ')
ent = collections.defaultdict(dict); outw = collections.defaultdict(list); inner = collections.Counter()
for ms1, tid, kind, _, at in cov:
    n, o = locate(at)
    if n not in M:
        continue
    m = M[n]
    if o in m.startset:
        ent[n].setdefault(o, ms1 - 1)
    elif m.fde(o) is None:
        outw[n].append((o, ms1 - 1, kind))
    else:
        inner[n] += 1   # a br/blr inside a function (kind 2): executed, not an entry
W('| module | FDEs | FDE starts executed | % | first run in: ' + ' | '.join(PNAMES) + ' |\n|---|---:|---:|---:|' + '---:|' * len(PNAMES))
for n, m in M.items():
    e = ent[n]; ph = collections.Counter(phase(t) for t in e.values())
    W(f'| {n} | {len(m.starts):,} | {len(e):,} | {100 * len(e) / len(m.starts):.1f} | ' + ' | '.join(f'{ph.get(p, 0):,}' for p in PNAMES) + ' |')
    with open(os.path.join(RUN, f'executed_{n}.txt'), 'w', newline='\n') as f:
        for o in sorted(e):
            f.write(f'{o:x}\t{e[o]}\n')
W()
lsda_exec = {n: len(set(ent[n]) & M[n].lsda) for n in M}
W('Executed functions that have an LSDA (C++ handlers or cleanups): ' + ', '.join(f'{n} {lsda_exec[n]:,} of {len(M[n].lsda):,}' for n in M) + '\n')
W('Executed words outside every FDE:\n')
W('| module | words | in PLT stubs | other | other regions |\n|---|---:|---:|---:|---|')
for n, m in M.items():
    ow = outw[n]; plt = [x for x in ow if x[0] in m.plt_words]; oth = sorted(x for x in ow if x[0] not in m.plt_words)
    regs = []
    for o, t, k in oth:
        if regs and o - regs[-1][1] <= 64:
            regs[-1][1] = o; regs[-1][2] += 1
        else:
            regs.append([o, o, 1])
    W(f'| {n} | {len(ow):,} | {len(plt):,} | {len(oth):,} | ' + ', '.join(f'{a:#x}..{b + 4:#x} ({c})' for a, b, c in regs[:12]) + (' …' if len(regs) > 12 else '') + ' |')
W()

# ---------- indirect branch targets ----------
ind = logrecs('ind_log.bin', '<IHHQQ')
cls = collections.Counter(); sites = collections.defaultdict(set); odd = collections.Counter(); hostt = collections.Counter()
for ms1, tid, _, at, t in ind:
    sn, so = locate(at)
    if sn not in M:
        continue
    ins = int(M[sn].words[so // 4]); isbr = (ins & 0xFFFFFC1F) == 0xD61F0000
    k = 'BR' if isbr else 'BLR'
    tn, to = locate(t)
    if tn is None:
        c = 'host function (HLE or stub)'; hostt[(k, where(at))] += 1
    elif to >= MODS[tn]['text'] or to < 0:
        c = 'patch area'
    elif to in M[tn].startset:
        c = 'FDE start'
    elif M[tn].fde(to) is not None:
        if isbr and tn == sn and M[tn].fde(to) == M[sn].fde(so):
            c = 'inside its own function (jump table / local)'
        else:
            c = 'INSIDE ANOTHER FUNCTION'; odd[(k, where(at), f'{tn}+{to:#x}')] += 1
    elif to in M[tn].plt_stub:
        c = 'PLT stub'
    else:
        c = 'outside FDEs'; odd[(k, where(at), f'{tn}+{to:#x}')] += 1
    cls[(k, c)] += 1; sites[(k, c)].add((sn, so))
W('## Indirect branch targets (first 8 executions of every executed br/blr)\n')
W('| branch | target | executions logged | sites |\n|---|---|---:|---:|')
for (k, c), n in sorted(cls.items()):
    W(f'| {k} | {c} | {n:,} | {len(sites[(k, c)]):,} |')
W()
allsites = set().union(*sites.values()) if sites else set()
W(f'Indirect sites executed: {len(allsites):,}. Logged targets that are neither an FDE start, a PLT stub, a label in the same function nor host code: {sum(odd.values())}\n')
for (k, a, b), n in odd.most_common(20):
    W(f'- {k} in {a} → {b} ×{n}')
W()

# ---------- sampling ----------
W('## Sampled pcs\n')
tot = collections.Counter(); topf = collections.Counter(); outs = collections.Counter()
for n, m in M.items():
    for r in tsv(f'samples_{n}.tsv'):
        o, c = int(r[0], 16), int(r[1])
        i = m.fde(o)
        if i is None:
            k = 'outside FDEs (PLT)' if o in m.plt_words else 'outside FDEs (other)'
            outs[f'{n}+{o:#x}'] += c
        else:
            k = 'inside an FDE'; topf[(n, m.starts[i])] += c
        tot[(n, k)] += c
allc = sum(tot.values())
W('| module | where | samples | share |\n|---|---|---:|---:|')
for (n, k), c in sorted(tot.items()):
    W(f'| {n} | {k} | {c:,} | {100 * c / allc:.3f} % |')
W(f'\nTotal guest-text samples: {allc:,}.\n')
dn = demangle(M[n].fname.get(s, '') for n, s in topf)
W('| function | samples |\n|---|---:|')
for (n, s), c in topf.most_common(15):
    nm = M[n].fname.get(s)
    W(f'| {dn.get(nm, nm) if nm else f"{n}+{s:#x}"} | {c:,} |')
if outs:
    W('\nOutside-FDE samples: ' + ', '.join(f'{a} ×{c}' for a, c in outs.most_common(10)))
W()
pr = tsv('samples_patch.tsv')
if pr:
    pk = collections.Counter(); psite = collections.Counter()
    for mod, off, c, site, blk in pr:
        c = int(c); site = int(site); m = M.get(mod)
        if site < 0:
            k = 'no rewriter branch within 4 KB before it (data or host-made code)'
        elif m and m.fde(site) is not None:
            k = 'block of a rewritten site inside an FDE'; psite[(mod, site)] += c
        elif m and site in m.plt_words:
            k = 'block of a rewritten site in a PLT stub'
        else:
            k = 'block of a rewritten site outside FDEs'
        pk[k] += c
    W(f'Samples in nxnative\'s patch areas (the rewriter\'s moved copies of original instructions): {sum(pk.values()):,}\n')
    for k, c in pk.most_common():
        W(f'- {k}: {c:,}')
    W()

open(os.path.join(RUN, 'summary.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(OUT) + '\n')
print('\n'.join(OUT))
