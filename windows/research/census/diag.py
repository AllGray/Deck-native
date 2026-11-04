"""Census part 3: diagnostics on what the counters cannot show by themselves.
Usage: py -3 -I diag.py <ExeFS dir> <scratch dir> <module>
  - code in FDE gaps (functions without unwind info): per gap, real instructions vs padding, rets, incoming BL targets
  - UDF placement (inside FDE ranges = deliberate traps, in gaps = padding)
  - relocation targets in text that are not FDE starts
  - TLS access examples (disassembled, printed only to the console)
  - jump tables whose decoded entries leave the function
Writes <scratch>/diag_<module>.json."""
import bisect, collections, json, os, re, struct, sys
from array import array

import capstone

sys.dont_write_bytecode = True   # keep this folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nsofmt as F


def main():
    exefs, scratch, name = sys.argv[1:4]
    mod = F.load_nso(os.path.join(exefs, name), name)
    img = mod.image
    info = json.load(open(os.path.join(scratch, f'fdes_{name}.json')))
    ins = json.load(open(os.path.join(scratch, f'insns_{name}.json')))
    text_end = info['text_end']
    ranges = [tuple(r) for r in info['ranges']]
    starts_sorted = [s for s, _ in ranges]
    starts = set(starts_sorted)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    words = array('I'); words.frombytes(bytes(img[:text_end & ~3]))

    def fde_of(a):
        i = bisect.bisect_right(starts_sorted, a) - 1
        return ranges[i] if i >= 0 and ranges[i][0] <= a < ranges[i][1] else None

    gaps, last = [], 0
    for s, e in ranges:
        if s > last: gaps.append((last, s))
        last = max(last, e)
    if last < text_end: gaps.append((last, text_end))

    # BL targets by raw encoding (BL = 100101 imm26)
    bl_into = collections.Counter()
    for i, w in enumerate(words):
        if w & 0xFC000000 == 0x94000000:
            off = (w & 0x3FFFFFF); off = off - (1 << 26) if off & (1 << 25) else off
            t = i * 4 + off * 4
            if 0 <= t < text_end and fde_of(t) is None:
                bl_into[t] += 1
    plt = ins['plt_by_encoding']
    out = {'gaps': [], 'gap_summary': collections.Counter()}
    for gs, ge in gaps:
        real = rets = pac = udf = zero = 0
        for a in range(gs, ge, 4):
            w = words[a >> 2]
            if w == 0: zero += 1; continue
            r = next(md.disasm_lite(bytes(img[a:a + 4]), a), None)
            if r is None: continue
            mn = r[2]
            if mn == 'udf': udf += 1; continue
            real += 1
            if mn in ('ret', 'retab', 'retaa'): rets += 1
            if mn in ('pacibsp', 'paciasp'): pac += 1
        is_plt = plt[0] is not None and gs <= plt[0] < ge
        tg = sum(1 for t in bl_into if gs <= t < ge)
        k = 'PLT' if is_plt else 'padding only' if real == 0 else 'code'
        out['gap_summary'][k + ' gaps'] += 1
        out['gap_summary'][k + ' bytes'] += ge - gs
        out['gap_summary'][k + ' real instructions'] += real
        if real >= 16 or is_plt:
            out['gaps'].append({'start': hex(gs), 'size': ge - gs, 'real_insns': real, 'rets': rets, 'pac_prologues': pac,
                                'udf': udf, 'zero': zero, 'bl_targets_in_gap': tg, 'plt': is_plt})
    out['gap_summary'] = dict(out['gap_summary'])
    # small code gaps: what the typical short gap holds
    small = collections.Counter()
    for gs, ge in gaps:
        if ge - gs <= 64:
            seq = []
            for a in range(gs, ge, 4):
                r = next(md.disasm_lite(bytes(img[a:a + 4]), a), None)
                seq.append(r[2] if r else '??')
            small[' '.join(sorted(set(seq)))] += 1
    out['small_gap_contents'] = small.most_common(10)
    # UDF placement
    udf = collections.Counter()
    for a in ins['sites'].get('udf', []):
        w = words[a >> 2]
        udf[('in FDE' if fde_of(a) else 'gap', '#%#x' % (w & 0xFFFF))] += 1
    out['udf'] = [[a, b, n] for (a, b), n in udf.most_common()]
    # what precedes an in-FDE udf
    prev = collections.Counter()
    for a in ins['sites'].get('udf', []):
        if fde_of(a) and words[a >> 2] & 0xFFFF:
            r = next(md.disasm_lite(bytes(img[a - 4:a]), a - 4), None)
            prev[r[2] if r else '??'] += 1
    out['udf_nonzero_in_fde_prev'] = prev.most_common(8)
    # in-FDE udf #0: is it referenced as a branch target / after a call?
    prev0 = collections.Counter()
    for a in ins['sites'].get('udf', []):
        if fde_of(a) and words[a >> 2] == 0:
            r = next(md.disasm_lite(bytes(img[a - 4:a]), a - 4), None)
            prev0[r[2] if r else '??'] += 1
    out['udf0_in_fde_prev'] = prev0.most_common(8)
    # relocation targets in text that are not FDE starts
    rt = collections.Counter()
    for a in info['rel_text']:
        if a in starts: continue
        f = fde_of(a)
        rt['inside an FDE' if f else 'in a gap (code without FDE)' if a < text_end else 'beyond text'] += 1
    out['rel_text_not_fde_start'] = dict(rt)
    out['rel_text_into_gap_code'] = sum(1 for a in info['rel_text'] if a not in starts and not fde_of(a))
    # exclusive monitor usage shape: ldxr/ldaxr followed by stxr/stlxr within 16 instructions
    out['jt_out_tables_disasm'] = []
    for t in ins.get('jt_out_tables', [])[:2]:
        a = t[0]
        f = fde_of(a)
        out['jt_out_tables_disasm'].append({'br': hex(a), 'fde': [hex(f[0]), hex(f[1])] if f else None, 'n': t[1], 'outside': t[2]})
    with open(os.path.join(scratch, f'diag_{name}.json'), 'w') as fo:
        json.dump(out, fo, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != 'gaps'}, indent=1))
    print('code gaps (>=16 real insns):', len(out['gaps']))
    for g in sorted(out['gaps'], key=lambda g: -g['real_insns'])[:15]:
        print('  ', g)
    # console-only disassembly samples
    def dis(a, n):
        return ['%#x: %s %s' % (x[0], x[2], x[3]) for x in md.disasm_lite(bytes(img[a:a + 4 * n]), a)]
    for key in ('mrs tpidr_el0', 'mrs tpidrro_el0'):
        for a in ins['sites'].get(key, [])[:4]:
            print('--', key); print('\n'.join(dis(a - 8, 8)))
    for t in ins.get('jt_out_tables', [])[:1]:
        print('-- jt out', hex(t[0])); print('\n'.join(dis(t[0] - 4 * 24, 26)))


if __name__ == '__main__':
    main()
