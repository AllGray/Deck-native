// Research copy only: the game's UDP sockets on a virtual LDN network. DM_COOP: the coop module's vnet (Link Play between instances, windows-native
// src/coop): sockets made while nn::ldn is initialized live on 169.254.S.0/24 and reach the other instances. DM_MPFAKE=1 without DM_COOP: step 0's lone
// node (sends dropped and counted, nothing received: EAGAIN, Poll / Select report writable only). A descriptor neither knows keeps the offline answer
// (-1, ENETDOWN). Descriptors 0x100 and up, so they never collide with -1 checks. Results -errno come back as -1 with GetLastErrno set (FreeBSD values).
#include <pthread.h>
#include "coop.h"
coop_node *dm_coop(void);   // hle_offline.c: NULL without DM_COOP
#define MPSK_BASE 0x100
#define MPSK_N 32
#define MPSK_EAGAIN 35
static struct { int used, nb, port, sends, recvs; } g_mpsk[MPSK_N];
static pthread_mutex_t g_mpsk_lk = PTHREAD_MUTEX_INITIALIZER;
static int mpsk_on(void) { static int f = -1; if (f < 0) { const char *e = getenv("DM_MPFAKE"); f = e ? atoi(e) : 0; } return f; }
static int mpsk_fake(int s) { return mpsk_on() && s >= MPSK_BASE && s < MPSK_BASE + MPSK_N && g_mpsk[s - MPSK_BASE].used; }
static int mpsk_is(int s) { coop_node *c = dm_coop(); return c ? coop_vnet_is(c, s) : mpsk_fake(s); }
static long mpsk_ret(int64_t r) { if (r >= 0) return (long)r; t_skerr = (int)-r; return -1; }
static int mpsk_err(int e) { t_skerr = e; return -1; }
int mpsk_errno(void) { return t_skerr; }
int mpsk_socket(int fam, int type, int proto) {
    coop_node *c = dm_coop();
    if (c) {
        int r = coop_vnet_socket(c, fam, type, proto);
        if (r < 0) LOG("[coop] sock: Socket(%d, %#x, %d) stays offline (only AF_INET datagram sockets after ldn::Initialize are virtual)", fam, type, proto);
        return r < 0 ? (int)mpsk_ret(r) : r;
    }
    if (!mpsk_on() || fam != 2 || (type & 0xffff) != 2) { LOG("[mp] sock: Socket(%d, %#x, %d) refused (only AF_INET datagram sockets are virtual)", fam, type, proto); return sk_fail(); }
    pthread_mutex_lock(&g_mpsk_lk);
    for (int i = 0; i < MPSK_N; i++) if (!g_mpsk[i].used) {
        memset(&g_mpsk[i], 0, sizeof g_mpsk[i]); g_mpsk[i].used = 1; g_mpsk[i].nb = !!(type & 0x20000000);
        pthread_mutex_unlock(&g_mpsk_lk);
        LOG("[mp] sock: Socket(%d, %#x, %d) -> virtual %#x%s", fam, type, proto, MPSK_BASE + i, g_mpsk[i].nb ? " (non-blocking)" : "");
        return MPSK_BASE + i;
    }
    pthread_mutex_unlock(&g_mpsk_lk);
    return mpsk_err(24);   // EMFILE
}
int mpsk_bind(int s, const void *a, unsigned n) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? (int)mpsk_ret(coop_vnet_bind(c, s, a, n)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    const uint8_t *p = a; int port = n >= 4 ? p[2] << 8 | p[3] : 0;
    g_mpsk[s - MPSK_BASE].port = port;
    LOG("[mp] sock: Bind(%#x) port %d addr %u.%u.%u.%u", s, port, n >= 8 ? p[4] : 0, n >= 8 ? p[5] : 0, n >= 8 ? p[6] : 0, n >= 8 ? p[7] : 0);
    return 0;
}
int mpsk_close(int s) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? (int)mpsk_ret(coop_vnet_close(c, s)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    LOG("[mp] sock: Close(%#x): %d sends dropped, %d receives answered EAGAIN", s, g_mpsk[s - MPSK_BASE].sends, g_mpsk[s - MPSK_BASE].recvs);
    g_mpsk[s - MPSK_BASE].used = 0; return 0;
}
int mpsk_setopt(int s, int l, int o, const void *v, unsigned n) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? (int)mpsk_ret(coop_vnet_setsockopt(c, s, l, o, v, n)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    uint32_t x = 0; if (v) memcpy(&x, v, n < 4 ? n : 4);
    LOG("[mp] sock: SetSockOpt(%#x, level %#x, option %#x, %u bytes, %#x)", s, l, o, n, x);
    return 0;
}
int mpsk_getopt(int s, int l, int o, void *v, unsigned *n) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? (int)mpsk_ret(coop_vnet_getsockopt(c, s, l, o, v, n)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    if (v && n) memset(v, 0, *n);
    LOG("[mp] sock: GetSockOpt(%#x, level %#x, option %#x) -> zeros", s, l, o);
    return 0;
}
int mpsk_getname(int s, void *a, unsigned *n) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? (int)mpsk_ret(coop_vnet_getsockname(c, s, a, n)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    uint8_t sa[16] = {16, 2, (uint8_t)(g_mpsk[s - MPSK_BASE].port >> 8), (uint8_t)g_mpsk[s - MPSK_BASE].port, 169, 254, 1, 1};
    if (a && n) { memcpy(a, sa, *n < 16 ? *n : 16); *n = 16; }
    return 0;
}
int mpsk_fcntl(int s, int cmd, int v) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? (int)mpsk_ret(coop_vnet_fcntl(c, s, cmd, v)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    if (cmd == 3) return 2 | (g_mpsk[s - MPSK_BASE].nb ? 4 : 0);   // F_GETFL: O_RDWR | O_NONBLOCK (FreeBSD values)
    if (cmd == 4) { g_mpsk[s - MPSK_BASE].nb = !!(v & 4); LOG("[mp] sock: Fcntl(%#x, F_SETFL, %#x)", s, v); }
    return 0;
}
long mpsk_sendto(int s, const void *b, unsigned long n, int f, const void *to, unsigned tl) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? mpsk_ret(coop_vnet_sendto(c, s, b, n, f, to, tl)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    int k = g_mpsk[s - MPSK_BASE].sends++;
    if (k < 8 || k % 200 == 0) {
        const uint8_t *p = to;
        LOG("[mp] sock: send #%d on %#x, %lu bytes to %u.%u.%u.%u:%d (dropped)", k, s, n, p ? p[4] : 0, p ? p[5] : 0, p ? p[6] : 0, p ? p[7] : 0, p ? p[2] << 8 | p[3] : 0);
    }
    return (long)n;
}
long mpsk_send(int s, const void *b, unsigned long n, int f) { return mpsk_sendto(s, b, n, f, NULL, 0); }   // no connected sockets: DestAddrRequired under coop
long mpsk_recvfrom(int s, void *b, unsigned long n, int f, void *from, unsigned *fl) {
    coop_node *c = dm_coop();
    if (c) return coop_vnet_is(c, s) ? mpsk_ret(coop_vnet_recvfrom(c, s, b, n, f, from, fl)) : sk_fail();
    if (!mpsk_fake(s)) return sk_fail();
    if (g_mpsk[s - MPSK_BASE].recvs++ == 0) LOG("[mp] sock: first receive on %#x -> EAGAIN%s", s, g_mpsk[s - MPSK_BASE].nb ? "" : " (blocking socket answered as if non-blocking)");
    return mpsk_err(MPSK_EAGAIN);
}
long mpsk_recv(int s, void *b, unsigned long n, int f) { return mpsk_recvfrom(s, b, n, f, NULL, NULL); }
