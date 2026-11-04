#!/usr/bin/env python3
"""Turns a copy of nxnative into the trace host: copies hle_trace.c into dm/host (the Makefile takes hle_*.c by wildcard) and
makes three one-line hooks. Run on the Mac against the copy only, never against ~/nxnative:
    python3 apply_trace_patch.py ~/nxnative-trace hle_trace.c
Then: cd ~/nxnative-trace && DM_GAMES_DIR="$HOME/installer-test/build201-clean/profile" make -C dm/host GAME=0400A3A01EE4C000 dmhost -j8"""
import pathlib, shutil, sys

root = pathlib.Path(sys.argv[1]).expanduser().resolve()
if root.name == 'nxnative':
    sys.exit('refusing to patch ~/nxnative itself: give the copy')
host = root / 'dm/host'
shutil.copy(sys.argv[2], host / 'hle_trace.c')


def edit(name, old, new):
    p = host / name; s = p.read_text()
    if new in s:
        return
    if s.count(old) != 1:
        sys.exit(f'{name}: anchor not found exactly once: {old!r}')
    p.write_text(s.replace(old, new))


# crash.c: let the trace module chain to the trap hook probe.c installed
edit('crash.c', 'void crash_set_trap_hook(int (*fn)(ucontext_t *uc)) { g_trap_hook = fn; }',
     'void crash_set_trap_hook(int (*fn)(ucontext_t *uc)) { g_trap_hook = fn; }\n'
     'int (*crash_get_trap_hook(void))(ucontext_t *uc) { return g_trap_hook; }   // trace copy: hle_trace.c chains to it')
# main.c: install after the probes, before any guest code runs
edit('main.c', '    probe_install();   // DM_PROBE probes (for diagnostics)\n',
     '    probe_install();   // DM_PROBE probes (for diagnostics)\n'
     '    { void trace_install(void); trace_install(); }   // trace copy: DM_TRACE (hle_trace.c)\n')
# hle.c: every gate passes through the trace first
edit('hle.c', '        if (g->ret == ret_addr) switch (g->kind) {',
     '        if (g->ret == ret_addr) { extern int g_trace_on; void trace_gate(uint64_t, int, int, uint64_t, uint64_t *);   // trace copy\n'
     '            if (g_trace_on) trace_gate(g->ret, g->kind, g->arg, g->orig, regs); }\n'
     '        if (g->ret == ret_addr) switch (g->kind) {')
print('patched', host)
