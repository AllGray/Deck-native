#!/usr/bin/env python3
"""swapin_bisect.py <tag> --list FULL [--lib DYLIB] --fail CRITERION [--until X] [--det] [--trace] [--repeat K]: bisects a
swap-in divergence down to one function (docs/m4-swapin.md). Every test is a full run of swapin_drive.py with a subset of FULL
swapped in and everything else native; FULL itself must fail and the empty set must not (checked first).

CRITERION (a run "fails" when it holds):
  crash                 the run ended early: an exit, a trap in lifted code, a hang, a missing checkpoint
  log:<regex>           host.log has a line matching regex          nolog:<regex>   ... has none
  entry:<module>+<hex>  the function at that address ran (needs --trace)
  noentry:<module>+<hex> it did not run (needs --trace)
  before:<A>:<B>        A's first entry came before B's (A, B as module+hex): an order the native runs do not have
  shot:<name>:<limit>:<reference tag>   checkpoint <name>'s screenshot differs from the reference run's by more than limit (mean
                        absolute difference, 0-255, at 480x270)

The search splits the candidates in halves; when neither half fails alone (two functions that only diverge together), it
keeps one half swapped in while it searches the other (the classic "fixed set" refinement). The log is <runs>/<tag>.bisect.tsv."""
import argparse, json, pathlib, re, struct, subprocess, sys, time

H = pathlib.Path.home()
RUNS = H / 'swapin/runs'
DRIVE = pathlib.Path(__file__).with_name('swapin_drive.py')
BASES = {'main': 0x7000000000, 'subsdk0': 0x7005000000, 'sdk': 0x7006000000}


def addr_of(s):
    m, o = s.split('+')
    return BASES[m] + int(o, 16)


class Bisect:
    def __init__(s, a):
        s.a = a
        s.n = 0
        s.log = open(RUNS / f'{a.tag}.bisect.tsv', 'a')
        s.log.write(f'# {time.ctime()} {" ".join(sys.argv[1:])}\n')

    def run(s, fns):
        s.n += 1
        tag = f'{s.a.tag}-b{s.n:03d}'
        lst = RUNS / f'{tag}.list'
        RUNS.mkdir(parents=True, exist_ok=True)
        lst.write_text(''.join(f'{f}\n' for f in fns) or '# none\n')
        failed = False
        for k in range(s.a.repeat):
            cmd = [sys.executable, str(DRIVE), 'run', tag, '--list', str(lst), '--lib', s.a.lib, '--until', s.a.until]
            if s.a.det: cmd.append('--det')
            if s.a.trace or s.a.fail.startswith(('entry:', 'noentry:', 'before:')): cmd.append('--trace')
            if s.a.boot is not None: cmd += ['--boot', str(s.a.boot)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if s.failing(RUNS / tag):
                failed = True
                break
        s.log.write(f'{tag}\t{len(fns)}\t{"FAIL" if failed else "ok"}\t{fns[0] if fns else ""}..{fns[-1] if fns else ""}\n')
        s.log.flush()
        print(f'[{s.n}] {len(fns)} functions: {"FAIL" if failed else "ok"}', flush=True)
        return failed

    def failing(s, rd):
        c = s.a.fail
        try:
            r = json.loads((rd / 'result.json').read_text())
        except OSError:
            return True
        if c == 'crash':
            return r['ended'] != 'done' or r.get('crash', False)
        text = (rd / 'host.log').read_text(errors='replace')
        if c.startswith('log:'):
            return re.search(c[4:], text, re.M) is not None
        if c.startswith('nolog:'):
            return re.search(c[6:], text, re.M) is None
        if c.startswith(('entry:', 'noentry:', 'before:')):
            d = (rd / 'trace/cov_log.bin').read_bytes() if (rd / 'trace/cov_log.bin').exists() else b''
            order = [struct.unpack_from('<Q', d, i + 8)[0] for i in range(0, len(d) - 15, 16)]
            if c.startswith('before:'):   # before:A:B fails when A's first entry comes before B's (or B never comes)
                a, b = (addr_of(x) for x in c.split(':')[1:3])
                ia = order.index(a) if a in order else None
                ib = order.index(b) if b in order else None
                return ia is not None and (ib is None or ia < ib)
            want = addr_of(c.split(':', 1)[1])
            return (want in order) if c.startswith('entry:') else want not in order
        if c.startswith('shot:'):
            _, name, limit, ref = c.split(':')
            sys.path.insert(0, str(DRIVE.parent))
            from swapin_drive import shot_diff
            d = shot_diff(rd / f'{name}.png', RUNS / ref / f'{name}.png')
            return d is None or d > float(limit)
        sys.exit(f'unknown criterion {c}')

    # the smallest set of candidates that fails together with `fixed`
    def search(s, fixed, cands):
        while len(cands) > 1:
            h = len(cands) // 2
            a, b = cands[:h], cands[h:]
            if s.run(fixed + a):
                cands = a
            elif s.run(fixed + b):
                cands = b
            else:   # only together: one culprit in each half
                ca = s.search(fixed + b, a)
                cb = s.search(fixed + ca, b)
                return ca + cb
        return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tag')
    ap.add_argument('--list', required=True)
    ap.add_argument('--lib', default=str(H / 'swapin/work/all/swapin.dylib'))
    ap.add_argument('--fail', required=True)
    ap.add_argument('--until', default='play')
    ap.add_argument('--det', action='store_true')
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--repeat', type=int, default=1)
    ap.add_argument('--boot', type=float, help='seconds before the title checkpoint (with --until title: a short run)')
    ap.add_argument('--skip-checks', action='store_true')
    a = ap.parse_args()
    fns = [ln.split('#')[0].strip() for ln in open(a.list)]
    fns = [f for f in fns if f]
    b = Bisect(a)
    if not a.skip_checks:
        if b.run([]):
            sys.exit('the native run already fails the criterion: nothing to bisect')
        if not b.run(fns):
            sys.exit('the full list does not fail the criterion')
    found = b.search([], fns)
    b.log.write(f'# culprit(s): {" ".join(found)} after {b.n} runs\n')
    print('culprit(s):', ' '.join(found), f'({b.n} runs)')


if __name__ == '__main__':
    main()
