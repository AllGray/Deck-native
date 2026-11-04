int dm_sk_poll(void *fds, uint64_t n, int ms) {   // also hle_offline.c's plain pollfd form
    sk_pollfd *p = fds;
    int ready = 0, virt = 0;
    for (uint64_t i = 0; i < n; i++) if (p[i].fd >= 0) { if (!mpsk_is(p[i].fd)) return sk_fail(); virt = 1; }
    coop_node *c = dm_coop();
    if (c && virt) return (int)mpsk_ret(coop_vnet_poll(c, (coop_pollfd *)fds, n, ms));   // research copy: readable when a datagram waits, always writable
    for (uint64_t i = 0; i < n; i++) { p[i].revents = mpsk_is(p[i].fd) ? p[i].events & 4 : 0; ready += p[i].revents != 0; }   // research copy (DM_MPFAKE): writable, never readable
    return ready ? ready : sk_idle(ms);
}
int dm_sk_select(int nfds, const uint64_t *r, const uint64_t *w, const uint64_t *e, const int64_t *tv) {   // fd_set: 1024 bits; timeval: {i64 seconds, i64 microseconds}
    const uint64_t *sets[3] = {r, w, e};
    int ready = 0, virt = 0;
    for (int k = 0; k < 3; k++) for (int fd = 0; sets[k] && fd < nfds && fd < 1024; fd++) if (sets[k][fd >> 6] >> (fd & 63) & 1) { if (!mpsk_is(fd)) return sk_fail(); virt = 1; }
    int64_t ms = tv ? tv[0] * 1000 + tv[1] / 1000 : -1;
    coop_node *c = dm_coop();
    if (c && virt) return (int)mpsk_ret(coop_vnet_select(c, nfds, (uint64_t *)r, (uint64_t *)w, (uint64_t *)e, ms > 0x7fffffff ? 0x7fffffff : (int)ms));
    for (int k = 0; k < 3; k++) for (int fd = 0; sets[k] && fd < nfds && fd < 1024; fd++) if (sets[k][fd >> 6] >> (fd & 63) & 1) {
        if (k == 1) ready++; else ((uint64_t *)sets[k])[fd >> 6] &= ~(1ull << (fd & 63));   // research copy (DM_MPFAKE): writable, never readable
    }
    return ready ? ready : sk_idle(ms);
}
