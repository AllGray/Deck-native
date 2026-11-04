#!/usr/bin/env python3
"""Research copy only: turns ~/mp/nxnative-mp (a copy of ~/nxnative-trace) into the multiplayer-research host. Refuses anything but a ~/mp copy.
    python3 apply_mp_patch.py ~/mp/nxnative-mp ~/mp/macsrc
Idempotent: it applies to a fresh copy and upgrades a copy an earlier version patched, with exact anchors.
- step 0: hle_mptrace.c (DM_MPTRACE=1 call log); DM_MPFAKE=1: pctl allowed, a lone access point, lone-node virtual sockets;
- step 1 (docs/coop-step1.md): DM_COOP=host:<port> | join:127.0.0.1:<port> routes nn::ldn, the game's UDP sockets and (with DM_UID) the account Uid
  to the shared coop module (windows-native src/coop, from macsrc/coop/, copied in as hle_coop_*.c for the Makefile's hle_*.c wildcard)."""
import pathlib, shutil, sys

root = pathlib.Path(sys.argv[1]).expanduser().resolve()
src = pathlib.Path(sys.argv[2]).expanduser()
if root.parent.name != 'mp':
    sys.exit('refusing: give the ~/mp copy')
host = root / 'dm/host'


def put(dst, data):
    if not dst.exists() or dst.read_bytes() != data:
        dst.write_bytes(data)


put(host / 'hle_mptrace.c', (src / 'hle_mptrace.c').read_bytes())
for f in ('coop.h', 'coop_int.h'):
    put(host / f, (src / 'coop' / f).read_bytes())
for f in ('coop_ldn.c', 'coop_vnet.c', 'coop_mpnet.c', 'coop_plat.c', 'coop_disc.c', 'coop_auth.c'):   # coop_disc.c: step 1c's DNS-SD; coop_auth.c: 1d's internet play
    put(host / ('hle_' + f), (src / 'coop' / f).read_bytes())


def edit(name, old, new):
    p = host / name
    s = p.read_text()
    if new in s:
        return
    if s.count(old) != 1:
        sys.exit(f'{name}: anchor not found exactly once: {old[:60]!r}')
    p.write_text(s.replace(old, new))


def once(lines, pred, what):
    hit = [i for i, l in enumerate(lines) if pred(l)]
    if len(hit) != 1:
        sys.exit(f'{what}: found {len(hit)} times')
    return hit[0]


def section(name, start, new_text):   # from the line starting with `start` to the next "// ----------" section line
    p = host / name
    lines = p.read_text().split('\n')
    a = once(lines, lambda l: l.startswith(start), f'{name}: {start!r}')
    b = next(i for i in range(a + 1, len(lines)) if lines[i].startswith('// ----------'))
    new = new_text.rstrip('\n').split('\n') + ['']
    if lines[a:b] != new:
        lines[a:b] = new
        p.write_text('\n'.join(lines))


def func(name, header, new_text):   # a whole function: its header line to the first line that is "}"
    p = host / name
    lines = p.read_text().split('\n')
    a = once(lines, lambda l: l.startswith(header), f'{name}: {header!r}')
    b = next(i for i in range(a + 1, len(lines)) if lines[i] == '}')
    new = new_text.rstrip('\n').split('\n')
    if lines[a:b + 1] != new:
        lines[a:b + 1] = new
        p.write_text('\n'.join(lines))


def body(name, file, new, sig=None):   # a one-line hook: keep its head, replace its body
    q = host / file
    ls = q.read_text().split('\n')
    i = once(ls, lambda l: l.startswith(('SKF("' + name + '"', 'HLE_FORCE("' + name + '"')), f'{file}: {name}')
    l = ls[i]
    if not l.rstrip().endswith('}'):
        sys.exit(f'{file}: {name} is not a one-line definition')
    head = l[:l.index(') { ') + 1]
    if sig and sig[1] not in head:
        head = head[:head.rindex(sig[0])] + sig[1]
    nl = head + ' ' + new
    if nl != l:
        ls[i] = nl
        q.write_text('\n'.join(ls))


edit('hle_count.c', 'void *hlecount_wrap(const char *kind, const char *name, void *fn) {\n    if (!hc_on() || !fn) return fn;',
     'void *hlecount_wrap(const char *kind, const char *name, void *fn) {\n'
     '    { void *mp_wrap(const char *, const char *, void *); void *mw = mp_wrap(kind, name, fn); if (mw) return mw; }   // research copy\n'
     '    if (!hc_on() || !fn) return fn;')
edit('hle_count.c', 'void hlecount_seal(void) {\n    if (!hc_on() || !g_hcode) return;',
     'void hlecount_seal(void) {\n    { void mp_seal(void); mp_seal(); }   // research copy\n    if (!hc_on() || !g_hcode) return;')

# nn::ldn: the coop module (DM_COOP), step 0's lone access point (DM_MPFAKE=1), else cut
section('hle_offline.c', '// ---------- ldn: local wireless', (src / 'ldn_coop.c').read_text())

# Link Play asks parental controls before opening (step 0 found it): allowed under DM_MPFAKE=1 or DM_COOP
p = host / 'hle_misc.c'
ls = p.read_text().split('\n')
helper = ('static unsigned mpfake_pctl(void) { const char *e = getenv("DM_MPFAKE"), *c = getenv("DM_COOP"); return (e && atoi(e)) || (c && *c); }'
          '   // research copy: Link Play (DM_MPFAKE, DM_COOP)')
hit = [i for i, l in enumerate(ls) if l.startswith('static unsigned mpfake_pctl(void)')]
if hit:
    ls[hit[0]] = helper
else:
    a = once(ls, lambda l: l == '// ---------- nsd server name resolution / pctl free communication: networking, out of scope ----------', 'hle_misc.c: pctl section')
    ls.insert(a + 1, helper)
p.write_text('\n'.join(ls))
edit('hle_misc.c', 'h_pctl_CheckFree, unsigned show_ui) { (void)show_ui; return 0; }', 'h_pctl_CheckFree, unsigned show_ui) { (void)show_ui; return mpfake_pctl(); }')
edit('hle_misc.c', 'h_pctl_TryBegin, unsigned show_ui) { (void)show_ui; return 0; }', 'h_pctl_TryBegin, unsigned show_ui) { (void)show_ui; return mpfake_pctl(); }')
edit('hle_misc.c', 'h_pctl_IsFree, void) { return 0; }', 'h_pctl_IsFree, void) { return mpfake_pctl(); }')

# The account Uid: DM_UID under DM_COOP (two instances must not share one), else nxnative's constant
edit('hle_account.c', 'static const Uid k_uid = {{0x78626c0000000001ull, 0x53574e4154495645ull}};   // Non-zero means valid\n',
     'static const Uid k_uid_const = {{0x78626c0000000001ull, 0x53574e4154495645ull}};   // Non-zero means valid\n'
     '// Research copy: with DM_COOP, DM_UID=<32 hex digits> gives this instance its own user (KTGL refuses a second member with the same user ID)\n'
     '#include "coop.h"\n#include <stdatomic.h>\n'
     'static const Uid *dm_uid(void) {\n'
     '    static Uid u; static _Atomic int st;   // 0 unread, 1 DM_UID, 2 the constant\n'
     '    int s = atomic_load(&st);\n'
     '    if (!s) {\n'
     '        uint8_t b[16]; s = coop_env_uid(b) ? 1 : 2;\n'
     '        if (s == 1) { memcpy(&u, b, 16); LOG("[coop] account: Uid %016llx%016llx from DM_UID", (unsigned long long)u.v[0], (unsigned long long)u.v[1]); }\n'
     '        atomic_store(&st, s);\n'
     '    }\n'
     '    return s == 1 ? &u : &k_uid_const;\n'
     '}\n'
     '#define k_uid (*dm_uid())\n')

# The game's UDP sockets: hle_net.c gets the routing block (sock_coop.c) right after sk_fail, and Poll / Select that know virtual descriptors
p = host / 'hle_net.c'
net = p.read_text()
a, b = 'static int sk_fail(void) { t_skerr = SK_ENETDOWN; return -1; }\n', '#define SKF(m, cname, ...) HLE_FORCE('
if net.count(a) != 1 or net.count(b) != 1:
    sys.exit('hle_net.c: sk_fail / SKF anchors')
i, j = net.index(a) + len(a), net.index(b)
block = (src / 'sock_coop.c').read_text()
if net[i:j] != block:
    p.write_text(net[:i] + block + net[j:])
poll = (src / 'sock_poll.c').read_text().split('\nint dm_sk_select(')
func('hle_net.c', 'int dm_sk_poll(void *fds, uint64_t n, int ms) {', poll[0])
func('hle_net.c', 'int dm_sk_select(int nfds, const uint64_t *r, const uint64_t *w, const uint64_t *e, const int64_t *tv) {', 'int dm_sk_select(' + poll[1])

FCNTL = ('int c)', 'int c, int v)')   # Fcntl is variadic; its third argument arrives in x2
for nm in ('_ZN2nn6socket6SocketENS0_6FamilyENS0_4TypeENS0_8ProtocolE', 'nnsocketSocket'):
    body(nm, 'hle_net.c', '{ return mpsk_socket(a, b, c); }')
for nm in ('_ZN2nn6socket4BindEiPKNS0_8SockAddrEj', 'nnsocketBind'):
    body(nm, 'hle_net.c', '{ return mpsk_bind(s, a, n); }')
for nm in ('_ZN2nn6socket5CloseEi', 'nnsocketClose'):
    body(nm, 'hle_net.c', '{ return mpsk_close(s); }')
for nm in ('_ZN2nn6socket11GetSockNameEiPNS0_8SockAddrEPj', 'nnsocketGetSockName'):
    body(nm, 'hle_net.c', '{ return mpsk_getname(s, a, n); }')
for nm in ('_ZN2nn6socket10GetSockOptEiNS0_5LevelENS0_6OptionEPvPj', 'nnsocketGetSockOpt'):
    body(nm, 'hle_net.c', '{ return mpsk_getopt(s, l, o, v, n); }')
for nm in ('_ZN2nn6socket10SetSockOptEiNS0_5LevelENS0_6OptionEPKvj', 'nnsocketSetSockOpt'):
    body(nm, 'hle_net.c', '{ return mpsk_setopt(s, l, o, v, n); }')
for nm in ('_ZN2nn6socket5FcntlEiNS0_12FcntlCommandEz', 'nnsocketFcntl'):
    body(nm, 'hle_net.c', '{ return mpsk_fcntl(s, c, v); }', sig=FCNTL)
for nm in ('_ZN2nn6socket4RecvEiPvmNS0_7MsgFlagE', 'nnsocketRecv'):
    body(nm, 'hle_net.c', '{ return mpsk_recv(s, b, n, f); }')
body('_ZN2nn6socket8RecvFromEiPvmNS0_7MsgFlagEPNS0_8SockAddrEPj', 'hle_net.c', '{ return mpsk_recvfrom(s, b, n, f, a, al); }')
for nm in ('_ZN2nn6socket4SendEiPKvmNS0_7MsgFlagE', 'nnsocketSend'):
    body(nm, 'hle_net.c', '{ return mpsk_send(s, b, n, f); }')
# hle_offline.c's overloads (plain sockaddr / int forms)
for nm in ('_ZN2nn6socket6SendToEiPKvmNS0_7MsgFlagEPKNS0_8SockAddrEj', '_ZN2nn6socket6SendToEiPKvmiPK8sockaddrj'):
    body(nm, 'hle_offline.c', '{ long mpsk_sendto(int, const void *, unsigned long, int, const void *, unsigned); return mpsk_sendto(s, b, n, f, a, al); }')
body('_ZN2nn6socket8RecvFromEiPvmiP8sockaddrPj', 'hle_offline.c', '{ long mpsk_recvfrom(int, void *, unsigned long, int, void *, unsigned *); return mpsk_recvfrom(s, b, n, f, a, al); }')
body('_ZN2nn6socket12GetLastErrnoEv', 'hle_offline.c', '{ int mpsk_errno(void); return mpsk_errno(); }')
body('_ZN2nn6socket6SocketEiii', 'hle_offline.c', '{ int mpsk_socket(int, int, int); return mpsk_socket(a, b, c); }')
body('_ZN2nn6socket4BindEiPK8sockaddrj', 'hle_offline.c', '{ int mpsk_bind(int, const void *, unsigned); return mpsk_bind(s, a, n); }')
body('_ZN2nn6socket11GetSockNameEiP8sockaddrPj', 'hle_offline.c', '{ int mpsk_getname(int, void *, unsigned *); return mpsk_getname(s, a, n); }')
body('_ZN2nn6socket10GetSockOptEiiiPvPj', 'hle_offline.c', '{ int mpsk_getopt(int, int, int, void *, unsigned *); return mpsk_getopt(s, l, o, v, n); }')
body('_ZN2nn6socket10SetSockOptEiiiPKvj', 'hle_offline.c', '{ int mpsk_setopt(int, int, int, const void *, unsigned); return mpsk_setopt(s, l, o, v, n); }')
body('_ZN2nn6socket5FcntlEiiz', 'hle_offline.c', '{ int mpsk_fcntl(int, int, int); return mpsk_fcntl(s, c, v); }', sig=FCNTL)
body('_ZN2nn6socket4RecvEiPvmi', 'hle_offline.c', '{ long mpsk_recv(int, void *, unsigned long, int); return mpsk_recv(s, b, n, f); }')
body('_ZN2nn6socket4SendEiPKvmi', 'hle_offline.c', '{ long mpsk_send(int, const void *, unsigned long, int); return mpsk_send(s, b, n, f); }')
print('patched', host)
