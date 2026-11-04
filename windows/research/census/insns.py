"""Census part 2: linear-sweep instruction census of a module's whole text segment (single process, single thread).
Usage: py -3 -I insns.py <ExeFS dir> <scratch dir> <module>
Needs <scratch>/fdes_<module>.json from layout.py. Writes <scratch>/insns_<module>.json (counters and site address lists, no code bytes).

Method: capstone 5 disasm_lite over the text in 16 KB chunks; at a word it cannot decode the sweep records the word and restarts 4 bytes later, so every
word of text is either decoded or counted as undecodable. Each instruction is keyed by (mnemonic, encoding group from bits 28:25, register signature for
SIMD&FP); the classes are derived from those keys afterwards (summarize.py). Indirect branches are classified by walking back through the instructions of the
same function (FDE range) to the register's definition, stopping at unconditional control flow and, for caller-saved registers, at calls."""
import collections, json, os, re, struct, sys, time
from array import array

import capstone

sys.dont_write_bytecode = True   # keep this folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nsofmt as F

# ---- encoding groups (ARM ARM top-level decode, op0 = bits 28:25) ----
EG = {}
for op0 in range(16):
    if op0 in (8, 9): EG[op0] = 'imm'
    elif op0 in (10, 11): EG[op0] = 'bes'          # branches, exception generating, system
    elif op0 & 5 == 4: EG[op0] = 'ls'
    elif op0 & 7 == 5: EG[op0] = 'reg'
    elif op0 & 7 == 7: EG[op0] = 'fp'
    elif op0 == 2: EG[op0] = 'sve'
    elif op0 == 0: EG[op0] = 'sme/rsvd'
    else: EG[op0] = 'unalloc'


def egroup(w):
    g = EG[(w >> 25) & 0xF]
    if g == 'ls':
        return 'lsv' if w >> 26 & 1 else 'ls'
    if g == 'fp':
        top = w >> 28
        if top & 9 == 0: return 'fp:vec'
        if top & 0xD == 5: return 'fp:simdscalar'
        if top & 5 == 1: return 'fp:scalar' + (':h' if (w >> 22) & 3 == 3 else '')
        if top == 0xC: return 'fp:crypto'
        return 'fp:other'
    return g


RS = re.compile(r'\b([vz])\d+\.(\d*[bhsdq])|\b([qdshbxwvzp])(?:\d+|zr)\b')


def regsig(ops):
    out = []
    for m in RS.finditer(ops):
        t = m.group(1) + '.' + m.group(2) if m.group(1) else m.group(3)
        if t not in out: out.append(t)
    return ','.join(out)


X18 = re.compile(r'\b[xw]18\b')
REG = re.compile(r'^([xw])(\d+)$')
UNCOND = {'b', 'br', 'ret', 'braa', 'brab', 'braaz', 'brabz', 'retaa', 'retab', 'eret'}
CALLS = {'bl', 'blr', 'blraa', 'blrab', 'blraaz', 'blrabz'}
NODEST = {'cmp', 'cmn', 'tst', 'ccmp', 'ccmn', 'fcmp', 'fcmpe', 'fccmp', 'fccmpe', 'prfm', 'prfum', 'msr', 'dmb', 'dsb', 'isb', 'nop', 'svc', 'brk',
          'hlt', 'cbz', 'cbnz', 'tbz', 'tbnz', 'b', 'bl', 'br', 'blr', 'ret', 'dc', 'ic', 'sys', 'tlbi', 'at', 'clrex', 'hint', 'yield', 'wfe', 'wfi',
          'sev', 'sevl', 'bti', 'udf'}
PAIRLOAD = {'ldp', 'ldnp', 'ldpsw', 'ldxp', 'ldaxp'}
STATUS_ST = {'stxr', 'stxrb', 'stxrh', 'stlxr', 'stlxrb', 'stlxrh', 'stxp', 'stlxp'}
LSE_LD = ('ldadd', 'ldclr', 'ldeor', 'ldset', 'ldsmax', 'ldsmin', 'ldumax', 'ldumin', 'swp')


def regnum(tok):
    tok = tok.strip().lstrip('{[')
    m = REG.match(tok)
    if m: return int(m.group(2))
    if tok in ('sp', 'wsp'): return 'sp'
    return None


def dests(mn, ops):
    if mn in NODEST or mn.startswith('b.') or (mn.startswith('st') and mn not in STATUS_ST):
        if ']!' in ops or '], ' in ops:
            b = ops.split('[', 1)[1].split(']', 1)[0].split(',')[0]
            return (regnum(b),)
        return ()
    parts = ops.split(', ')
    if mn.startswith(LSE_LD) and len(parts) > 1:
        return (regnum(parts[1]),)
    if mn in PAIRLOAD and len(parts) > 1:
        return (regnum(parts[0]), regnum(parts[1]))
    return (regnum(parts[0]),)


def find_def(H, i, reg):
    """(index, entry) of the last writer of reg before H[i], or (reason, None)"""
    for j in range(i - 1, -1, -1):
        a, mn, ops = H[j]
        if mn in UNCOND:
            return 'block start', None
        if mn in CALLS and isinstance(reg, int) and reg <= 18:
            return 'clobbered by call', None
        if reg in dests(mn, ops):
            return j, H[j]
    return 'function start / window / undecodable word', None


def mem_operand(ops):
    """(base, imm or None, index reg or None) of the [..] operand"""
    if '[' not in ops:
        return None
    inner = ops.split('[', 1)[1].split(']', 1)[0]
    p = [x.strip() for x in inner.split(',')]
    base = regnum(p[0])
    imm, idx = 0, None
    if len(p) > 1:
        if p[1].startswith('#'):
            imm = int(p[1][1:], 0)
        else:
            idx = regnum(p[1])
    return base, imm, idx


def imm_of(tok):
    return int(tok.strip().lstrip('#'), 0)


def addr_of(H, i, reg, depth=0):
    """The address reg holds at H[i] when materialized by adr / adrp (+ add) in the window, else None"""
    if depth > 4: return None
    j, d = find_def(H, i, reg)
    if d is None: return None
    a, mn, ops = d
    p = ops.split(', ')
    if mn in ('adr', 'adrp'):
        return imm_of(p[1])
    if mn == 'add' and len(p) == 3 and p[2].startswith('#'):
        b = addr_of(H, j, regnum(p[1]), depth + 1)
        return None if b is None else b + imm_of(p[2])
    if mn == 'add' and len(p) == 4 and p[2].startswith('#') and p[3] == 'lsl #12':
        b = addr_of(H, j, regnum(p[1]), depth + 1)
        return None if b is None else b + (imm_of(p[2]) << 12)
    if mn == 'mov' and regnum(p[1]) is not None and p[1][0] == 'x':
        return addr_of(H, j, regnum(p[1]), depth + 1)
    return None


class Census:
    def __init__(self, img, info):
        self.img = img
        self.text_end = info['text_end']
        self.ranges = [tuple(r) for r in info['ranges']]
        self.starts = {s for s, _ in self.ranges}
        self.segs = info['segs']
        self.bss = info['bss']
        plt_got = info['plt']
        self.plt_lo = self.plt_hi = None
        self.c = collections.Counter()            # (mn, egroup, sig)
        self.misc = collections.Counter()
        self.sysreg = collections.Counter()
        self.svc = collections.Counter()
        self.sites = collections.defaultdict(list)  # kind -> [addr]
        self.barrier = collections.Counter()
        self.brk = collections.Counter()
        self.cache = collections.Counter()
        self.br = collections.Counter()
        self.blr = collections.Counter()
        self.vt_slots = collections.Counter()
        self.jt = collections.Counter()
        self.jt_entries = []
        self.jt_target_check = collections.Counter()
        self.jt_out = collections.Counter()
        self.jt_out_tables = []
        self.tls = collections.Counter()
        self.undec = collections.Counter()
        self.undec_where = collections.Counter()
        self.undec_addrs = []
        self.gap = collections.Counter()
        self.bl_tgt = collections.Counter()
        self.b_tail = collections.Counter()
        self.lit = collections.Counter()
        self.adr = collections.Counter()
        self.ls_mode = collections.Counter()
        self.samples = collections.defaultdict(list)
        self.tail_targets = set()
        self.bl_targets = collections.Counter()
        self.gap_bl_targets = set()
        self.examples = collections.defaultdict(list)

    def where(self, a):
        for k in ('text', 'ro', 'data'):
            mo, sz = self.segs[k]
            if mo <= a < mo + sz: return k
        if self.bss[0] <= a < self.bss[1]: return 'bss'
        return 'outside'

    def sample(self, k, a, n=8):
        if len(self.samples[k]) < n: self.samples[k].append(a)

    def run(self):
        img, text_end = self.img, self.text_end
        text = bytes(img[:text_end])
        words = array('I'); words.frombytes(text[:text_end & ~3])
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        ranges, nr = self.ranges, len(self.ranges)
        # PLT region: the largest gap whose words are adrp x16 / ldr x17 / add x16 / br x17 groups is found below; here the tail gap guess
        ri = 0
        H = collections.deque(maxlen=48)
        cur = (0, 0)          # current function (start, end) or gap
        in_fde = False
        pend = []             # TLS: [(reg, kind, addr, remaining)]
        c, misc = self.c, self.misc
        CH = 0x4000
        t0 = time.time()
        for cs in range(0, text_end & ~3, CH):
            ce = min(cs + CH, text_end & ~3)
            off = cs
            while off < ce:
                nxt = off
                for addr, size, mn, ops in md.disasm_lite(text[off:ce], off):
                    nxt = addr + 4
                    # function context
                    while ri < nr and ranges[ri][1] <= addr: ri += 1
                    if ri < nr and ranges[ri][0] <= addr:
                        if not in_fde or cur[0] != ranges[ri][0]:
                            cur = ranges[ri]; in_fde = True; H.clear(); pend = []
                    else:
                        if in_fde or addr < cur[0] or addr >= cur[1]:
                            gs = addr; ge = ranges[ri][0] if ri < nr else text_end
                            cur = (gs, ge); in_fde = False; H.clear(); pend = []
                    w = words[addr >> 2]
                    g = egroup(w)
                    if g[0] in 'fl' and (g[0] == 'f' or g == 'lsv'):
                        c[(mn, g, regsig(ops))] += 1
                    elif g == 'sve' or g == 'sme/rsvd':
                        c[(mn, g, regsig(ops))] += 1
                    else:
                        c[(mn, g, '')] += 1
                    if not in_fde:
                        self.gap['insns'] += 1
                        self.gap['mn:' + mn] += 1
                    if '18' in ops and X18.search(ops):
                        misc['x18_use'] += 1; self.sites['x18'].append(addr)
                    if pend:
                        self.tls_pending(pend, addr, mn, ops)
                    H.append((addr, mn, ops))
                    h = HANDLERS.get(mn)
                    if h is not None:
                        h(self, addr, mn, ops, w, H, cur, in_fde, pend)
                    elif g == 'ls' or g == 'lsv':
                        self.ls(addr, mn, ops)
                    elif mn[:2] == 'b.':
                        self.cond_branch(addr, mn, ops, cur)
                    if mn in UNCOND:
                        H.clear()
                if nxt < ce:
                    w = words[nxt >> 2]
                    self.undec[w] += 1
                    self.undec_where['fde' if (ri < nr and ranges[ri][0] <= nxt < ranges[ri][1]) else 'gap'] += 1
                    if w == 0: misc['undecodable_zero_words'] += 1
                    if len(self.undec_addrs) < 4000: self.undec_addrs.append(nxt)
                    H.clear()
                    off = nxt + 4
                else:
                    off = ce
        self.elapsed = time.time() - t0

    # ---- handlers ----
    def tls_pending(self, pend, addr, mn, ops):
        """First use of the register an mrs tpidr_el0 / tpidrro_el0 wrote, within the next 8 instructions"""
        keep = []
        for reg, kind, a0, rem in pend:
            r = re.compile(r'[xw]%d' % reg)
            parts = ops.split(', ')
            m = mem_operand(ops)
            if m and m[0] == reg:
                pat = '%s X, [TP, #%#x]' % (mn, m[1]) if m[2] is None else '%s X, [TP, reg]' % mn
                if mn.startswith('st'): pat = pat.replace('X, ', 'X -> ')
            elif len(parts) > 1 and r.search(', '.join(parts[1:])) and not mn.startswith(('st', 'cb', 'tb', 'cmp')):
                if mn in ('add', 'sub'):
                    pat = '%s X, TP, %s' % (mn, '#imm' if parts[-1].startswith('#') else 'reg')
                else:
                    pat = mn + ' X, TP'
            elif r.search(ops):
                if r.match(parts[0]) and not mn.startswith(('st', 'cb', 'tb', 'cmp')):
                    pat = 'overwritten before use (' + mn + ')'
                else:
                    pat = mn + ' (TP as operand)'
            else:
                if rem > 1:
                    keep.append((reg, kind, a0, rem - 1))
                else:
                    self.tls[(kind, 'no use within 8')] += 1
                continue
            self.tls[(kind, pat)] += 1
        pend[:] = keep

    def h_mrs_msr(self, addr, mn, ops, w, H, cur, in_fde, pend):
        p = ops.split(', ')
        if mn == 'mrs':
            reg = p[1]
            self.sysreg[('mrs', reg)] += 1
            if reg in ('tpidr_el0', 'tpidrro_el0'):
                self.sites['mrs ' + reg].append(addr)
                r = regnum(p[0])
                pend.append((r, reg, addr, 8))
            elif reg in ('cntpct_el0', 'cntvct_el0', 'cntfrq_el0', 'ctr_el0', 'dczid_el0', 'fpcr', 'fpsr', 'nzcv', 'midr_el1', 'mpidr_el1'):
                self.sites['mrs ' + reg].append(addr)
            else:
                self.sites['mrs other'].append(addr)
        else:
            reg = p[0]
            kind = 'msr' if not p[1].startswith('#') else 'msr(imm)'
            self.sysreg[(kind, reg)] += 1
            self.sites[kind + ' ' + reg].append(addr)

    def h_svc(self, addr, mn, ops, w, H, cur, in_fde, pend):
        if mn == 'svc':
            self.svc[(w >> 5) & 0xFFFF] += 1
            self.sites['svc'].append(addr)
        else:
            self.brk[(mn, ops)] += 1
            self.sites[mn].append(addr)

    def h_barrier(self, addr, mn, ops, w, H, cur, in_fde, pend):
        self.barrier[(mn, ops)] += 1

    def h_cache(self, addr, mn, ops, w, H, cur, in_fde, pend):
        self.cache[(mn, ops.split(',')[0])] += 1
        self.sites[mn + ' ' + ops.split(',')[0]].append(addr)

    def h_sys(self, addr, mn, ops, w, H, cur, in_fde, pend):
        self.cache[(mn, ops)] += 1
        self.sites[mn].append(addr)

    def target(self, ops):
        return int(ops.rsplit('#', 1)[1], 0)

    def h_b(self, addr, mn, ops, w, H, cur, in_fde, pend):
        t = self.target(ops)
        if cur[0] <= t < cur[1]:
            self.misc['b_intra'] += 1; return
        if t in self.starts:
            self.b_tail['b to another FDE start (tail call)'] += 1; self.tail_targets.add(t)
        elif self.plt_lo is not None and self.plt_lo <= t < self.plt_hi:
            self.b_tail['b to PLT stub (tail call to import)'] += 1
        elif 0 <= t < self.text_end:
            k = 'b out of function, target in another FDE (not its start)' if self.in_any_fde(t) else 'b out of function, target in a gap'
            self.b_tail[k] += 1; self.sample(k, addr)
        else:
            self.b_tail['b outside text'] += 1
        if not in_fde: self.b_tail['(of which from gap code)'] += 1

    def cond_branch(self, addr, mn, ops, cur):
        t = self.target(ops)
        if not (cur[0] <= t < cur[1]):
            k = 'conditional branch leaving function' + (' (to FDE start)' if t in self.starts else '')
            self.b_tail[k] += 1; self.sample(k, addr)

    def h_cb(self, addr, mn, ops, w, H, cur, in_fde, pend):
        self.cond_branch(addr, mn, ops, cur)

    def in_any_fde(self, t):
        import bisect
        i = bisect.bisect_right(self._starts_sorted, t) - 1
        return i >= 0 and self.ranges[i][0] <= t < self.ranges[i][1]

    def h_bl(self, addr, mn, ops, w, H, cur, in_fde, pend):
        t = self.target(ops)
        self.bl_targets[t] += 1
        if t in self.starts: k = 'FDE start'
        elif self.plt_lo is not None and self.plt_lo <= t < self.plt_hi: k = 'PLT stub (import)'
        elif not 0 <= t < self.text_end: k = 'outside text'
        elif self.in_any_fde(t): k = 'inside an FDE (not its start)'; self.sample('bl mid-FDE', addr)
        else: k = 'gap (code without FDE)'; self.gap_bl_targets.add(t)
        self.bl_tgt[k] += 1

    def h_ldst_special(self, addr, mn, ops, w, H, cur, in_fde, pend):
        self.ls(addr, mn, ops)

    def ls(self, addr, mn, ops):
        if '[' not in ops:
            if mn in ('ldr', 'ldrsw', 'prfm'):
                t = int(ops.rsplit('#', 1)[1], 0)
                self.lit[self.where(t)] += 1
                if t < self.text_end: self.sample('ldr literal into text', addr)
            return
        if ']!' in ops: self.ls_mode['pre-index writeback'] += 1
        elif '], ' in ops: self.ls_mode['post-index writeback'] += 1
        else:
            inner = ops.split('[', 1)[1].split(']', 1)[0]
            if ',' in inner and not inner.split(',', 1)[1].strip().startswith('#'):
                self.ls_mode['register offset'] += 1
            else:
                self.ls_mode['base + immediate'] += 1

    def h_adr(self, addr, mn, ops, w, H, cur, in_fde, pend):
        t = int(ops.split(', ')[1].lstrip('#'), 0)
        if mn == 'adr':
            if t in self.starts: self.code_addr_taken.add(t)
            if t < self.text_end:
                self.adr['adr -> text: ' + ('FDE start' if t in self.starts else 'same function' if cur[0] <= t < cur[1] else 'other')] += 1
            else:
                self.adr['adr -> ' + self.where(t)] += 1
        else:
            self.adr['adrp page in ' + self.where(t)] += 1

    def h_add(self, addr, mn, ops, w, H, cur, in_fde, pend):
        """adrp + add materializing an address: code addresses taken in code (function pointers built without a relocation)"""
        p = ops.split(', ')
        if len(p) != 3 or p[2][:1] != '#' or p[0][:1] != 'x':
            return
        pre = p[1] + ','
        n = len(H)
        for k in range(n - 2, max(-1, n - 10), -1):
            a, m2, o2 = H[k]
            if o2.startswith(pre) and not m2.startswith(('st', 'cb', 'tb', 'cmp')):
                if m2 == 'adrp':
                    t = imm_of(o2.split(', ')[1]) + imm_of(p[2])
                    if t < self.text_end:
                        k2 = 'adrp+add -> text: ' + ('FDE start' if t in self.starts else 'same function' if cur[0] <= t < cur[1] else 'other')
                        self.adr[k2] += 1
                        if t in self.starts: self.code_addr_taken.add(t)
                    else:
                        self.adr['adrp+add -> ' + self.where(t)] += 1
                return

    def classify_ptr(self, H, i, reg, what):
        """What an indirect branch's target register was computed from"""
        hops = 0
        j, d = find_def(H, i, reg)
        while d is not None and d[1] == 'mov' and hops < 3:
            src = d[2].split(', ')[1]
            if regnum(src) is None or not src.startswith('x'): break
            reg = regnum(src); j, d = find_def(H, j, reg); hops += 1
        if d is None:
            return 'no def in window: ' + j, None
        a, mn, ops = d
        if mn in ('ldr', 'ldur') and '[' in ops:
            base, imm, idx = mem_operand(ops)
            if idx is not None:
                return 'pointer from register-indexed table [xB, xI]', None
            if base == 'sp':
                return 'pointer reloaded from stack', None
            jb, db = find_def(H, j, base)
            if db is None:
                return 'ptr = [xB + off], xB defined earlier/elsewhere', imm
            bmn = db[1]
            if bmn in ('ldr', 'ldur') and '[' in db[2]:
                b2, imm2, idx2 = mem_operand(db[2])
                if idx2 is None and b2 != 'sp':
                    j2, d2 = find_def(H, jb, b2)
                    if d2 is not None and d2[1] in ('adrp', 'adr'):
                        return 'ptr from global object (adrp; ldr; ldr)', imm
                    return 'vtable-style: ldr vptr,[obj,#o]; ldr fn,[vptr,#slot]', imm
                if b2 == 'sp':
                    return 'vtable-style (object pointer from stack)', imm
                return 'ptr = [[table, idx] + off]', imm
            if bmn in ('adrp', 'adr') or (bmn == 'add' and addr_of(H, jb + 1, base) is not None):
                return 'global / GOT function pointer (adrp + ldr)', None
            return 'ptr = [xB + off], xB computed (' + bmn + ')', imm
        if mn in ('ldraa', 'ldrab'):
            return 'PAC-authenticated load (ldraa/ldrab)', None
        if mn in ('ldapr', 'ldar'):
            return 'pointer loaded with acquire (ldapr/ldar)', None
        if mn.startswith('aut'):
            return 'authenticated pointer (aut*)', None
        if mn in ('adr', 'adrp', 'add') and addr_of(H, i, reg) is not None:
            return 'address materialized in code (adr/adrp+add)', None
        if mn in ('ldp',) and '[' in ops:
            return 'pointer from ldp', None
        return 'other def: ' + mn, None

    def h_blr(self, addr, mn, ops, w, H, cur, in_fde, pend):
        Hl = list(H)
        if mn != 'blr':
            self.blr['PAC form: ' + mn] += 1
            return
        k, slot = self.classify_ptr(Hl, len(Hl) - 1, regnum(ops), 'blr')
        self.blr[k] += 1
        if k.startswith('vtable') and slot is not None:
            self.vt_slots[slot] += 1
        if len(self.examples['blr:' + k]) < 3: self.examples['blr:' + k].append(addr)

    def h_br(self, addr, mn, ops, w, H, cur, in_fde, pend):
        Hl = list(H)
        if mn != 'br':
            self.br['PAC form: ' + mn] += 1
            return
        reg = regnum(ops)
        # PLT stub: adrp x16, page; ldr x17, [x16, #off]; add x16, x16, #off; br x17
        if len(Hl) >= 4 and reg == 17 and Hl[-4][1] == 'adrp' and Hl[-4][2].startswith('x16') and Hl[-3][1] == 'ldr' and Hl[-3][2].startswith('x17, [x16') and Hl[-2][1] == 'add':
            self.br['PLT stub (adrp x16; ldr x17; add x16; br x17)'] += 1
            self.plt_stub_addrs.append(Hl[-4][0])
            return
        jt = self.jump_table(Hl, len(Hl) - 1, reg, cur)
        if jt:
            self.br['jump table: ' + jt] += 1
            return
        k, slot = self.classify_ptr(Hl, len(Hl) - 1, reg, 'br')
        self.br['other: ' + k] += 1
        if len(self.examples['br:' + k]) < 3: self.examples['br:' + k].append(addr)

    def jump_table(self, H, i, reg, cur):
        j, d = find_def(H, i, reg)
        if d is None or d[1] != 'add':
            if d is not None and d[1] in ('ldr',) and '[' in d[2]:
                b, imm, idx = mem_operand(d[2])
                if idx is not None:
                    return 'absolute pointers (ldr xT, [table, idx, lsl #3])'
            return None
        p = d[2].split(', ')
        if len(p) < 3 or p[2].startswith('#'):
            return None
        ra, rb = regnum(p[1]), regnum(p[2])
        shift = 0
        if len(p) == 4:
            m = re.search(r'#(\d+)', p[3]); shift = int(m.group(1)) if m else 0
        # find the load into rb (or ra) from a register-indexed table
        for load_reg, anchor_reg in ((rb, ra), (ra, rb)):
            jl, dl = find_def(H, j, load_reg)
            if dl is None or not dl[1].startswith('ldr') or '[' not in dl[2]:
                continue
            base, imm, idx = mem_operand(dl[2])
            if idx is None:
                continue
            esz = {'ldrb': 1, 'ldrh': 2, 'ldrsw': 4, 'ldr': 4 if dl[2].startswith('w') else 8, 'ldrsb': 1, 'ldrsh': 2}.get(dl[1])
            tbl = addr_of(H, jl, base)
            anchor = addr_of(H, j, anchor_reg)
            # bound: cmp on the index register (w or x) before the load, followed by b.hi / b.hs
            # bound: a 'cmp wR, #k' followed by b.hi (k+1 entries) or b.hs (k) before the load. Walking back from the load, 'mov' and masking
            # 'and #2^k-1' move the search to their source register (the and caps the bound), lsr #s / ubfx give a bound themselves, any other
            # write stops the walk. A guard on the chased register wins; otherwise the nearest guard on any register (the compiler often checks a
            # sibling copy of the index: and w8, w0, #0xff; cmp w8, #4; b.hi ... and x8, x21, #0xff with w21 = w0) is taken, capped by the mask
            n_mask, n_strong, n_weak, cur_idx, walking = None, None, None, idx, True
            for k in range(jl - 1, max(-1, jl - 32), -1):
                a, mn, ops = H[k]
                q = ops.split(', ')
                if mn == 'cmp' and len(q) == 2 and q[1].startswith('#') and k + 1 < len(H) and H[k + 1][1] in ('b.hi', 'b.hs'):
                    nc = imm_of(q[1]) + (1 if H[k + 1][1] == 'b.hi' else 0)
                    if walking and regnum(q[0]) == cur_idx:
                        n_strong = nc; break
                    if n_weak is None: n_weak = nc
                    continue
                if walking and cur_idx in dests(mn, ops):
                    if mn == 'mov' and len(q) == 2 and regnum(q[1]) is not None:
                        cur_idx = regnum(q[1]); continue
                    if mn == 'and' and len(q) == 3 and q[2].startswith('#') and imm_of(q[2]) & (imm_of(q[2]) + 1) == 0:
                        n1 = imm_of(q[2]) + 1; n_mask = n1 if n_mask is None else min(n_mask, n1)
                        cur_idx = regnum(q[1]); continue
                    if mn == 'lsr' and len(q) == 3 and q[2].startswith('#'):
                        n1 = 1 << ((32 if q[0][0] == 'w' else 64) - imm_of(q[2])); n_mask = n1 if n_mask is None else min(n_mask, n1)
                    elif mn == 'ubfx' and len(q) == 4:
                        n1 = 1 << imm_of(q[3]); n_mask = n1 if n_mask is None else min(n_mask, n1)
                    walking = False
            cands = [x for x in (n_mask, n_strong if n_strong is not None else n_weak) if x is not None]
            n = min(cands) if cands else None
            if n is not None and (n > 0x10000 or tbl is None or tbl + n * (esz or 1) > len(self.img)):
                n = None   # a range limit wider than any table (lsr of a 64-bit value) or a table past the image: bound not known
            kind = '%s entries (%d B), anchor %s, table in %s' % (dl[1], esz or 0, 'adr' if anchor is not None and anchor < self.text_end else 'table base' if anchor == tbl else 'unknown',
                                                                   self.where(tbl) if tbl is not None else '?')
            self.jt[kind] += 1
            if n is not None and tbl is not None and anchor is not None and esz:
                self.jt_entries.append(n)
                inside = 0
                ext = p[3] if len(p) == 4 else ''
                fmt = {1: '<B', 2: '<H', 4: '<i', 8: '<Q'}[esz]
                if dl[1] == 'ldrsb' or 'sxtb' in ext: fmt = '<b'
                if dl[1] == 'ldrsh' or 'sxth' in ext: fmt = '<h'
                for e in range(n):
                    v = struct.unpack_from(fmt, self.img, tbl + e * esz)[0]
                    t = anchor + (v << shift)
                    if cur[0] <= t < cur[1]: inside += 1
                    else:
                        self.jt_out[('FDE start' if t in self.starts else 'inside another FDE' if self.in_any_fde(t) else 'gap' if 0 <= t < self.text_end else 'outside text')] += 1
                if inside < n:
                    self.jt_target_check['tables with entries outside the function'] += 1
                    if len(self.jt_out_tables) < 40: self.jt_out_tables.append([addr_br(H), n, n - inside, tbl, anchor, dl[1]])
                self.jt_target_check['tables decoded'] += 1
                self.jt_target_check['entries'] += n
                self.jt_target_check['entries landing inside the function'] += inside
            else:
                self.jt_target_check['tables without decoded bound/base'] += 1
            return 'register-indexed load + add'
        return None

    def h_ret(self, addr, mn, ops, w, H, cur, in_fde, pend):
        if not in_fde: self.gap['ret'] += 1


def addr_br(H):
    return H[-1][0]


HANDLERS = {}
for m in ('mrs', 'msr'): HANDLERS[m] = Census.h_mrs_msr
for m in ('svc', 'hvc', 'smc', 'brk', 'hlt', 'udf', 'dcps1', 'dcps2', 'dcps3'): HANDLERS[m] = Census.h_svc
for m in ('dmb', 'dsb', 'isb', 'sb', 'ssbb', 'pssbb'): HANDLERS[m] = Census.h_barrier
for m in ('dc', 'ic', 'tlbi', 'at'): HANDLERS[m] = Census.h_cache
for m in ('sys', 'sysl'): HANDLERS[m] = Census.h_sys
HANDLERS['b'] = Census.h_b
HANDLERS['bl'] = Census.h_bl
for m in ('cbz', 'cbnz', 'tbz', 'tbnz'): HANDLERS[m] = Census.h_cb
for m in ('blr', 'blraa', 'blrab', 'blraaz', 'blrabz'): HANDLERS[m] = Census.h_blr
for m in ('br', 'braa', 'brab', 'braaz', 'brabz'): HANDLERS[m] = Census.h_br
for m in ('adr', 'adrp'): HANDLERS[m] = Census.h_adr
HANDLERS['add'] = Census.h_add
for m in ('ret', 'retaa', 'retab'): HANDLERS[m] = Census.h_ret


def main():
    exefs, scratch, name = sys.argv[1:4]
    mod = F.load_nso(os.path.join(exefs, name), name)
    info = json.load(open(os.path.join(scratch, f'fdes_{name}.json')))
    cz = Census(mod.image, info)
    cz._starts_sorted = [s for s, _ in cz.ranges]
    cz.code_addr_taken = set()
    cz.plt_stub_addrs = []
    # PLT region: PLT stubs are found during the sweep; a first cheap pass finds them by encoding so b/bl targets can be classified
    words = array('I'); words.frombytes(bytes(mod.image[:info['text_end'] & ~3]))
    plt = [i * 4 for i in range(len(words) - 3)
           if words[i] & 0x9F00001F == 0x90000010 and words[i + 1] & 0xFFC003FF == 0xF9400211 and words[i + 2] & 0xFFC003FF == 0x91000210 and words[i + 3] == 0xD61F0220]
    if plt:
        cz.plt_lo, cz.plt_hi = min(plt), max(plt) + 16
    cz.run()
    tail_fde = len(cz.tail_targets)
    called = set(cz.bl_targets)
    res = {
        'module': name, 'elapsed_s': cz.elapsed, 'text_end': info['text_end'],
        'plt_by_encoding': [cz.plt_lo, cz.plt_hi, len(plt)],
        'keys': [[mn, g, s, n] for (mn, g, s), n in cz.c.items()],
        'misc': dict(cz.misc), 'sysreg': [[a, b, n] for (a, b), n in cz.sysreg.most_common()],
        'svc': sorted(cz.svc.items()), 'brk': [[a, b, n] for (a, b), n in cz.brk.most_common()],
        'barrier': [[a, b, n] for (a, b), n in cz.barrier.most_common()], 'cache': [[a, b, n] for (a, b), n in cz.cache.most_common()],
        'br': cz.br.most_common(), 'blr': cz.blr.most_common(), 'vt_slots_top': cz.vt_slots.most_common(20),
        'jt_kinds': cz.jt.most_common(), 'jt_check': dict(cz.jt_target_check), 'jt_out': dict(cz.jt_out), 'jt_out_tables': cz.jt_out_tables,
        'jt_entries_hist': sorted(collections.Counter(min(n, 1000) if n < 1000 else 1000 for n in cz.jt_entries).items()),
        'tls': [[a, b, n] for (a, b), n in cz.tls.most_common()],
        'undecodable_total': sum(cz.undec.values()), 'undecodable_where': dict(cz.undec_where),
        'undecodable_top': [[hex(w), n] for w, n in cz.undec.most_common(40)], 'undecodable_distinct': len(cz.undec),
        'undecodable_words': sorted(cz.undec), 'undecodable_addrs_first': cz.undec_addrs[:400],
        'gap': {k: v for k, v in cz.gap.items() if not k.startswith('mn:')}, 'gap_mn_top': [[k[3:], v] for k, v in cz.gap.most_common() if k.startswith('mn:')][:40],
        'gap_bl_targets_distinct': len(cz.gap_bl_targets),
        'bl_targets': dict(cz.bl_tgt), 'bl_distinct_targets': len(called),
        'bl_distinct_fde_starts': len(called & cz.starts), 'fde_starts': len(cz.starts),
        'tail': dict(cz.b_tail), 'tail_distinct_fde_targets': tail_fde,
        'fde_starts_never_bl_or_tail': len(cz.starts - called - cz.tail_targets),
        'fde_starts_never_bl_tail_or_reloc': len(cz.starts - called - cz.tail_targets - set(info['rel_text']) - cz.code_addr_taken - set(info['exports_text'])),
        'code_addr_taken_fde_starts': len(cz.code_addr_taken),
        'indirect_target_candidates': len((set(info['rel_text']) | cz.code_addr_taken | set(info['exports_text'])) & cz.starts),
        'literal_loads': dict(cz.lit), 'adr': dict(cz.adr), 'ls_mode': dict(cz.ls_mode),
        'samples': {k: [hex(a) for a in v] for k, v in cz.samples.items()},
        'examples': {k: [hex(a) for a in v] for k, v in cz.examples.items()},
        'sites': {k: v for k, v in cz.sites.items()},
        'plt_stubs_seen': len(cz.plt_stub_addrs),
    }
    with open(os.path.join(scratch, f'insns_{name}.json'), 'w') as f:
        json.dump(res, f)
    total = sum(cz.c.values())
    print(f'{name}: {total} instructions decoded, {res["undecodable_total"]} undecodable words, {cz.elapsed:.1f} s')


if __name__ == '__main__':
    main()
