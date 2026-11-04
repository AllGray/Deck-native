// coop-relay: a meeting point for Pokopia Link Play over the internet, for players who cannot forward a port (docs/coop-step1d.md). MIT, our own code.
// The host and its visitors all dial out to it. It keeps rooms keyed by a 16-byte room id the players derive from their room code (src/coop
// coop_auth.c): it never sees the code or any key. A host keeps a control link open for its room; a visitor asks for the room, the relay tells the
// host, the host dials one more link for that visitor, and the relay pairs the two and copies bytes between them without reading them. Everything
// inside is the players' own authenticated protocol, so the relay cannot forge or change game messages; it CAN read them (nothing is encrypted).
// Treat the relay as untrusted: it only routes. Limits on rooms, links, addresses, bytes and time; rooms expire.
// Protocol (the same values as src/coop/coop_int.h RLY_*): a link's first 32 bytes are u32 magic "PKR1", u8 kind (1 host, 2 join, 3 attach), u8
// version 1, 2 reserved, room id[16], u32 visitor id, u32 cookie. The relay answers in 16-byte messages: u32 magic, u8 type (1 ok, 2 visitor,
// 3 gone, 4 error, 5 pong), u8 error code, 2 reserved, u32 arg (a visitor id), u32 cookie. A host's control link may send pings (type 6).
// Portable C11: BSD sockets (macOS, Linux) or Winsock (Windows); one thread; poll() / WSAPoll().
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define COOP_RELAY_PORT 47610

typedef struct coop_relay coop_relay;
typedef struct coop_relay_cfg {
    uint32_t ip; uint16_t port;   // where it listens, host order: 127.0.0.1:47610 by default (another address is the operator's choice)
    int max_rooms;                // rooms open at once (64)
    int max_peers;                // visitors in one room, waiting or paired (7: Link Play has at most 8 players)
    int max_conns;                // links in all (256)
    int max_per_ip;               // links from one address (24)
    int new_per_ip;               // new links a second from one address (10; bursts twice that)
    int nofind_per_min;           // requests naming no open room or visitor, per address a minute (60); beyond, refused with "limit"
    int64_t hello_ms;             // a new link says what it is within this (5 s)
    int64_t wait_ms;              // a visitor waits this long for its host to dial in (10 s)
    int64_t idle_ms;              // a host's control link silent this long closes its room (30 s; hosts ping every 5 s)
    int64_t room_ms;              // a room lives at most this long (12 h), then it closes with "expired"
    uint64_t room_bytes;          // and carries at most this many bytes, both ways, all its links (4 GiB)
    uint32_t link_bps;            // bytes a second one link may send (256 KiB; bursts twice that); beyond, the relay stops reading it for a moment
    void (*log)(void *user, const char *line); void *user;   // one line without a newline; NULL: silent
} coop_relay_cfg;
typedef struct coop_relay_stats {
    int rooms, links, waiting, paired;   // now
    uint64_t rooms_opened, visitors_paired, bytes;
    uint64_t refused;             // links refused by a limit (rooms, links, addresses, rates, a full room)
    uint64_t not_found;           // joins and attaches naming no open room or waiting visitor
    uint64_t bad;                 // malformed or late first messages
    uint64_t expired;             // rooms closed by age, bytes or a silent host
} coop_relay_stats;

void coop_relay_defaults(coop_relay_cfg *c);
// Listens (non-blocking); NULL and err filled on failure. The port actually bound: coop_relay_port (0 in cfg lets the system pick)
coop_relay *coop_relay_create(const coop_relay_cfg *c, char *err, size_t errn);
uint16_t coop_relay_port(const coop_relay *r);
// One round: waits up to ms for traffic, moves bytes, runs the timers. 0, or -1 when polling failed
int coop_relay_step(coop_relay *r, int ms);
void coop_relay_get_stats(const coop_relay *r, coop_relay_stats *s);
void coop_relay_destroy(coop_relay *r);   // closes every link

#ifdef __cplusplus
}
#endif
