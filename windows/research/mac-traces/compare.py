"""Executed FDE starts across traced sessions: per run, shared by all, and the union (analyze.py writes executed_<module>.txt).
Usage: py -3 -I compare.py <run dir> <run dir> ..."""
import os, sys

runs = sys.argv[1:]
print('| module | ' + ' | '.join(os.path.basename(os.path.abspath(r)) for r in runs) + ' | in all | union |')
print('|---|' + '---:|' * (len(runs) + 2))
for mod in ('main', 'sdk', 'subsdk0'):
    sets = []
    for r in runs:
        p = os.path.join(r, f'executed_{mod}.txt')
        sets.append(set(int(l.split('\t')[0], 16) for l in open(p)) if os.path.exists(p) else set())
    if not any(sets):
        continue
    print(f'| {mod} | ' + ' | '.join(f'{len(s):,}' for s in sets) + f' | {len(set.intersection(*sets)):,} | {len(set.union(*sets)):,} |')
