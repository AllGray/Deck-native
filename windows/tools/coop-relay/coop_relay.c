// coop-relay engine (MIT, our own code): rooms, pairing and the byte copy (coop_relay.h has the protocol). Every byte from the network is untrusted:
// a link's first message must be exactly the 32-byte hello within hello_ms; a host's control link may only send 16-byte pings; paired links are
// copied as they are, within a byte rate, a buffer bound and the room's byte budget, and never parsed.
#ifdef _WIN32
#ifndef _CRT_RAND_S
#define _CRT_RAND_S
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <stdint.h>
#include <stdlib.h>   // rand_s, after _CRT_RAND_S
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
typedef SOCKET rsock;
#define RS_BAD INVALID_SOCKET
typedef WSAPOLLFD rpollfd;
#define rpoll WSAPoll
static void rclose(rsock s) { if (s != RS_BAD) closesocket(s); }
static int rblock(void) { int e = WSAGetLastError(); return e == WSAEWOULDBLOCK || e == WSAEINTR; }
static void rnonblock(rsock s) { u_long on = 1; ioctlsocket(s, FIONBIO, &on); }
static int64_t rnow(void) { return (int64_t)GetTickCount64(); }
static uint32_t rrand(void) { unsigned v = 0; rand_s(&v); return v; }
#define RSEND_FLAGS 0
#else
#include <stdint.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
typedef int rsock;
#define RS_BAD (-1)
typedef struct pollfd rpollfd;
#define rpoll poll
static void rclose(rsock s) { if (s != RS_BAD) close(s); }
static int rblock(void) { return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR; }
static void rnonblock(rsock s) { fcntl(s, F_SETFL, fcntl(s, F_GETFL, 0) | O_NONBLOCK); }
static int64_t rnow(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000; }
#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__)
#include <stdlib.h>
static uint32_t rrand(void) { return arc4random(); }
#else
static uint32_t rrand(void) {
    uint32_t v = 0; int fd = open("/dev/urandom", O_RDONLY);
    if (fd >= 0) { if (read(fd, &v, sizeof v) != (ssize_t)sizeof v) v = 0; close(fd); }
    return v ^ (uint32_t)rnow();   // a cookie only stops blind attaches; the players' own handshake is what protects a link
}
#endif
#ifdef MSG_NOSIGNAL
#define RSEND_FLAGS MSG_NOSIGNAL
#else
#define RSEND_FLAGS 0
#endif
#endif
#include "coop_relay.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAGIC 0x31524B50u   // "PKR1"
#define HELLO_LEN 32
#define MSG_LEN 16
enum { K_HOST = 1, K_JOIN, K_ATTACH };
enum { T_OK = 1, T_VISITOR, T_GONE, T_ERR, T_PONG, T_PING };
enum { E_NOROOM = 1, E_TAKEN, E_FULL, E_LIMIT, E_TIMEOUT, E_BAD, E_EXPIRED };
#define OUT_MAX (256u << 10)   // bytes waiting for a slow reader; beyond, the other side is not read
#define READ_MAX (16u << 10)
#define CLOSE_FLUSH_MS 2000
#define IPS 256

enum { C_FREE, C_NEW, C_HOST, C_WAIT, C_PAIR };
typedef struct rconn {
    int st; rsock s; uint32_t ip; uint16_t port;
    uint8_t in[HELLO_LEN]; int inn;   // the first message; on a host's control link, a partial ping
    uint8_t *out; size_t outn, outcap;
    int pair, room, visitor;          // C_PAIR: the other link, and whether this is the visitor's side; C_HOST / C_WAIT / C_PAIR: the room
    uint32_t vid, cookie;             // C_WAIT: the visitor's id and the cookie its host must name
    int64_t opened, last_rx, deadline;
    int64_t tok, tok_at;              // bytes it may still send, refilled at link_bps
    int closing; int64_t close_by;    // 1: flush what is queued for it, then close
} rconn;
typedef struct rroom { int used; uint8_t id[16]; int host; int64_t created; uint64_t bytes; uint32_t next_vid; } rroom;
typedef struct rip { uint32_t ip; int conns; int64_t ntok, nat, ftok, fat, seen; } rip;
struct coop_relay {
    coop_relay_cfg c;
    rsock ls; uint16_t port;
    rconn *k; rroom *rm; rip ips[IPS];
    rpollfd *pf; int *pi;
    coop_relay_stats st;
};

static void rlog(coop_relay *r, const char *fmt, ...) {
    if (!r->c.log) return;
    char b[400]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    r->c.log(r->c.user, b);
}
static void put32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static uint32_t get32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void ipstr(char *o, uint32_t ip) { snprintf(o, 16, "%u.%u.%u.%u", ip >> 24, ip >> 16 & 0xFF, ip >> 8 & 0xFF, ip & 0xFF); }
static void roomstr(char *o, const uint8_t *id) { snprintf(o, 12, "%02x%02x%02x%02x", id[0], id[1], id[2], id[3]); }   // the first 4 bytes, for logs

void coop_relay_defaults(coop_relay_cfg *c) {
    memset(c, 0, sizeof *c);
    c->ip = 0x7F000001u; c->port = COOP_RELAY_PORT;
    c->max_rooms = 64; c->max_peers = 7; c->max_conns = 256; c->max_per_ip = 24; c->new_per_ip = 10; c->nofind_per_min = 60;
    c->hello_ms = 5000; c->wait_ms = 10000; c->idle_ms = 30000; c->room_ms = 12ll * 3600 * 1000;
    c->room_bytes = 4ull << 30; c->link_bps = 256u << 10;
}

// ---------- addresses: links, new links a second and failed lookups a minute per address ----------
static rip *ip_get(coop_relay *r, uint32_t ip, int64_t now) {
    rip *free_ = NULL;
    for (int i = 0; i < IPS; i++) {
        if (r->ips[i].ip == ip && (r->ips[i].conns || r->ips[i].seen)) return &r->ips[i];
        if (!r->ips[i].conns && (!free_ || r->ips[i].seen < free_->seen)) free_ = &r->ips[i];
    }
    if (!free_) return NULL;   // 256 addresses each holding links: refused
    memset(free_, 0, sizeof *free_); free_->ip = ip; free_->seen = now;
    free_->ntok = 2000ll * r->c.new_per_ip; free_->nat = now; free_->ftok = 1000ll * r->c.nofind_per_min; free_->fat = now;
    return free_;
}
static int ip_new_ok(coop_relay *r, rip *a, int64_t now) {   // thousandths of a link: rate a second, burst twice that
    a->ntok += (now - a->nat) * r->c.new_per_ip; a->nat = now;
    if (a->ntok > 2000ll * r->c.new_per_ip) a->ntok = 2000ll * r->c.new_per_ip;
    if (a->ntok < 1000) return 0;
    a->ntok -= 1000; return 1;
}
static int ip_nofind_ok(coop_relay *r, rip *a, int64_t now) {   // thousandths of a request: per minute, burst as much
    int64_t add = (now - a->fat) * r->c.nofind_per_min / 60;
    if (add > 0) { a->ftok += add; a->fat = now; if (a->ftok > 1000ll * r->c.nofind_per_min) a->ftok = 1000ll * r->c.nofind_per_min; }
    if (a->ftok < 1000) return 0;
    a->ftok -= 1000; return 1;
}

// ---------- links ----------
static int out_add(rconn *k, const void *p, size_t n) {
    if (k->outn + n > k->outcap) {
        size_t nc = k->outcap ? k->outcap : 4096; while (nc < k->outn + n) nc *= 2;
        uint8_t *q = realloc(k->out, nc); if (!q) return -1;
        k->out = q; k->outcap = nc;
    }
    memcpy(k->out + k->outn, p, n); k->outn += n;
    return 0;
}
static void msg(rconn *k, int type, int code, uint32_t arg, uint32_t cookie) {
    uint8_t m[MSG_LEN] = {0};
    put32(m, MAGIC); m[4] = (uint8_t)type; m[5] = (uint8_t)code; put32(m + 8, arg); put32(m + 12, cookie);
    out_add(k, m, sizeof m);
}
static void closing(rconn *k, int64_t now) { if (k->st != C_FREE && !k->closing) { k->closing = 1; k->close_by = now + CLOSE_FLUSH_MS; } }
static void refuse(coop_relay *r, rconn *k, int code, int64_t now) { msg(k, T_ERR, code, 0, 0); closing(k, now); (void)r; }
static void room_close(coop_relay *r, int ri, const char *why, int code, int64_t now);
static void conn_free(coop_relay *r, int i, int64_t now) {   // closes a link now; what depended on it follows
    rconn *k = &r->k[i];
    if (k->st == C_FREE) return;
    int st = k->st, room = k->room, pair = k->pair;
    rclose(k->s);
    for (int j = 0; j < IPS; j++) if (r->ips[j].ip == k->ip && r->ips[j].conns) { r->ips[j].conns--; r->ips[j].seen = now; break; }
    free(k->out);
    memset(k, 0, sizeof *k); k->s = RS_BAD; k->pair = k->room = -1;
    if (st == C_HOST && room >= 0 && r->rm[room].used && r->rm[room].host == i) room_close(r, room, "its host left", 0, now);
    else if (st == C_WAIT && room >= 0 && r->rm[room].used && r->rm[room].host >= 0) msg(&r->k[r->rm[room].host], T_GONE, 0, 0, 0);
    else if (st == C_PAIR && pair >= 0 && r->k[pair].st == C_PAIR) { r->k[pair].pair = -1; closing(&r->k[pair], now); }
}
static void room_close(coop_relay *r, int ri, const char *why, int code, int64_t now) {
    rroom *m = &r->rm[ri];
    if (!m->used) return;
    char id[12]; roomstr(id, m->id);
    rlog(r, "room %s closed: %s (open %lld s, %llu bytes)", id, why, (long long)((now - m->created) / 1000), (unsigned long long)m->bytes);
    m->used = 0;
    for (int i = 0; i < r->c.max_conns; i++) {
        rconn *k = &r->k[i];
        if (k->st == C_FREE || k->room != ri) continue;
        k->room = -1;
        if (k->st == C_HOST) { if (code) msg(k, T_ERR, code, 0, 0); closing(k, now); }
        else if (k->st == C_WAIT) refuse(r, k, E_NOROOM, now);
        else closing(k, now);   // paired links of this room
    }
    m->host = -1;
}
static int room_find(coop_relay *r, const uint8_t *id) {
    for (int i = 0; i < r->c.max_rooms; i++) if (r->rm[i].used && !memcmp(r->rm[i].id, id, 16)) return i;
    return -1;
}
static int room_visitors(coop_relay *r, int ri) {
    int v = 0;
    for (int i = 0; i < r->c.max_conns; i++) v += r->k[i].room == ri && (r->k[i].st == C_WAIT || (r->k[i].st == C_PAIR && r->k[i].visitor));
    return v;
}
static void hello(coop_relay *r, int i, int64_t now) {   // the 32-byte first message is complete
    rconn *k = &r->k[i]; char a[16], id[12];
    const uint8_t *h = k->in;
    ipstr(a, k->ip); roomstr(id, h + 8);
    rip *ad = ip_get(r, k->ip, now);
    if (get32(h) != MAGIC || h[5] != 1 || h[4] < K_HOST || h[4] > K_ATTACH) { r->st.bad++; refuse(r, k, E_BAD, now); return; }
    int ri = room_find(r, h + 8);
    if (h[4] == K_HOST) {
        if (ri >= 0) { r->st.refused++; refuse(r, k, E_TAKEN, now); rlog(r, "%s asked for room %s, which another host holds", a, id); return; }
        for (int j = 0; j < r->c.max_rooms && ri < 0; j++) if (!r->rm[j].used) ri = j;
        if (ri < 0) { r->st.refused++; refuse(r, k, E_LIMIT, now); rlog(r, "%s refused: %d rooms are open", a, r->c.max_rooms); return; }
        rroom *m = &r->rm[ri];
        memset(m, 0, sizeof *m); m->used = 1; memcpy(m->id, h + 8, 16); m->host = i; m->created = now;
        k->st = C_HOST; k->room = ri; k->inn = 0;
        msg(k, T_OK, 0, 0, 0);
        r->st.rooms_opened++;
        rlog(r, "room %s open (host %s:%u)", id, a, k->port);
        return;
    }
    if (ri < 0 || r->rm[ri].host < 0) {
        r->st.not_found++;
        refuse(r, k, ad && ip_nofind_ok(r, ad, now) ? E_NOROOM : E_LIMIT, now);
        return;
    }
    rroom *m = &r->rm[ri];
    if (h[4] == K_JOIN) {
        if (room_visitors(r, ri) >= r->c.max_peers) { r->st.refused++; refuse(r, k, E_FULL, now); rlog(r, "%s refused: room %s has %d visitors", a, id, r->c.max_peers); return; }
        k->st = C_WAIT; k->room = ri; k->vid = ++m->next_vid; k->cookie = rrand(); k->deadline = now + r->c.wait_ms;
        msg(&r->k[m->host], T_VISITOR, 0, k->vid, k->cookie);
        rlog(r, "visitor %u (%s) waits for room %s", k->vid, a, id);
        return;
    }
    // attach: the host's link for one waiting visitor
    uint32_t vid = get32(h + 24), cookie = get32(h + 28);
    int w = -1;
    for (int j = 0; j < r->c.max_conns && w < 0; j++) if (r->k[j].st == C_WAIT && r->k[j].room == ri && r->k[j].vid == vid && r->k[j].cookie == cookie) w = j;
    if (w < 0) { r->st.not_found++; refuse(r, k, ad && ip_nofind_ok(r, ad, now) ? E_NOROOM : E_LIMIT, now); return; }
    k->st = C_PAIR; k->room = ri; k->pair = w; k->visitor = 0; k->tok = r->c.link_bps; k->tok_at = now;
    rconn *v = &r->k[w];
    v->st = C_PAIR; v->pair = i; v->visitor = 1; v->tok = r->c.link_bps; v->tok_at = now;
    msg(v, T_OK, 0, vid, 0);   // the visitor speaks to its host from here on
    r->st.visitors_paired++;
    rlog(r, "visitor %u paired with room %s's host", vid, id);
}
static void host_ctl(coop_relay *r, int i, int64_t now) {   // a host's control link: 16-byte pings only
    rconn *k = &r->k[i];
    while (k->inn >= MSG_LEN) {
        if (get32(k->in) != MAGIC) { r->st.bad++; conn_free(r, i, now); return; }
        if (k->in[4] == T_PING) msg(k, T_PONG, 0, 0, 0);
        memmove(k->in, k->in + MSG_LEN, (size_t)(k->inn - MSG_LEN)); k->inn -= MSG_LEN;
    }
}
static void conn_read(coop_relay *r, int i, int64_t now) {
    rconn *k = &r->k[i];
    if (k->st == C_NEW || k->st == C_HOST) {
        int want = k->st == C_NEW ? HELLO_LEN - k->inn : MSG_LEN - k->inn;
        int n = (int)recv(k->s, (char *)k->in + k->inn, want, 0);
        if (n == 0 || (n < 0 && !rblock())) { conn_free(r, i, now); return; }
        if (n < 0) return;
        k->inn += n; k->last_rx = now;
        if (k->st == C_NEW && k->inn == HELLO_LEN) hello(r, i, now);
        else if (k->st == C_HOST) host_ctl(r, i, now);
        return;
    }
    if (k->st == C_WAIT) {   // a waiting visitor has nothing to say until it is paired
        uint8_t b[64]; int n = (int)recv(k->s, (char *)b, sizeof b, 0);
        if (n == 0 || (n < 0 && !rblock())) { conn_free(r, i, now); return; }
        if (n > 0) { r->st.bad++; refuse(r, k, E_BAD, now); }
        return;
    }
    if (k->st != C_PAIR || k->pair < 0) return;
    rconn *o = &r->k[k->pair];
    k->tok += (now - k->tok_at) * (int64_t)r->c.link_bps / 1000; k->tok_at = now;
    if (k->tok > 2 * (int64_t)r->c.link_bps) k->tok = 2 * (int64_t)r->c.link_bps;
    size_t room = OUT_MAX > o->outn ? OUT_MAX - o->outn : 0, want = READ_MAX;
    if (want > room) want = room;
    if (k->tok < (int64_t)want) want = k->tok > 0 ? (size_t)k->tok : 0;
    if (!want) return;
    if (o->outn + want > o->outcap) {
        size_t nc = o->outcap ? o->outcap : 4096; while (nc < o->outn + want) nc *= 2;
        uint8_t *q = realloc(o->out, nc); if (!q) { conn_free(r, i, now); return; }
        o->out = q; o->outcap = nc;
    }
    int n = (int)recv(k->s, (char *)o->out + o->outn, (int)want, 0);
    if (n == 0 || (n < 0 && !rblock())) { conn_free(r, i, now); return; }
    if (n < 0) return;
    o->outn += (size_t)n; k->tok -= n; k->last_rx = now;
    r->st.bytes += (uint64_t)n;
    if (k->room >= 0 && r->rm[k->room].used) {
        rroom *m = &r->rm[k->room];
        m->bytes += (uint64_t)n;
        if (m->bytes > r->c.room_bytes) { r->st.expired++; room_close(r, k->room, "its byte budget is spent", E_EXPIRED, now); }
    }
}
static void conn_write(coop_relay *r, int i, int64_t now) {
    rconn *k = &r->k[i];
    while (k->outn) {
        int n = (int)send(k->s, (const char *)k->out, (int)(k->outn > 65536 ? 65536 : k->outn), RSEND_FLAGS);
        if (n < 0 && rblock()) return;
        if (n <= 0) { conn_free(r, i, now); return; }
        memmove(k->out, k->out + n, k->outn - (size_t)n); k->outn -= (size_t)n;
    }
}
static void accept_all(coop_relay *r, int64_t now) {
    for (int round = 0; round < 64; round++) {
        struct sockaddr_in sa; socklen_t sl = sizeof sa;
        rsock s = accept(r->ls, (struct sockaddr *)&sa, &sl);
        if (s == RS_BAD) return;
        uint32_t ip = sa.sin_family == AF_INET ? ntohl(sa.sin_addr.s_addr) : 0;
        int slot = -1, total = 0;
        for (int i = 0; i < r->c.max_conns; i++) { if (r->k[i].st == C_FREE) { if (slot < 0) slot = i; } else total++; }
        rip *a = ip_get(r, ip, now);
        const char *why = slot < 0 ? "every link slot is taken" : !a ? "too many addresses hold links" : a->conns >= r->c.max_per_ip ? "too many links from it" :
                          !ip_new_ok(r, a, now) ? "too many new links a second from it" : NULL;
        if (why) {
            r->st.refused++; rclose(s);
            if (r->st.refused <= 8 || r->st.refused % 100 == 0) { char t[16]; ipstr(t, ip); rlog(r, "a link from %s refused: %s (%llu refused so far)", t, why, (unsigned long long)r->st.refused); }
            continue;
        }
        (void)total;
        rnonblock(s);
        int one = 1; setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof one);
#ifdef SO_NOSIGPIPE
        setsockopt(s, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
        rconn *k = &r->k[slot];
        memset(k, 0, sizeof *k);
        k->st = C_NEW; k->s = s; k->ip = ip; k->port = ntohs(sa.sin_port); k->pair = k->room = -1; k->opened = k->last_rx = now;
        a->conns++; a->seen = now;
    }
}
static void timers(coop_relay *r, int64_t now) {
    for (int i = 0; i < r->c.max_conns; i++) {
        rconn *k = &r->k[i];
        if (k->st == C_FREE) continue;
        if (k->closing) { if (!k->outn || now >= k->close_by) conn_free(r, i, now); continue; }
        if (k->st == C_NEW && now - k->opened >= r->c.hello_ms) { r->st.bad++; conn_free(r, i, now); }
        else if (k->st == C_WAIT && now >= k->deadline) { r->st.expired++; refuse(r, k, E_TIMEOUT, now); }
        else if (k->st == C_HOST && now - k->last_rx >= r->c.idle_ms && k->room >= 0) { r->st.expired++; room_close(r, k->room, "its host went silent", 0, now); }
    }
    for (int i = 0; i < r->c.max_rooms; i++)
        if (r->rm[i].used && now - r->rm[i].created >= r->c.room_ms) { r->st.expired++; room_close(r, i, "it reached its age limit", E_EXPIRED, now); }
}

coop_relay *coop_relay_create(const coop_relay_cfg *c, char *err, size_t errn) {
#ifdef _WIN32
    WSADATA w; if (WSAStartup(MAKEWORD(2, 2), &w)) { snprintf(err, errn, "WSAStartup failed"); return NULL; }
#endif
    coop_relay *r = calloc(1, sizeof *r);
    if (!r) {
        snprintf(err, errn, "out of memory");
#ifdef _WIN32
        WSACleanup();
#endif
        return NULL;
    }
    r->c = *c;
    if (r->c.max_conns < 4) r->c.max_conns = 4;
    if (r->c.max_rooms < 1) r->c.max_rooms = 1;
    r->k = calloc((size_t)r->c.max_conns, sizeof *r->k); r->rm = calloc((size_t)r->c.max_rooms, sizeof *r->rm);
    r->pf = calloc((size_t)r->c.max_conns + 1, sizeof *r->pf); r->pi = calloc((size_t)r->c.max_conns + 1, sizeof *r->pi);
    if (!r->k || !r->rm || !r->pf || !r->pi) { snprintf(err, errn, "out of memory"); r->ls = RS_BAD; coop_relay_destroy(r); return NULL; }
    for (int i = 0; i < r->c.max_conns; i++) { r->k[i].s = RS_BAD; r->k[i].pair = r->k[i].room = -1; }
    for (int i = 0; i < r->c.max_rooms; i++) r->rm[i].host = -1;
    r->ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (r->ls == RS_BAD) { snprintf(err, errn, "socket failed"); coop_relay_destroy(r); return NULL; }
    int one = 1;
#ifdef _WIN32
    setsockopt(r->ls, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char *)&one, sizeof one);
#else
    setsockopt(r->ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
#endif
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = htons(c->port); sa.sin_addr.s_addr = htonl(c->ip);
    if (bind(r->ls, (struct sockaddr *)&sa, sizeof sa) || listen(r->ls, 64)) {
        char a[16]; ipstr(a, c->ip); snprintf(err, errn, "cannot listen on %s:%u", a, c->port); coop_relay_destroy(r); return NULL;
    }
    socklen_t sl = sizeof sa; getsockname(r->ls, (struct sockaddr *)&sa, &sl); r->port = ntohs(sa.sin_port);
    rnonblock(r->ls);
    return r;
}
uint16_t coop_relay_port(const coop_relay *r) { return r->port; }
int coop_relay_step(coop_relay *r, int ms) {
    int n = 0, throttled = 0;
    r->pf[n].fd = r->ls; r->pf[n].events = POLLIN; r->pf[n].revents = 0; r->pi[n++] = -1;   // with every slot taken, accepted and refused at once
    int64_t now = rnow();
    for (int i = 0; i < r->c.max_conns; i++) {
        rconn *k = &r->k[i];
        if (k->st == C_FREE) continue;
        short ev = 0;
        if (!k->closing) {
            if (k->st != C_PAIR) ev |= POLLIN;
            else if (k->pair >= 0 && r->k[k->pair].outn < OUT_MAX) {
                int64_t tok = k->tok + (now - k->tok_at) * (int64_t)r->c.link_bps / 1000;
                if (tok > 0) ev |= POLLIN; else throttled = 1;   // out of bytes for now: not read until the rate refills
            }
        }
        if (k->outn) ev |= POLLOUT;
        r->pf[n].fd = k->s; r->pf[n].events = ev; r->pf[n].revents = 0; r->pi[n++] = i;
    }
    int got = rpoll(r->pf, (unsigned)n, throttled && ms > 10 ? 10 : ms);
    if (got < 0) {
#ifndef _WIN32
        if (errno == EINTR) return 0;
#endif
        return -1;
    }
    now = rnow();
    for (int j = 0; j < n; j++) {
        if (!r->pf[j].revents) continue;
        int i = r->pi[j];
        if (i < 0) { accept_all(r, now); continue; }
        rconn *k = &r->k[i];
        if (k->st == C_FREE || k->s != r->pf[j].fd) continue;
        if (r->pf[j].revents & (POLLIN | POLLERR | POLLHUP)) conn_read(r, i, now);
        if (k->st != C_FREE && (r->pf[j].revents & POLLOUT)) conn_write(r, i, now);
    }
    for (int i = 0; i < r->c.max_conns; i++) if (r->k[i].st != C_FREE && r->k[i].outn) conn_write(r, i, now);   // what this round queued goes out now
    timers(r, now);
    return 0;
}
void coop_relay_get_stats(const coop_relay *r, coop_relay_stats *s) {
    *s = r->st; s->rooms = s->links = s->waiting = s->paired = 0;
    for (int i = 0; i < r->c.max_rooms; i++) s->rooms += r->rm[i].used;
    for (int i = 0; i < r->c.max_conns; i++) { s->links += r->k[i].st != C_FREE; s->waiting += r->k[i].st == C_WAIT; s->paired += r->k[i].st == C_PAIR; }
}
void coop_relay_destroy(coop_relay *r) {
    if (!r) return;
    if (r->k) for (int i = 0; i < r->c.max_conns; i++) if (r->k[i].st != C_FREE) { rclose(r->k[i].s); free(r->k[i].out); }
    rclose(r->ls);
    free(r->k); free(r->rm); free(r->pf); free(r->pi); free(r);
#ifdef _WIN32
    WSACleanup();
#endif
}
