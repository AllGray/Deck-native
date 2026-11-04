"""title_compare.py <mac run dir> <windows log> [--mac-also <run dir>]... [--title-from <ms>] [--title-to <ms>]
M4's exit criterion against the Mac (docs/m4-title.md), from one Windows run of nxrt (NXRT_TRACE=first:N and DM_FS=1) and one Mac run of
titledrive.py (research/mac-traces; nxnative-trace's coverage, analysed by analyze.py into executed_<module>.txt, and DM_FS=1):
  1. functions: the Mac's executed function starts against Windows' first entries (<log>.trace.txt), per module; the title scene's set is what the
     Mac first ran in [--title-from, --title-to) ms (default 18000-40000: after the loading screens, before any input);
  2. game files: the RomFS files opened (fs open lines), as distinct files in order of first open, and the number of opens of each;
  3. presents: the [nvn] present-count lines and the frame-time report of the Windows log.
Windows functions the Mac never ran are checked against every --mac-also run too (another route or save)."""
import collections, os, re, sys

args = sys.argv[1:]
mac, win = args[0], args[1]
also, t_from, t_to = [], 18000, 40000
i = 2
while i < len(args):
    if args[i] == '--mac-also': also.append(args[i + 1]); i += 2
    elif args[i] == '--title-from': t_from = int(args[i + 1]); i += 2
    elif args[i] == '--title-to': t_to = int(args[i + 1]); i += 2
    else: sys.exit(f'unknown option {args[i]}')
MODS = ('main', 'sdk', 'subsdk0')


def mac_exec(run):
    out = {}
    for m in MODS:
        p = os.path.join(run, f'executed_{m}.txt')
        out[m] = {int(l.split()[0], 16): int(l.split()[1]) for l in open(p) if l.strip()} if os.path.exists(p) else {}
    return out


def win_exec(log):
    out = {m: {} for m in MODS}
    for l in open(log + '.trace.txt', encoding='utf-8', errors='replace'):
        if not l.startswith('F'):
            continue
        m = re.match(r'F\d+ t\d+ (\w+)\+0x([0-9a-f]+)', l)
        if m and m.group(1) in out:
            t = re.search(r' ms=(\d+)', l)
            out[m.group(1)].setdefault(int(m.group(2), 16), int(t.group(1)) if t else -1)
    return out


def opens(path):
    seq = []
    for l in open(path, encoding='utf-8', errors='replace'):
        m = re.search(r'fs open \[([0-9.]+)s\] (rom:/\S+)', l)
        if m:
            seq.append((float(m.group(1)), m.group(2)))
    return seq


M, W = mac_exec(mac), win_exec(win)
A = [mac_exec(r) for r in also]
print(f'# Mac {mac} vs Windows {win}\n')
print('## Functions\n')
print('| module | Mac ran | Windows ran | both | Mac only | Windows only | Windows only, in no Mac run |')
print('|---|---:|---:|---:|---:|---:|---:|')
for m in MODS:
    mk, wk = set(M[m]), set(W[m])
    allmac = mk.union(*(set(a[m]) for a in A))
    print(f'| {m} | {len(mk):,} | {len(wk):,} | {len(mk & wk):,} | {len(mk - wk):,} | {len(wk - mk):,} | {len(wk - allmac):,} |')
title = {o for o, t in M['main'].items() if t_from <= t < t_to}
boot = {o for o, t in M['main'].items() if t < t_from}
print(f'\nmain, by when the Mac first ran it: boot and loading (before {t_from / 1000:.0f} s) {len(boot):,}, of them on Windows {len(boot & set(W["main"])):,}; '
      f'title scene ({t_from / 1000:.0f}-{t_to / 1000:.0f} s) {len(title):,}, of them on Windows {len(title & set(W["main"])):,} '
      f'({100 * len(title & set(W["main"])) / max(1, len(title)):.2f} %)')
miss = sorted(title - set(W['main']), key=lambda o: M['main'][o])
if miss:
    print(f'\nTitle-scene functions Windows did not run (first {min(40, len(miss))}, by the Mac\'s time): ' +
          ', '.join(f'main+{o:#x} ({M["main"][o] / 1000:.1f} s)' for o in miss[:40]))
for m in MODS:
    only = sorted(set(W[m]) - set(M[m]).union(*(set(a[m]) for a in A)))
    if only:
        print(f'\n{m}: Windows ran {len(only)} functions no Mac run ran: ' + ', '.join(f'{m}+{o:#x}' for o in only[:40]))

print('\n## Game files (RomFS)\n')
mo, wo = opens(os.path.join(mac, 'host.log')), opens(win)
if not mo:
    print('The Mac run has no fs open lines (DM_FS=1 was not set).')
else:
    def firsts(seq):
        seen, out = set(), []
        for _, p in seq:
            if p not in seen:
                seen.add(p); out.append(p)
        return out
    mf, wf = firsts(mo), firsts(wo)
    mc, wc = collections.Counter(p for _, p in mo), collections.Counter(p for _, p in wo)
    print(f'Mac: {len(mo):,} opens of {len(mf)} files, last at {mo[-1][0]:.2f} s; Windows: {len(wo):,} opens of {len(wf)} files, last at {wo[-1][0]:.2f} s')
    print(f'Distinct files in order of first open: {"identical" if mf == wf else "different"}')
    if mf != wf:
        for k in range(max(len(mf), len(wf))):
            a, b = mf[k] if k < len(mf) else '-', wf[k] if k < len(wf) else '-'
            print(f'  {k:3} {"  " if a == b else "!="} Mac {a}   Windows {b}')
    diffc = sorted(p for p in set(mc) | set(wc) if mc[p] != wc[p])
    print(f'Opens per file: {"identical" if not diffc else f"{len(diffc)} files differ"}')
    print('\n| file | Mac opens | Windows opens |\n|---|---:|---:|')
    for p in mf + [p for p in wf if p not in mf]:
        print(f'| {p} | {mc[p]} | {wc[p]} |')

print('\n## Presents (Windows)\n')
for l in open(win, encoding='utf-8', errors='replace'):
    if re.search(r'\[nvn\] (\d+ presents|frame pacing|first frame request)', l):
        print('    ' + l.rstrip())
