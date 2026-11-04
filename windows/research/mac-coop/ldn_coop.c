// ---------- ldn: local wireless. Cut (initialization fails, state "none"), except in this research copy: DM_COOP routes every call to the coop module (Link Play
// between instances, windows-native src/coop, copied in as hle_coop_*.c); DM_MPFAKE=1 without DM_COOP keeps step 0's lone access point (OpenAccessPoint /
// CreateNetwork / GetNetworkInfo / GetIpv4Address succeed, nobody ever joins) and an empty scan ----------
#include "coop.h"
#include <stdatomic.h>
static int mpfake(void) { static int f = -1; if (f < 0) { const char *e = getenv("DM_MPFAKE"); f = e ? atoi(e) : 0; } return f; }
static void *_Atomic g_ldn_attached;   // the game's own state-change event (AttachStateChangeEvent; Pokopia polls instead)
static void coop_hlog(void *u, const char *l) { (void)u; LOG("[coop] %s", l); }
static void coop_hsleep(void *u, int64_t ns) { (void)u; void dm_guest_sleep_ns(int64_t ns); dm_guest_sleep_ns(ns); }
static void coop_hstate(void *u, int st) { (void)u; (void)st; void *e = atomic_load(&g_ldn_attached); if (e) hle_signal_bound_system_event(e); hle_signal_bound_system_event(g_ev_ldn); }
coop_node *dm_coop(void) {   // the process's coop node with DM_COOP set, else NULL (hle_net.c asks too)
    static coop_node *_Atomic node; static _Atomic int done;
    if (atomic_load(&done)) return atomic_load(&node);
    static const coop_hooks h = {NULL, coop_hlog, coop_hsleep, coop_hstate};
    coop_node *n = coop_env_node(&h);
    atomic_store(&node, n); atomic_store(&done, 1);
    return n;
}
enum { LS_NONE, LS_INIT, LS_AP, LS_APC, LS_STA, LS_STAC };
static int g_ls;
static uint8_t g_lcfg[0x20], g_luser[0x30], g_ladv[0x180]; static uint16_t g_ladvn;
#define LDN_INVALID_STATE 0x40CBu
#define LDN_CONNECT_NOT_FOUND 0x82CBu
#define MP_IP 0xA9FE0101u   // 169.254.1.1 (Horizon keeps it as a host-order u32)
static Result ls_move(int from_mask, int to) {
    if (!(from_mask >> g_ls & 1)) { LOG("[mp] ldn: invalid state %d", g_ls); return LDN_INVALID_STATE; }
    LOG("[mp] ldn: state %d -> %d", g_ls, to); g_ls = to; hle_signal_bound_system_event(g_ev_ldn); return RES_OK;
}
#define COOP(call) do { coop_node *c_ = dm_coop(); if (c_) return call; } while (0)
HLE_FORCE("_ZN2nn3ldn10InitializeEv", "local wireless: the coop module (DM_COOP) or step 0's lone access point (DM_MPFAKE), else cut: initialization fails", Result, h_ldn_Init, void) {
    COOP(coop_ldn_initialize(c_)); if (!mpfake()) return RES_FAIL; g_ls = LS_INIT; return RES_OK;
}
HLE_FORCE("_ZN2nn3ldn8FinalizeEv", "local wireless (coop / cut)", void, h_ldn_Fini, void) { coop_node *c = dm_coop(); if (c) { coop_ldn_finalize(c); return; } g_ls = LS_NONE; }
HLE_FORCE("_ZN2nn3ldn8GetStateEv", "local wireless (coop / cut): state 0 (none)", int, h_ldn_State, void) { COOP(coop_ldn_state(c_)); return mpfake() ? g_ls : 0; }
HLE_FORCE("_ZN2nn3ldn22AttachStateChangeEventEPNS_2os15SystemEventTypeE", "local wireless state event: bound, fired by the coop module on every state or network change", void, h_ldn_Attach, void *ev) {
    hle_bind_system_event(ev, 0); atomic_store(&g_ldn_attached, ev); (void)g_ev_ldn;
}
HLE_FORCE("_ZN2nn3ldn11OpenStationEv", "local wireless (coop / cut)", Result, h_ldn_OpenSta, void) { COOP(coop_ldn_open_station(c_)); return mpfake() ? ls_move(1 << LS_INIT, LS_STA) : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn12CloseStationEv", "local wireless (coop / cut)", Result, h_ldn_CloseSta, void) { COOP(coop_ldn_close_station(c_)); return mpfake() ? ls_move(1 << LS_STA | 1 << LS_STAC, LS_INIT) : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn15OpenAccessPointEv", "local wireless (coop / cut)", Result, h_ldn_OpenAp, void) { COOP(coop_ldn_open_ap(c_)); return mpfake() ? ls_move(1 << LS_INIT, LS_AP) : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn16CloseAccessPointEv", "local wireless (coop / cut)", Result, h_ldn_CloseAp, void) { COOP(coop_ldn_close_ap(c_)); return mpfake() ? ls_move(1 << LS_AP | 1 << LS_APC, LS_INIT) : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn10DisconnectEv", "local wireless (coop / cut)", Result, h_ldn_Disc, void) { COOP(coop_ldn_disconnect(c_)); return mpfake() ? ls_move(1 << LS_STAC, LS_STA) : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn14DestroyNetworkEv", "local wireless (coop / cut)", Result, h_ldn_Destroy, void) { COOP(coop_ldn_destroy_network(c_)); return mpfake() ? ls_move(1 << LS_APC, LS_AP) : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn4ScanEPNS0_11NetworkInfoEPiiRKNS0_10ScanFilterEi", "local wireless scan (coop: asks the DM_COOP host; cut: 0 found)", Result, h_ldn_Scan, void *out, int *n, int max, const void *f, int ch) {
    COOP(coop_ldn_scan(c_, out, n, max, f, ch));
    (void)out; (void)max; (void)f; (void)ch; if (n) *n = 0; return mpfake() ? RES_OK : RES_FAIL;
}
HLE_FORCE("_ZN2nn3ldn11ScanPrivateEPNS0_11NetworkInfoEPiiRKNS0_10ScanFilterEi", "local wireless scan (coop: asks the DM_COOP host; cut: 0 found)", Result, h_ldn_ScanP, void *out, int *n, int max, const void *f, int ch) {
    COOP(coop_ldn_scan(c_, out, n, max, f, ch));
    (void)out; (void)max; (void)f; (void)ch; if (n) *n = 0; return mpfake() ? RES_OK : RES_FAIL;
}
HLE_FORCE("_ZN2nn3ldn13CreateNetworkERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_10UserConfigE", "local wireless (coop / cut)", Result, h_ldn_Create,
          const void *a, const void *b, const void *c) {
    COOP(coop_ldn_create_network(c_, a, b, c));
    (void)b; if (!mpfake()) return RES_FAIL;
    Result r = ls_move(1 << LS_AP, LS_APC); if (r) return r;
    memcpy(g_lcfg, a, sizeof g_lcfg); memcpy(g_luser, c, sizeof g_luser); return RES_OK;
}
HLE_FORCE("_ZN2nn3ldn20CreateNetworkPrivateERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_17SecurityParameterERKNS0_10UserConfigEiPKNS0_12AddressEntryE", "local wireless (cut)",
          Result, h_ldn_CreateP, const void *a, const void *b, const void *c, const void *d, int n, const void *e) { (void)a; (void)b; (void)c; (void)d; (void)n; (void)e; return RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn7ConnectERKNS0_11NetworkInfoERKNS0_14SecurityConfigERKNS0_10UserConfigEiNS0_13ConnectOptionE", "local wireless (coop / cut)", Result, h_ldn_Connect,
          const void *a, const void *b, const void *c, int v, int o) { COOP(coop_ldn_connect(c_, a, b, c, v, o)); (void)a; (void)b; (void)c; (void)v; (void)o; return mpfake() ? LDN_CONNECT_NOT_FOUND : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn14ConnectPrivateERKNS0_13NetworkConfigERKNS0_14SecurityConfigERKNS0_17SecurityParameterERKNS0_10UserConfigEiNS0_13ConnectOptionE", "local wireless (cut)",
          Result, h_ldn_ConnectP, const void *a, const void *b, const void *c, const void *d, int v, int o) { (void)a; (void)b; (void)c; (void)d; (void)v; (void)o; return RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn14GetNetworkInfoEPNS0_11NetworkInfoE", "local wireless (coop / cut)", Result, h_ldn_NetInfo, void *o) {
    COOP(coop_ldn_network_info(c_, o));
    if (!mpfake()) return RES_FAIL;
    if (g_ls != LS_APC) return LDN_INVALID_STATE;
    uint8_t *p = o; memset(p, 0, 0x480);
    memcpy(p, g_lcfg, 0x10);                                                   // NetworkId.IntentId
    for (int i = 0; i < 16; i++) p[0x10 + i] = (uint8_t)(0xA0 + i);             // NetworkId.SessionId
    static const uint8_t mac[6] = {0x02, 0x00, 0xa9, 0xfe, 0x01, 0x01};
    memcpy(p + 0x20, mac, 6); p[0x26] = 32; memset(p + 0x27, 'M', 32);         // Common: Bssid, Ssid
    p[0x48] = 1; p[0x4A] = 3; p[0x4B] = 2;                                     // Channel 1, LinkLevel 3, NetworkType Ldn
    uint8_t *l = p + 0x50;
    for (int i = 0; i < 16; i++) l[i] = (uint8_t)(0x50 + i);                    // ServerRandom
    l[0x10] = 1;                                                               // SecurityMode Product
    l[0x16] = g_lcfg[0x12]; l[0x17] = 1;                                       // NodeCountMax, NodeCount
    uint8_t *nd = l + 0x18; uint32_t ip = MP_IP; memcpy(nd, &ip, 4); memcpy(nd + 4, mac, 6); nd[0xA] = 0; nd[0xB] = 1;
    memcpy(nd + 0xC, g_luser, 0x21); memcpy(nd + 0x2E, g_lcfg + 0x14, 2);       // UserName, LocalCommunicationVersion
    memcpy(l + 0x21A, &g_ladvn, 2); memcpy(l + 0x21C, g_ladv, g_ladvn);
    return RES_OK;
}
HLE_FORCE("_ZN2nn3ldn14GetIpv4AddressEPNS0_11Ipv4AddressEPNS0_10SubnetMaskE", "local wireless (coop / cut)", Result, h_ldn_Ip, uint32_t *a, uint32_t *m) {
    COOP(coop_ldn_ipv4(c_, a, m));
    if (mpfake() && (g_ls == LS_APC || g_ls == LS_STAC)) { *a = MP_IP; *m = 0xFFFFFF00u; return RES_OK; }
    if (a) *a = 0;
    if (m) *m = 0;
    return mpfake() ? LDN_INVALID_STATE : RES_FAIL;
}
HLE_FORCE("_ZN2nn3ldn20GetSecurityParameterEPNS0_17SecurityParameterE", "local wireless (coop / cut)", Result, h_ldn_SecPrm, void *o) { COOP(coop_ldn_security_parameter(c_, o)); (void)o; return RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn16SetAdvertiseDataEPKvm", "local wireless (coop / cut)", Result, h_ldn_Adv, const void *d, uint64_t n) {
    COOP(coop_ldn_set_advertise(c_, d, n));
    if (!mpfake()) return RES_FAIL;
    if (n > sizeof g_ladv) return 0xC0CBu;
    memcpy(g_ladv, d, n); g_ladvn = (uint16_t)n; hle_signal_bound_system_event(g_ev_ldn); return RES_OK;
}
HLE_FORCE("_ZN2nn3ldn19GetDisconnectReasonEv", "local wireless (coop / cut): 0", int, h_ldn_DiscReason, void) { COOP(coop_ldn_disconnect_reason(c_)); return 0; }
HLE_FORCE("_ZN2nn3ldn20AddAcceptFilterEntryENS0_10MacAddressE", "local wireless (cut)", Result, h_ldn_Filter, uint64_t mac) { (void)mac; return RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn22SetStationAcceptPolicyENS0_12AcceptPolicyE", "local wireless (coop / cut)", Result, h_ldn_Policy, int p) { COOP(coop_ldn_set_accept_policy(c_, p & 0xFF)); (void)p; return mpfake() ? RES_OK : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn6RejectENS0_11Ipv4AddressE", "local wireless (coop / cut)", Result, h_ldn_Reject, uint32_t a) { COOP(coop_ldn_reject(c_, a)); (void)a; return mpfake() ? RES_OK : RES_FAIL; }
HLE_FORCE("_ZN2nn3ldn16GetNetworkConfigEPNS0_13NetworkConfigE", "local wireless (coop / cut)", Result, h_ldn_NetCfg, void *o) {
    COOP(coop_ldn_network_config(c_, o));
    if (mpfake() && g_ls == LS_APC) { memcpy(o, g_lcfg, 0x20); return RES_OK; }
    return RES_FAIL;
}
