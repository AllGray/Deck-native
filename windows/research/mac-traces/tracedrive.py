#!/usr/bin/env python3
"""tracedrive.py <build folder> <tag> [--host <dmhost>] [--env K=V ...] [--boot <s>] [--title <presses>] step ...
~/setup/menudrive.py with tracing: boots a copy of the run-4 save on an installer build (read-only use) with DM_TRACE=<run>/trace,
presses A through the title screen and the save load, plays the steps through DM_CTL, then asks the trace writer to flush and stops
the host. The run folder is ~/setup/traces/<tag>/; phases.tsv there has each step's time in seconds since the host started.
Steps: a key ("X", "A+B", "LUp~2" = held 2 s), "wait:<s>", "shot:<name>" (PNG of the next frame), "ctl:<raw DM_CTL line>",
"phase:<name>" (a phase boundary)."""
import os, pathlib, shutil, subprocess, sys, time

H = pathlib.Path.home(); B = pathlib.Path(sys.argv[1]).expanduser(); TAG = sys.argv[2]; rest = sys.argv[3:]
host = B / 'host/dmhost'; extra = {}; boot = 40.0; title = 8
while rest and rest[0].startswith('--'):
    if rest[0] == '--host': host = pathlib.Path(rest[1]).expanduser(); rest = rest[2:]
    elif rest[0] == '--env': k, v = rest[1].split('=', 1); extra[k] = v; rest = rest[2:]
    elif rest[0] == '--boot': boot = float(rest[1]); rest = rest[2:]
    elif rest[0] == '--title': title = int(rest[1]); rest = rest[2:]
    else: sys.exit(f'unknown option {rest[0]}')
rd = H / f'setup/traces/{TAG}'; shutil.rmtree(rd, ignore_errors=True); rd.mkdir(parents=True)
shutil.copytree(H / 'setup/save_run4/save', rd / 'save'); (rd / 'temp').mkdir()
tr = rd / 'trace'; tr.mkdir()
ctl = rd / 'ctl.txt'; ctl.write_text(''); log = rd / 'host.log'
env = dict(os.environ, DM_ROOT=str(B), DM_ROMFS=str(B / 'extract/RomFS.img'), DM_SAVE=str(rd / 'save'), DM_TEMP=str(rd / 'temp'), DM_MSL_DIR=str(B / 'shader/cache'),
           DM_FONT_DIR=str(B / 'font'), DM_NVN_SHMAP=str(B / 'profile/0400A3A01EE4C000/shader_map.tsv'), DM_LANG='en-US', DM_AUDIO='0', DM_CTL=str(ctl),
           DM_TRACE=str(tr), **extra)
ph = open(rd / 'phases.tsv', 'w'); ph.write('second\tevent\n')
t0 = time.time()


def mark(what):
    ph.write(f'{time.time() - t0:.2f}\t{what}\n'); ph.flush()


def say(line):
    with open(ctl, 'a') as f: f.write(line + '\n')


def shot(name):
    f = rd / f'{name}.png'; say(f'shot {f}')
    for _ in range(60):
        if f.exists() and f.stat().st_size: break
        time.sleep(0.25)
    mark(f'shot {name} {"ok" if f.exists() else "none"}')


p = subprocess.Popen([str(host), str(B / 'patched')], env=env, cwd=rd, stdout=open(log, 'w'), stderr=subprocess.STDOUT)
mark('start')
try:
    time.sleep(boot)
    mark('phase title'); shot('title')
    for i in range(title):
        say('A'); mark('A'); time.sleep(4)
        if i in (0, 2, 5): shot(f'title{i}')
    time.sleep(8)
    mark('phase loaded'); shot('loaded')
    for s in rest:
        if p.poll() is not None: sys.exit(f'the host exited ({p.returncode}) before step {s}')
        if s.startswith('wait:'): time.sleep(float(s[5:]))
        elif s.startswith('shot:'): shot(s[5:])
        elif s.startswith('ctl:'): say(s[4:]); mark(s); time.sleep(1.0)
        elif s.startswith('phase:'): mark('phase ' + s[6:])
        else:
            hold = float(s.split('~', 1)[1]) if '~' in s else 0.15
            say(s); mark(s); time.sleep(hold + 0.85)
    mark('flush'); (tr / 'flush').touch()
    for _ in range(240):
        if (tr / 'flushed').exists(): break
        time.sleep(0.25)
    mark('flushed' if (tr / 'flushed').exists() else 'flush timed out')
finally:
    rc = p.poll()
    p.terminate()
    try: p.wait(10)
    except subprocess.TimeoutExpired: p.kill()
    mark(f'stop (host was {"running" if rc is None else f"exited {rc}"})')
