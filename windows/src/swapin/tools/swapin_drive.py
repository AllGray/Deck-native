#!/usr/bin/env python3
"""swapin_drive.py: runs Pokopia 2.0.1 under the swap-in host on the Mac (docs/m4-swapin.md), native or with a chosen set of
functions swapped in, through scripted checkpoints, and compares runs.

  run <tag> [--list FILE] [--lib DYLIB] [--until title|loaded|menus|play] [--trace] [--det] [--bare] [--boot S] [--env K=V]...
      ~/swapin/runs/<tag>/: host.log (+ host.tlog: each line with its arrival second), title.png / loaded.png / menus.png /
      play.png, phases.tsv, result.json (how far it got, how it ended), trace/ with --trace (first entries: coverage traps on
      every function start, and the lifted functions' own first entries). Without --list the run is native: the same host,
      module bases and dylib, with nothing swapped in (--bare: no dylib at all). --det plays the keys and takes the screenshots
      at fixed frame numbers instead of wall-clock seconds.
  compare <tag> <reference tag>: the log (timestamps and addresses masked), the screenshots and the first-entry traces.

Built from ~/setup/menudrive.py and research/mac-traces/tracedrive.py (the same save copy, keys and route). The build folder is
used read-only. Game data stays on the Mac."""
import json, os, pathlib, re, shutil, struct, subprocess, sys, time

H = pathlib.Path.home()
B = H / 'installer-test/build201-clean'
HOST = H / 'nxnative-swapin/dm/host/dmhost'
RUNS = H / 'swapin/runs'
BASES = 'main=7000000000,subsdk0=7005000000,sdk=7006000000'   # = build_dylib.sh
FDE = H / 'swapin/data/fde'
ROUTE = ['phase:menus', 'X', 'wait:1', 'Right', 'Right', 'Down', 'wait:1', 'B', 'wait:1.5', 'Plus', 'wait:2', 'Right', 'wait:1', 'Right',
         'wait:1', 'Down', 'wait:1', 'B', 'wait:1', 'B', 'wait:2', 'Minus', 'wait:2', 'B', 'wait:2', 'shot:menus',
         'phase:play', 'LUp~3', 'LLeft~2', 'LRight~2', 'LDown~2', 'B', 'LUp~3', 'ZR', 'wait:1', 'ZR', 'wait:1', 'ctl:look 90 0 0.5', 'wait:1',
         'ctl:look -45 10 0.3', 'wait:1', 'A', 'wait:2', 'A', 'wait:2', 'B', 'wait:1', 'LUp~2', 'ZL~1', 'LRight~3', 'Y', 'wait:1', 'B', 'wait:1',
         'shot:play']   # research/05's route a, shortened

# --det: the same session in presented frames (DM_KEYS takes frame-numbered steps, DM_SWAPIN_SHOTS takes the screenshots at exact
# frames). Frame 1 is the first present (about 28 s in). nxnative's DM_DETCLOCK cannot be used: Pokopia waits on the clock between
# presents (its boot and its frame limiter), and a clock that only presents advance never lets it go on.
DET_TITLE, DET_A0, DET_LOADED = 720, 780, 3200


def det_script(until):
    keys, shots = [], {'title': DET_TITLE}
    for i in range(8):
        keys.append(f'f{DET_A0 + 240 * i}:A~f9')
    if until == 'title':
        return keys[:0], shots
    shots['loaded'] = DET_LOADED
    f = DET_LOADED + 30
    for st in ROUTE:
        if st.startswith('wait:'):
            f += round(float(st[5:]) * 60)
        elif st.startswith('shot:'):
            shots[st[5:]] = f
            if st[5:] == until:
                break
        elif st.startswith('ctl:look'):   # a camera turn: the right stick, as long as the turn's seconds
            _, dx, dy, sec = st.split()
            x, y = max(-1.0, min(1.0, float(dx) / 90)), max(-1.0, min(1.0, float(dy) / 45))
            keys.append(f'f{f}:R{x:g}/{y:g}~f{round(float(sec) * 60)}')
            f += round(float(sec) * 60) + 60
        elif not st.startswith('phase:'):
            hold = float(st.split('~', 1)[1]) if '~' in st else 0.15
            keys.append(f'f{f}:{st.split("~")[0]}~f{max(1, round(hold * 60))}')
            f += round((hold + 0.85) * 60)
    return keys, shots


class Run:
    def __init__(s, tag, a):
        s.rd = RUNS / tag
        shutil.rmtree(s.rd, ignore_errors=True)
        s.rd.mkdir(parents=True)
        shutil.copytree(H / 'setup/save_run4/save', s.rd / 'save')
        (s.rd / 'temp').mkdir()
        s.ctl = s.rd / 'ctl.txt'
        s.ctl.write_text('')
        env = dict(os.environ, DM_ROOT=str(B), DM_ROMFS=str(B / 'extract/RomFS.img'), DM_SAVE=str(s.rd / 'save'), DM_TEMP=str(s.rd / 'temp'),
                   DM_MSL_DIR=str(B / 'shader/cache'), DM_FONT_DIR=str(B / 'font'), DM_NVN_SHMAP=str(B / 'profile/0400A3A01EE4C000/shader_map.tsv'),
                   DM_LANG='en-US', DM_AUDIO='0', DM_CTL=str(s.ctl), DM_SWAPIN_BASES=BASES, DM_FIXED_TIME='1793700000')
        if not a.bare:   # a native run loads the dylib too, with nothing swapped: the host's own memory then lays out the same
            lst = pathlib.Path(a.list).resolve() if a.list else s.rd / 'none.list'
            if not a.list:
                lst.write_text('# native: nothing swapped in\n')
            env.update(DM_SWAPIN_LIB=str(pathlib.Path(a.lib).resolve()), DM_SWAPIN_LIST=str(lst), DM_SWAPIN_APPLIED=str(s.rd / 'applied.txt'))
        if a.trace:
            (s.rd / 'trace').mkdir()
            env.update(DM_TRACE=str(s.rd / 'trace'), DM_TRACE_COV=str(FDE), DM_TRACE_COV_ENTRIES='1', DM_TRACE_IND='0')
        if a.stats:
            env['DM_SWAPIN_STATS'] = str(s.rd / 'native_calls.tsv')
        s.det_shots = {}
        if a.det:
            keys, s.det_shots = det_script(a.until)
            env.update(DM_KEYS=','.join(keys) or 'f999999:A',
                       DM_SWAPIN_SHOTS=','.join(f'{f}:{s.rd / n}.png' for n, f in s.det_shots.items()))
        for kv in a.env:
            k, v = kv.split('=', 1)
            env[k] = v
        s.ph = open(s.rd / 'phases.tsv', 'w')
        s.ph.write('second\tevent\n')
        s.log = open(s.rd / 'host.log', 'w')
        s.t0 = time.time()
        s.p = subprocess.Popen([str(HOST), str(B / 'patched')], env=env, cwd=s.rd, stdout=s.log, stderr=subprocess.STDOUT)
        s.tlog = open(s.rd / 'host.tlog', 'w')
        s.seen = 0
        s.frames = 0
        s.last_progress = time.time()
        s.result = {'tag': tag, 'list': a.list, 'checkpoints': [], 'ended': None}
        s.mark('start')

    def mark(s, what):
        s.ph.write(f'{time.time() - s.t0:.2f}\t{what}\n')
        s.ph.flush()

    def say(s, line):
        with open(s.ctl, 'a') as f:
            f.write(line + '\n')

    def poll(s):   # new log lines, with their arrival time; frame progress from the 300-frame reports
        try:
            text = (s.rd / 'host.log').read_text(errors='replace')
        except OSError:
            return
        lines = text.split('\n')[:-1]
        for ln in lines[s.seen:]:
            s.tlog.write(f'{time.time() - s.t0:.2f}\t{ln}\n')
            m = re.search(r'timing up to frame (\d+)', ln)
            if m:
                s.frames = int(m.group(1))
            s.last_progress = time.time()
        s.tlog.flush()
        s.seen = len(lines)

    def alive(s):
        s.poll()
        return s.p.poll() is None

    def sleep(s, sec):
        end = time.time() + sec
        while time.time() < end:
            if not s.alive():
                return False
            time.sleep(min(0.25, max(0, end - time.time())))
        return True

    def shot(s, name):
        f = s.rd / f'{name}.png'
        s.say(f'shot {f}')
        for _ in range(80):
            if f.exists() and f.stat().st_size:
                break
            if not s.sleep(0.25):
                break
        ok = f.exists() and f.stat().st_size > 0
        s.mark(f'shot {name} {"ok" if ok else "none"}')
        if ok:
            s.result['checkpoints'].append(name)
        return ok

    def stop(s, why):
        s.mark(f'stop: {why}')
        if s.result['ended'] is None:
            s.result['ended'] = why
        if s.p.poll() is None:
            if (s.rd / 'trace').exists():
                (s.rd / 'trace/flush').touch()
                for _ in range(120):
                    if (s.rd / 'trace/flushed').exists():
                        break
                    time.sleep(0.25)
            s.p.terminate()
            try:
                s.p.wait(10)
            except subprocess.TimeoutExpired:
                s.p.kill()
        else:
            s.result['exit'] = s.p.returncode
        s.poll()
        text = (s.rd / 'host.log').read_text(errors='replace')
        s.result['traps'] = [ln for ln in text.split('\n') if 'TRAP in lifted code' in ln or 'FATAL' in ln][:5]
        s.result['crash'] = bool(re.search(r'FATAL|TRAP in lifted|signal \d+|Segmentation|crash report', text))
        s.result['frames'] = s.frames
        s.result['seconds'] = round(time.time() - s.t0, 1)
        for ln in text.split('\n'):
            m = re.search(r'swapin: lifted for main \S+: (\d+) functions in the dylib, (\d+) swapped in', ln)
            if m:
                s.result['swapped'] = int(m.group(2))
        (s.rd / 'result.json').write_text(json.dumps(s.result, indent=1) + '\n')
        print(json.dumps(s.result))


def cmd_run(argv):
    import argparse
    ap = argparse.ArgumentParser(prog='swapin_drive.py run')
    ap.add_argument('tag')
    ap.add_argument('--list')
    ap.add_argument('--lib', default=str(H / 'swapin/work/all/swapin.dylib'))
    ap.add_argument('--until', default='play', choices=['title', 'loaded', 'menus', 'play'])
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--boot', type=float, default=40.0)
    ap.add_argument('--title', type=int, default=8)
    ap.add_argument('--env', action='append', default=[])
    ap.add_argument('--det', action='store_true', help='frame-numbered keys and frame-exact screenshots')
    ap.add_argument('--bare', action='store_true', help='native without loading the dylib at all')
    ap.add_argument('--stats', action='store_true', help='native_calls.tsv: the native calls lifted code makes, by target')
    ap.add_argument('--timeout', type=float, default=600.0)
    a = ap.parse_args(argv)
    r = Run(a.tag, a)
    try:
        if a.det:   # the host plays the keys and takes the shots by itself: wait for the last shot (or an exit, or no progress)
            end = time.time() + a.timeout
            last = max(r.det_shots, key=r.det_shots.get)
            while time.time() < end:
                for n in r.det_shots:
                    f = r.rd / f'{n}.png'
                    if n not in r.result['checkpoints'] and f.exists() and f.stat().st_size:
                        time.sleep(0.5)
                        r.result['checkpoints'].append(n)
                        r.mark(f'shot {n} ok')
                if last in r.result['checkpoints']:
                    return r.stop('done')
                if not r.sleep(0.5):
                    return r.stop('exited before ' + ', '.join(n for n in r.det_shots if n not in r.result['checkpoints']))
                if time.time() - r.last_progress > 90:
                    return r.stop('no log progress for 90 s (hang?)')
            return r.stop('timed out')
        if not r.sleep(a.boot):
            return r.stop('exited during boot')
        r.mark('phase title')
        r.shot('title')
        if a.until == 'title':
            return r.stop('done')
        for i in range(a.title):
            r.say('A')
            r.mark('A')
            if not r.sleep(4):
                return r.stop('exited at the title screen')
        if not r.sleep(8):
            return r.stop('exited loading the save')
        r.mark('phase loaded')
        r.shot('loaded')
        if a.until == 'loaded':
            return r.stop('done')
        for st in ROUTE:
            if not r.alive():
                return r.stop(f'exited before {st}')
            if st.startswith('wait:'):
                r.sleep(float(st[5:]))
            elif st.startswith('shot:'):
                r.shot(st[5:])
                if st[5:] == a.until:
                    return r.stop('done')
            elif st.startswith('ctl:'):
                r.say(st[4:]); r.mark(st); r.sleep(1.0)
            elif st.startswith('phase:'):
                r.mark('phase ' + st[6:])
            else:
                hold = float(st.split('~', 1)[1]) if '~' in st else 0.15
                r.say(st); r.mark(st); r.sleep(hold + 0.85)
        return r.stop('done')
    except KeyboardInterrupt:
        return r.stop('interrupted')


# ---------------------------------------------------------------- comparison
MASK = [(re.compile(r'/swapin/runs/[^/\s]+/'), '/swapin/runs/<run>/'), (re.compile(r'0x[0-9a-fA-F]{5,}'), '0x#'),(re.compile(r'\b\d+\.\d+\b'), '#.#'), (re.compile(r'\b\d{4,}\b'), '#'),
        (re.compile(r'(p50|p95|max|time|took|in|after) [\d.]+ ?(ms|s|µs)'), r'\1 # \2')]
# the host's periodic statistics and timing lines (they differ between two native runs too)
NOISE = re.compile(r'swapin:|trace:|timing up to frame|memory up to frame|frame \d+:|power|present tick|QoS|hitch|pipeline|CPU-written texture|'
                   r'TBDR|staging|resident|prewarm|prebuild|archive|DM_CTL|shot|screenshot|guest AS|key numbers|: base 0x|'
                   r'texture views by texture|segmented submission|time-constraint policy|longframe|packaged textures')


def norm(ln):
    ln = ln.removeprefix('[dm] ')
    for r, t in MASK:
        ln = r.sub(t, ln)
    return ln


def log_lines(rd):
    out = []
    for ln in (rd / 'host.log').read_text(errors='replace').split('\n'):
        if ln and not NOISE.search(ln):
            out.append(norm(ln))
    return out


def cov(rd):   # first entries: {address: (ms, thread, kind)} from the coverage log (kinds 1 trap, 3 lifted)
    p = rd / 'trace/cov_log.bin'
    if not p.exists():
        return {}
    d = p.read_bytes()
    out = {}
    for i in range(0, len(d) - 15, 16):
        ms1, tid, kind, _, at = struct.unpack_from('<IHBBQ', d, i)
        if kind in (1, 3) and (at not in out or ms1 - 1 < out[at][0]):
            out[at] = (ms1 - 1, tid, kind)
    return out


def shot_diff(a, b):   # mean absolute difference of two PNGs, 0..255 (Pillow), or None
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return None
    if not (a.exists() and b.exists()):
        return None
    x = Image.open(a).convert('RGB').resize((480, 270))
    y = Image.open(b).convert('RGB').resize((480, 270))
    return round(sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3, 2)


def cmd_compare(argv):
    import collections
    a, b = RUNS / argv[0], RUNS / argv[1]
    rep = {'run': argv[0], 'reference': argv[1]}
    ra, rb = json.loads((a / 'result.json').read_text()), json.loads((b / 'result.json').read_text())
    rep['checkpoints'] = {'run': ra['checkpoints'], 'reference': rb['checkpoints']}
    rep['ended'] = {'run': ra['ended'], 'reference': rb['ended']}
    la, lb = collections.Counter(log_lines(a)), collections.Counter(log_lines(b))
    rep['log_only_in_run'] = sorted((la - lb).elements())[:40]
    rep['log_only_in_reference'] = sorted((lb - la).elements())[:40]
    rep['shots'] = {n: shot_diff(a / f'{n}.png', b / f'{n}.png') for n in ('title', 'loaded', 'menus', 'play')}
    ca, cb = cov(a), cov(b)
    if ca and cb:
        horizon = min(max(v[0] for v in ca.values()), max(v[0] for v in cb.values()))
        sa = {k for k, v in ca.items() if v[0] <= horizon}
        sb = {k for k, v in cb.items() if v[0] <= horizon}
        rep['entries'] = {'run': len(ca), 'reference': len(cb), 'horizon_ms': horizon, 'only_in_run': len(sa - cb.keys()),
                          'only_in_reference': len(sb - ca.keys())}
        # the main thread's first entries in order, where both runs have them
        def order(c):
            t = collections.Counter(v[1] for v in c.values()).most_common(1)[0][0]
            return [k for k, v in sorted(c.items(), key=lambda kv: kv[1][0]) if v[1] == t]
        oa, ob = order(ca), order(cb)
        common = set(oa) & set(ob)
        oa, ob = [k for k in oa if k in common], [k for k in ob if k in common]
        same = 0
        while same < min(len(oa), len(ob)) and oa[same] == ob[same]:
            same += 1
        # how many keep their relative order: the longest increasing run of run positions in reference order (patience sort)
        import bisect
        pos = {k: i for i, k in enumerate(oa)}
        tails = []
        for k in ob:
            j = bisect.bisect_left(tails, pos[k])
            tails[j:j + 1] = [pos[k]]
        rep['entries']['main_thread_common'] = len(oa)
        rep['entries']['main_thread_same_prefix'] = same
        rep['entries']['main_thread_in_order'] = len(tails)
        if same < len(oa):
            rep['entries']['main_thread_first_difference_ms'] = ca[oa[same]][0]
        (a / 'entries_only_in_run.txt').write_text(''.join(f'{k:x}\n' for k in sorted(sa - cb.keys())))
        (a / 'entries_only_in_reference.txt').write_text(''.join(f'{k:x}\n' for k in sorted(sb - ca.keys())))
    (a / f'compare_{argv[1]}.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n')
    print(json.dumps(rep, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('run', 'compare'):
        sys.exit(__doc__)
    {'run': cmd_run, 'compare': cmd_compare}[sys.argv[1]](sys.argv[2:])
