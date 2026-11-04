// coop-relay: the command (MIT, our own code). Build: cc -O2 -o coop-relay main.c coop_relay.c (macOS, Linux); on Windows with clang:
// clang -O2 -o coop-relay.exe main.c coop_relay.c -lws2_32 (or CMakeLists.txt here). Run: coop-relay [options]; Ctrl-C stops it.
//   --listen <IPv4>[:<port>]  where to listen (127.0.0.1:47610). Players elsewhere need an address they can reach: that is the operator's choice
//   --rooms N --peers N --links N --per-ip N --new-per-ip N --nofind-per-min N   limits (64, 7, 256, 24, 10, 60)
//   --room-hours H --room-mib M --link-kib K --wait-s S --idle-s S                 a room's age and bytes, a link's rate, waits (12, 4096, 256, 10, 30)
//   --stats S  a line with the counters every S seconds (0: never; default 60)    --quiet  only the start and stop lines
// The relay keeps nothing on disk and has no accounts. It cannot read room codes or forge game messages (the players' links are authenticated end
// to end), but it can read the game's traffic, which is not encrypted (docs/coop-step1d.md).
#include "coop_relay.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static volatile sig_atomic_t g_stop;
static void on_signal(int s) { (void)s; g_stop = 1; }
static int g_quiet;
static void out(void *u, const char *l) {
    (void)u; if (g_quiet) return;
    time_t t = time(NULL); struct tm tm;
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    printf("%04d-%02d-%02d %02d:%02d:%02d %s\n", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, l);
    fflush(stdout);
}
static int parse_addr(const char *s, uint32_t *ip, uint16_t *port) {
    unsigned a, b, c, d, p = *port; char tail;
    int n = sscanf(s, "%u.%u.%u.%u:%u%c", &a, &b, &c, &d, &p, &tail);
    if (n != 4 && n != 5) return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255 || p > 65535) return -1;
    *ip = a << 24 | b << 16 | c << 8 | d; *port = (uint16_t)p;
    return 0;
}
static void usage(void) {
    fprintf(stderr, "coop-relay [--listen <IPv4>[:<port>]] [--rooms N] [--peers N] [--links N] [--per-ip N] [--new-per-ip N] [--nofind-per-min N]\n"
                    "           [--room-hours H] [--room-mib M] [--link-kib K] [--wait-s S] [--idle-s S] [--stats S] [--quiet]\n"
                    "A meeting point for Pokopia Link Play over the internet (docs/coop-step1d.md). Listens on 127.0.0.1:47610 by default.\n");
}
int main(int argc, char **argv) {
    coop_relay_cfg c; coop_relay_defaults(&c);
    long stats_s = 60;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        long x = v ? strtol(v, NULL, 10) : 0;
        if (!strcmp(a, "--quiet")) { g_quiet = 1; continue; }
        if (!strcmp(a, "-h") || !strcmp(a, "--help")) { usage(); return 0; }
        if (!v) { usage(); return 2; }
        i++;
        if (!strcmp(a, "--listen")) { if (parse_addr(v, &c.ip, &c.port)) { fprintf(stderr, "--listen %s: <IPv4>[:<port>] wanted\n", v); return 2; } }
        else if (x < 0) { fprintf(stderr, "%s %s: a number >= 0 wanted\n", a, v); return 2; }
        else if (!strcmp(a, "--rooms")) c.max_rooms = (int)x;
        else if (!strcmp(a, "--peers")) c.max_peers = (int)x;
        else if (!strcmp(a, "--links")) c.max_conns = (int)x;
        else if (!strcmp(a, "--per-ip")) c.max_per_ip = (int)x;
        else if (!strcmp(a, "--new-per-ip")) c.new_per_ip = (int)x;
        else if (!strcmp(a, "--nofind-per-min")) c.nofind_per_min = (int)x;
        else if (!strcmp(a, "--room-hours")) c.room_ms = (int64_t)x * 3600 * 1000;
        else if (!strcmp(a, "--room-mib")) c.room_bytes = (uint64_t)x << 20;
        else if (!strcmp(a, "--link-kib")) c.link_bps = (uint32_t)x << 10;
        else if (!strcmp(a, "--wait-s")) c.wait_ms = (int64_t)x * 1000;
        else if (!strcmp(a, "--idle-s")) c.idle_ms = (int64_t)x * 1000;
        else if (!strcmp(a, "--stats")) stats_s = x;
        else { usage(); return 2; }
    }
    c.log = out;
    char err[200];
    coop_relay *r = coop_relay_create(&c, err, sizeof err);
    if (!r) { fprintf(stderr, "coop-relay: %s\n", err); return 1; }
    int q = g_quiet; g_quiet = 0;
    char line[200];
    snprintf(line, sizeof line, "listening on %u.%u.%u.%u:%u (rooms %d, visitors a room %d, links %d)", c.ip >> 24, c.ip >> 16 & 0xFF, c.ip >> 8 & 0xFF, c.ip & 0xFF,
             coop_relay_port(r), c.max_rooms, c.max_peers, c.max_conns);
    out(NULL, line);
    if (c.ip >> 24 != 127)
        out(NULL, "not loopback: anyone who can reach this address can open rooms here; rooms are authenticated end to end by the players, but their traffic is not encrypted");
    g_quiet = q;
    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);
    time_t last = time(NULL);
    while (!g_stop) {
        if (coop_relay_step(r, 200) < 0) { fprintf(stderr, "coop-relay: polling failed\n"); break; }
        if (stats_s > 0 && time(NULL) - last >= stats_s) {
            coop_relay_stats s; coop_relay_get_stats(r, &s); last = time(NULL);
            snprintf(line, sizeof line, "rooms %d, links %d (%d waiting, %d paired); since start: %llu rooms, %llu visitors, %llu bytes, %llu refused, %llu not found, "
                     "%llu bad, %llu expired", s.rooms, s.links, s.waiting, s.paired, (unsigned long long)s.rooms_opened, (unsigned long long)s.visitors_paired,
                     (unsigned long long)s.bytes, (unsigned long long)s.refused, (unsigned long long)s.not_found, (unsigned long long)s.bad, (unsigned long long)s.expired);
            out(NULL, line);
        }
    }
    coop_relay_destroy(r);
    g_quiet = 0; out(NULL, "stopped");
    return 0;
}
