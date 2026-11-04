// Research copy only (~/mp/nxnative-mp, multiplayer research 06): DM_MPTRACE=1 logs every guest call of the networking / account / pctl /
// friends / streamplay hooks with x0..x7, the guest return address, the result and struct dumps for the ldn and socket calls.
// hle_count.c's hlecount_wrap asks mp_wrap first: a selected entry gets a stub "ldr x16, =&entry; ldr x17, =mp_tramp; br x17".
#include "dm.h"
#include <stdatomic.h>
#include <sys/mman.h>
#include <libkern/OSCacheControl.h>
#define MP_MAX 1024
typedef struct { const char *name; void *fn; _Atomic uint64_t n; } mp_ent;
static mp_ent g_mp[MP_MAX]; static unsigned g_nmp; static uint32_t *g_mpcode; static int g_mpon = -1, g_mpsealed;
static const char *const k_sel[] = {"N2nn3ldn", "N2nn4nifm", "N2nn6socket", "nnsocket", "N2nn4pctl", "N2nn7account", "N2nn7friends", "N2nn10streamplay",
                                    "N2nn3ssl", "nnssl", "N2nn4bcat", "N2nn3nsd", "N2nn3err", "N2nn4time36AsyncEnsure", "N2nn2oe39SendServer", NULL};
static int sel(const char *n) { for (int i = 0; k_sel[i]; i++) if (strstr(n, k_sel[i])) return 1; return 0; }
void mp_tramp(void);
__asm__(".globl _mp_tramp\n.p2align 4\n_mp_tramp:\n"
        "sub sp, sp, #0x140\n"
        "stp x0, x1, [sp, #0x00]\n stp x2, x3, [sp, #0x10]\n stp x4, x5, [sp, #0x20]\n stp x6, x7, [sp, #0x30]\n"
        "stp x8, x16, [sp, #0x40]\n stp x29, x30, [sp, #0x50]\n"
        "stp q0, q1, [sp, #0x60]\n stp q2, q3, [sp, #0x80]\n stp q4, q5, [sp, #0xa0]\n stp q6, q7, [sp, #0xc0]\n"
        "add x29, sp, #0x50\n"
        "mov x0, x16\n mov x1, sp\n bl _mp_pre\n"
        "ldp q0, q1, [sp, #0x60]\n ldp q2, q3, [sp, #0x80]\n ldp q4, q5, [sp, #0xa0]\n ldp q6, q7, [sp, #0xc0]\n"
        "ldp x0, x1, [sp, #0x00]\n ldp x2, x3, [sp, #0x10]\n ldp x4, x5, [sp, #0x20]\n ldp x6, x7, [sp, #0x30]\n ldr x8, [sp, #0x40]\n"
        "ldr x16, [sp, #0x48]\n ldr x16, [x16, #8]\n blr x16\n"
        "stp x0, x1, [sp, #0xe0]\n str q0, [sp, #0xf0]\n"
        "ldr x0, [sp, #0x48]\n mov x1, sp\n ldr x2, [sp, #0xe0]\n bl _mp_post\n"
        "ldp x0, x1, [sp, #0xe0]\n ldr q0, [sp, #0xf0]\n"
        "ldp x29, x30, [sp, #0x50]\n add sp, sp, #0x140\n ret\n");
static int mp_on(void) { if (g_mpon < 0) { const char *e = getenv("DM_MPTRACE"); g_mpon = e && atoi(e); } return g_mpon; }
static int want(mp_ent *e, uint64_t k) { return k < 60 || k % 500 == 0; }
static void hexs(char *o, size_t on, const void *p, size_t n) {
    static const char hx[] = "0123456789abcdef"; size_t j = 0; const uint8_t *b = p;
    if (!p) { snprintf(o, on, "(null)"); return; }
    for (size_t i = 0; i < n && j + 3 < on; i++) { o[j++] = hx[b[i] >> 4]; o[j++] = hx[b[i] & 15]; }
    o[j] = 0;
}
static void dump(const char *nm, const char *what, const void *p, size_t n) { static char buf[0x480 * 2 + 8]; static _Atomic int lk;
    while (atomic_exchange(&lk, 1)) {} hexs(buf, sizeof buf, p, n); LOG("[mp]   %s %s[%zu]=%s", nm, what, n, buf); atomic_store(&lk, 0); }
static void texts(const char *nm, const uint8_t *p, size_t n) {   // the printable runs inside an argument (an error applet's code and messages)
    for (size_t i = 0, k = 0; i < n && k < 4; i++) {
        size_t j = i; while (j < n && p[j] >= 0x20 && p[j] < 0x7f) j++;
        if (j - i >= 4) { LOG("[mp]   %s text@%#zx \"%.*s\"", nm, i, (int)(j - i > 240 ? 240 : j - i), (const char *)p + i); k++; }
        i = j;
    }
}
static __thread uint64_t t_k;
void mp_pre(mp_ent *e, uint64_t *r) {
    uint64_t k = atomic_fetch_add(&e->n, 1); t_k = k; if (!want(e, k)) return;
    const char *n = e->name;
    LOG("[mp] > %s #%llu lr=%#llx x0=%#llx x1=%#llx x2=%#llx x3=%#llx x4=%#llx x5=%#llx", n, (unsigned long long)k, (unsigned long long)r[11],
        (unsigned long long)r[0], (unsigned long long)r[1], (unsigned long long)r[2], (unsigned long long)r[3], (unsigned long long)r[4], (unsigned long long)r[5]);
    void *a0 = (void *)r[0], *a1 = (void *)r[1], *a2 = (void *)r[2], *a3 = (void *)r[3], *a4 = (void *)r[4];
    if (strstr(n, "3ldn13CreateNetwork")) { dump(n, "NetworkConfig", a0, 0x20); dump(n, "SecurityConfig", a1, 0x44); dump(n, "UserConfig", a2, 0x30); }
    else if (strstr(n, "3ldn7Connect")) { dump(n, "NetworkInfo", a0, 0x480); dump(n, "SecurityConfig", a1, 0x44); dump(n, "UserConfig", a2, 0x30); }
    else if (strstr(n, "3ldn4Scan")) dump(n, "ScanFilter", a3, 0x60);
    else if (strstr(n, "3ldn16SetAdvertiseData")) dump(n, "data", a0, r[1] < 0x180 ? r[1] : 0x180);
    else if (strstr(n, "socket4Bind") || strstr(n, "socket7Connect") || !strcmp(n, "nnsocketBind") || !strcmp(n, "nnsocketConnect")) dump(n, "addr", a1, 16);
    else if (strstr(n, "socket6SendTo")) { dump(n, "to", a4, 16); dump(n, "data", a1, r[2] < 96 ? r[2] : 96); }
    else if (strstr(n, "socket10SetSockOpt") || !strcmp(n, "nnsocketSetSockOpt")) dump(n, "value", a3, r[4] < 16 ? r[4] : 16);
    else if (strstr(n, "socket10InitializeERKNS0_6ConfigE")) dump(n, "Config", a0, 0x40);
    else if (strstr(n, "3err20ShowApplicationError") && a0) { dump(n, "arg", a0, 0x20); texts(n, a0, 0x1010); }
    (void)a2;
}
void mp_post(mp_ent *e, uint64_t *r, uint64_t ret) {
    if (!want(e, t_k)) return;
    const char *n = e->name;
    LOG("[mp] < %s #%llu = %#llx", n, (unsigned long long)t_k, (unsigned long long)ret);
    if (strstr(n, "3ldn14GetNetworkInfo") && t_k < 6 && (uint32_t)ret == 0) dump(n, "NetworkInfo", (void *)r[0], 0x480);
    else if (strstr(n, "3ldn14GetIpv4Address")) { dump(n, "ip", (void *)r[0], 4); dump(n, "mask", (void *)r[1], 4); }
    else if (strstr(n, "3ldn4Scan") && r[1]) { int c = *(int *)r[1]; LOG("[mp]   scan count %d", c); for (int i = 0; i < c && i < 4; i++) dump(n, "found", (uint8_t *)r[0] + 0x480 * i, 0x480); }
    else if (strstr(n, "socket8RecvFrom") && (int64_t)ret > 0) { dump(n, "data", (void *)r[1], ret < 96 ? ret : 96); dump(n, "from", (void *)r[4], 16); }
    else if (strstr(n, "7account9GetUserId")) dump(n, "uid", (void *)r[0], 16);
    else if (strstr(n, "7account11GetNickname")) dump(n, "nick", (void *)r[0], 33);
    else if (strstr(n, "7account26GetNetworkServiceAccountId")) dump(n, "nsaid", (void *)r[0], 8);
}
void *mp_wrap(const char *kind, const char *name, void *fn) {
    (void)kind;
    if (!mp_on() || !fn || !name || !sel(name)) return NULL;
    if (g_mpsealed) return NULL;
    for (unsigned i = 0; i < g_nmp; i++) if (g_mp[i].fn == fn && !strcmp(g_mp[i].name, name)) return g_mpcode + 8 * i;
    if (g_nmp == MP_MAX) return NULL;
    if (!g_mpcode) { g_mpcode = mmap(NULL, MP_MAX * 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); if (g_mpcode == MAP_FAILED) die("DM_MPTRACE mmap"); }
    unsigned i = g_nmp++; g_mp[i].name = name; g_mp[i].fn = fn;
    uint32_t *w = g_mpcode + 8 * i;                                  // 32 bytes: ldr x16,[pc+0x10]; ldr x17,[pc+0x14]; br x17; nop; &entry; mp_tramp
    w[0] = 0x58000000 | ((0x10 / 4) << 5) | 16;
    w[1] = 0x58000000 | (((0x18 - 4) / 4) << 5) | 17;
    w[2] = 0xD61F0220;
    w[3] = 0xD503201F;
    uint64_t a = (uint64_t)&g_mp[i], b = (uint64_t)(void *)mp_tramp; memcpy(w + 4, &a, 8); memcpy(w + 6, &b, 8);
    return w;
}
void mp_seal(void) {
    if (!g_mpcode || g_mpsealed) return;
    if (mprotect(g_mpcode, MP_MAX * 32, PROT_READ | PROT_EXEC)) die("DM_MPTRACE mprotect");
    g_mpsealed = 1; sys_icache_invalidate(g_mpcode, MP_MAX * 32);
    LOG("DM_MPTRACE: %u entries traced", g_nmp);
}
