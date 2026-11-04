"""Re-run the whole binary census, one step at a time (single process, no parallelism).
Usage: py -3 -I run_all.py [ExeFS dir] [patched dir] [scratch dir]
Defaults are the Pokopia 2.0.1 build paths used for research/04-binary-census.md. Output: <scratch>/tables.md and the JSON it is built from."""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
EXEFS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(WORK, 'installer-test-win', 'build201', 'extract', 'ExeFS')
PATCHED = sys.argv[2] if len(sys.argv) > 2 else os.path.join(WORK, 'installer-test-win', 'build201', 'patched')
SCRATCH = sys.argv[3] if len(sys.argv) > 3 else os.path.join(WORK, 'windows-native', '_local', 'census')
MODULES = ('rtld', 'subsdk0', 'sdk', 'main')


def step(*args):
    t = time.time()
    subprocess.run([sys.executable, '-I', os.path.join(HERE, args[0])] + list(args[1:]), check=True, stdout=subprocess.DEVNULL)
    print(f'{" ".join(args):60s} {time.time() - t:6.1f} s')


os.makedirs(SCRATCH, exist_ok=True)
step('layout.py', EXEFS, SCRATCH)
for m in MODULES:
    step('insns.py', EXEFS, SCRATCH, m)
    step('diag.py', EXEFS, SCRATCH, m)
    step('xcheck_llvm.py', EXEFS, SCRATCH, m)
step('crosscheck.py', PATCHED, SCRATCH)
step('summarize.py', EXEFS, SCRATCH)
print('tables:', os.path.join(SCRATCH, 'tables.md'))
