"""Census part 5: compare the census's SVC / system-register / x18 sites with nxnative's rewriter output for the same build
(<patched>/<module>.sites.tsv, .gates.tsv, .changed.tsv). Only the .tsv tables are read.
Usage: py -3 -I crosscheck.py <patched dir> <scratch dir>
Writes <scratch>/crosscheck.json."""
import collections, json, os, sys

KIND_OF = {   # census site key -> rewriter sites.tsv kind
    'mrs tpidr_el0': 'tpidr_r', 'msr tpidr_el0': 'tpidr_w', 'mrs tpidrro_el0': 'tpidrro_r', 'mrs cntpct_el0': 'cntpct', 'mrs cntvct_el0': 'cntvct',
    'mrs ctr_el0': 'ctr', 'svc': 'svc', 'x18': 'x18'}


def read_tsv(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        rows.append(line.rstrip('\n').split('\t'))
    return rows


def main():
    patched, scratch = sys.argv[1:3]
    out = {}
    for name in ('main', 'sdk', 'subsdk0'):
        ins = json.load(open(os.path.join(scratch, f'insns_{name}.json')))
        sites = ins['sites']
        tsv_sites = collections.defaultdict(set)
        other = collections.Counter()
        for r in read_tsv(os.path.join(patched, f'{name}.sites.tsv')):
            if r[1] in ('W', 'J', 'T', 'S'):
                other[r[1]] += 1
            else:
                tsv_sites[r[1]].add(int(r[0], 16))
        gates = read_tsv(os.path.join(patched, f'{name}.gates.tsv'))
        gate_kinds = collections.Counter(g[1] for g in gates)
        gate_orig = collections.defaultdict(set)
        gate_svc_num = {}
        for ret, k, arg, orig in gates:
            gate_orig[k].add(int(orig, 16))
            if k == 'svc': gate_svc_num[int(orig, 16)] = int(arg)
        changed = read_tsv(os.path.join(patched, f'{name}.changed.tsv'))
        changed_words = sum(int(r[1], 16) // 4 for r in changed)
        res = {'tsv_other_rows': dict(other), 'gate_kinds': dict(gate_kinds), 'changed_rows': len(changed), 'changed_words': changed_words, 'kinds': {}}
        for ck, tk in KIND_OF.items():
            mine = set(sites.get(ck, []))
            theirs = tsv_sites.get(tk, set())
            if tk in ('svc', 'cntpct', 'cntvct'):
                theirs = theirs | gate_orig.get(tk, set())   # gate sites carry the original site address
            if not mine and not theirs:
                continue
            res['kinds'][ck] = {'census': len(mine), 'rewriter': len(theirs), 'both': len(mine & theirs),
                                'census_only': [hex(a) for a in sorted(mine - theirs)][:20], 'rewriter_only': [hex(a) for a in sorted(theirs - mine)][:20]}
        # svc numbers: census encoding vs rewriter gate argument
        census_svc = collections.Counter()
        for a in sites.get('svc', []):
            census_svc[a] = None
        res['svc_numbers_rewriter'] = sorted(set(gate_svc_num.values()))
        res['svc_numbers_census'] = [n for n, _ in ins['svc']]
        # system registers the census sees that the rewriter does not rewrite
        handled = {'tpidr_el0', 'tpidrro_el0', 'cntpct_el0', 'cntvct_el0', 'ctr_el0'}
        res['sysregs_not_rewritten'] = [[a, b, n] for a, b, n in ins['sysreg'] if b not in handled]
        res['cache_ops'] = ins['cache']
        out[name] = res
    with open(os.path.join(scratch, 'crosscheck.json'), 'w') as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
