#!/usr/bin/env python3
"""titledrive.py <build folder> <tag> [--host <dmhost>] [--env K=V ...] [--seconds N] [--save <folder>|--empty-save]
M4's reference run (docs/m4-title.md): boots the game to its title screen and leaves it there, with no input, for N seconds (default 75), then asks
nxnative-trace's writer to flush (when DM_TRACE is set) and stops the host. A copy of tracedrive.py without the title presses or the menu steps.
The run folder is ~/setup/m4title/<tag>/: host.log, phases.tsv (seconds since the host started), title<N>.png at 20, 30, 45 and 60 s, trace/ (DM_TRACE).
--empty-save starts from an empty save folder (the Windows runs do); the default copies ~/setup/save_run4/save. The build folder is only read."""
import os, pathlib, shutil, subprocess, sys, time

H = pathlib.Path.home(); B = pathlib.Path(sys.argv[1]).expanduser(); TAG = sys.argv[2]; rest = sys.argv[3:]
host = B / 'host/dmhost'; extra = {}; seconds = 75.0; save = H / 'setup/save_run4/save'
while rest and rest[0].startswith('--'):
    if rest[0] == '--host': host = pathlib.Path(rest[1]).expanduser(); rest = rest[2:]
    elif rest[0] == '--env': k, v = rest[1].split('=', 1); extra[k] = v; rest = rest[2:]
    elif rest[0] == '--seconds': seconds = float(rest[1]); rest = rest[2:]
    elif rest[0] == '--save': save = pathlib.Path(rest[1]).expanduser(); rest = rest[2:]
    elif rest[0] == '--empty-save': save = None; rest = rest[1:]
    else: sys.exit(f'unknown option {rest[0]}')
rd = H / f'setup/m4title/{TAG}'; shutil.rmtree(rd, ignore_errors=True); rd.mkdir(parents=True)
if save: shutil.copytree(save, rd / 'save')
else: (rd / 'save').mkdir()
(rd / 'temp').mkdir()
tr = rd / 'trace'; tr.mkdir()
ctl = rd / 'ctl.txt'; ctl.write_text(''); log = rd / 'host.log'
env = dict(os.environ, DM_ROOT=str(B), DM_ROMFS=str(B / 'extract/RomFS.img'), DM_SAVE=str(rd / 'save'), DM_TEMP=str(rd / 'temp'), DM_MSL_DIR=str(B / 'shader/cache'),
           DM_FONT_DIR=str(B / 'font'), DM_NVN_SHMAP=str(B / 'profile/0400A3A01EE4C000/shader_map.tsv'), DM_LANG='en-US', DM_AUDIO='0', DM_CTL=str(ctl), **extra)
if 'DM_TRACE' in env: env['DM_TRACE'] = str(tr)
ph = open(rd / 'phases.tsv', 'w'); ph.write('second\tevent\n')
t0 = time.time()


def mark(what):
    ph.write(f'{time.time() - t0:.2f}\t{what}\n'); ph.flush()


def shot(name):
    f = rd / f'{name}.png'
    with open(ctl, 'a') as c: c.write(f'shot {f}\n')
    for _ in range(60):
        if f.exists() and f.stat().st_size: break
        time.sleep(0.25)
    mark(f'shot {name} {"ok" if f.exists() else "none"}')


p = subprocess.Popen([str(host), str(B / 'patched')], env=env, cwd=rd, stdout=open(log, 'w'), stderr=subprocess.STDOUT)
mark('start')
try:
    for at in (20, 30, 45, 60):
        if at >= seconds: break
        time.sleep(max(0.0, at - (time.time() - t0)))
        if p.poll() is not None: sys.exit(f'the host exited ({p.returncode}) at {time.time() - t0:.1f} s')
        shot(f'title{at}')
    time.sleep(max(0.0, seconds - (time.time() - t0)))
    if 'DM_TRACE' in env:
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
