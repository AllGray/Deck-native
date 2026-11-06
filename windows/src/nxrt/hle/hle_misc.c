// from nxnative dm/host/hle_misc.c (MIT, © seedds)
// Miscellaneous service layer: add-on content / shop / error applet / profanity filter / server name resolution / parental controls / software keyboard.
// On the real device each goes through IPC or a system applet; the host gives results as single-player offline. Pure-computation SDK parts (hash, util, software Opus) are not taken over, the original implementation is used directly.
// Windows: the profanity filter's host vtable holds run-time host-call tokens with hand-written adapters (nxrt_host_fn_token), in host heap memory; `long` is int64_t.
#include "hle.h"
#include "nxrt/runtime.h"
#include "posix_fs.h"

// ---------- aoc add-on content (the DLC NSPs in the player's folder) / ec shop (excluded) ----------
// Owned add-on content indices (ascending), fixed at the first query:
//   numeric subdirectories of DM_AOC_DIR: each is one DLC's RomFS, which the packing script extracts only from DLC NSPs really in the player's folder (title ID = base + 0x1000 + index).
//     Engage: F001 → 1 (E0), F002 → 2 (H0); the content itself is in the 2.0.0 update's RomFS and each DLC package holds one empty marker file; the game calls MountAddOnContent for each index ListAddOnContent returns (hle_fs.c).
//   No DM_AOC_DIR: none (on a Switch 2 Mario 1.2.1 uses it to tell whether the upgrade pack is owned, and the user has no Mario add-on content files).
//   DM_AOC_OWNED=0: always none (test switch, for runs without the DLC).
// Room for every index the scan accepts (1..2000): Super Smash Bros. Ultimate has 99 DLC, and a cap below the folder count dropped whichever readdir returned last (fighters among them).
static int g_aoc[2000], g_naoc = -1;
static int aoc_int_cmp(const void *a, const void *b) { return *(const int *)a - *(const int *)b; }
static void aoc_load(void) {
    if (g_naoc >= 0) return;
    g_naoc = 0;
    const char *e = getenv("DM_AOC_OWNED"), *dir = getenv("DM_AOC_DIR"); const char *why = "no DM_AOC_DIR";
    if (e && !strcmp(e, "0")) why = "DM_AOC_OWNED=0";
    else if (dir && *dir) {
        why = dir; DIR *d = opendir(dir); struct dirent *ent;
        while (d && (ent = readdir(d)) && g_naoc < (int)(sizeof g_aoc / sizeof *g_aoc)) {
            char *end; int64_t i = strtoll(ent->d_name, &end, 10);
            if (ent->d_name[0] >= '1' && ent->d_name[0] <= '9' && (!*end || !strcmp(end, ".img")) && i <= 2000) g_aoc[g_naoc++] = (int)i;
        }
        if (d) closedir(d);
        qsort(g_aoc, g_naoc, sizeof *g_aoc, aoc_int_cmp);
        int unique = 0; for (int i = 0; i < g_naoc; i++) if (!i || g_aoc[i] != g_aoc[i - 1]) g_aoc[unique++] = g_aoc[i];
        g_naoc = unique;   // a legacy directory and its replacement image report the same DLC once
    }
    static char list[2000 * 5 + 1]; list[0] = 0; for (int i = 0; i < g_naoc; i++) snprintf(list + strlen(list), sizeof list - strlen(list), "%s%d", i ? "," : "", g_aoc[i]);
    LOG("aoc: reporting %d add-on contents {%s} (%s)", g_naoc, list, why);
}
HLE_FORCE_BIG("_ZN2nn3aoc17CountAddOnContentEv", "add-on content count: length of the owned index list (DM_AOC_DIR)", int, h_aoc_Count, void) { aoc_load(); return g_naoc; }
// ListAddOnContent(out, start index, max count): writes out from the owned index list starting at the start index, returns the number written
HLE_FORCE_BIG("_ZN2nn3aoc16ListAddOnContentEPiii", "add-on content list: the owned index list", int, h_aoc_List, int *out, int off, int n) {
    int w = 0; aoc_load();
    for (int i = off < 0 ? 0 : off; i < g_naoc && w < n; i++) out[w++] = g_aoc[i];
    return w;
}
// The event object is created on demand by the nn::os side table (not signaled), the game waiting on it only times out / gets not-signaled
HLE_FORCE("_ZN2nn3aoc31GetAddOnContentListChangedEventEPNS_2os11SystemEventE", "add-on content change event, out of scope; bound to a kernel event, never signalled (the game polls it with TryWait and aborts if it is uninitialized)", void, h_aoc_GetEv, void *ev) { hle_bind_system_event(ev, 0); }
HLE_FORCE("_ZN2nn2ec24ShowShopAddOnContentListEv", "shop applet, out of scope; cut: no-op", void, h_ec_ShowShop, void) { DM_APPLET("shop"); LOG("ec::ShowShopAddOnContentList (cut)"); }
// The other shop entry points (Engage's title menu item "DLC" calls ShowShopAddOnContentList(ApplicationId, UserHandle)): the original starts the shop applet through applet::CreateLibraryApplet,
// the host has no applet proxy (oe initialization is a no-op), and the SDK reads through the null proxy and crashes (sdk+0x38660). All return void: as if the player closed the shop at once, the game goes back to its menu.
HLE_FORCE("_ZN2nn2ec24ShowShopAddOnContentListENS_13ApplicationIdE", "shop applet, out of scope; cut: no-op", void, h_ec_AocList1, void) { DM_APPLET("shop"); LOG("ec::ShowShopAddOnContentList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopAddOnContentListENS_13ApplicationIdERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_AocList2, void) { DM_APPLET("shop"); LOG("ec::ShowShopAddOnContentList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopAddOnContentListERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_AocList3, void) { DM_APPLET("shop"); LOG("ec::ShowShopAddOnContentList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec30ShowShopApplicationInformationENS_13ApplicationIdE", "shop applet, out of scope; cut: no-op", void, h_ec_AppInfo1, void) { DM_APPLET("shop"); LOG("ec::ShowShopApplicationInformation (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec30ShowShopApplicationInformationENS_13ApplicationIdERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_AppInfo2, void) { DM_APPLET("shop"); LOG("ec::ShowShopApplicationInformation (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListENS_13ApplicationIdE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub1, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListENS_13ApplicationIdERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub2, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListENS_13ApplicationIdERKNS0_8CourseIdE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub3, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListENS_13ApplicationIdERKNS0_8CourseIdERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub4, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec26ShowShopConsumableItemListENS_13ApplicationIdE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsList1, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec26ShowShopConsumableItemListENS_13ApplicationIdERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsList2, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec28ShowShopConsumableItemDetailENS_13ApplicationIdERKNS0_12ConsumableIdENS0_5NsUidE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsDet1, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemDetail (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec28ShowShopConsumableItemDetailENS_13ApplicationIdERKNS0_12ConsumableIdENS0_5NsUidERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsDet2, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemDetail (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec22ShowShopEnterCodeSceneEv", "shop applet, out of scope; cut: no-op", void, h_ec_Code1, void) { DM_APPLET("shop"); LOG("ec::ShowShopEnterCodeScene (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec22ShowShopEnterCodeSceneERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_Code2, void) { DM_APPLET("shop"); LOG("ec::ShowShopEnterCodeScene (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec22ShowShopProductDetailsENS0_5NsUidE", "shop applet, out of scope; cut: no-op", void, h_ec_Prod1, void) { DM_APPLET("shop"); LOG("ec::ShowShopProductDetails (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec22ShowShopProductDetailsENS0_5NsUidERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_Prod2, void) { DM_APPLET("shop"); LOG("ec::ShowShopProductDetails (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec19ShowShopProductListEPKNS0_5NsUidEiPKc", "shop applet, out of scope; cut: no-op", void, h_ec_ProdList1, void) { DM_APPLET("shop"); LOG("ec::ShowShopProductList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec19ShowShopProductListEPKNS0_5NsUidEiPKcRKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_ProdList2, void) { DM_APPLET("shop"); LOG("ec::ShowShopProductList (cut): the host has no shop"); }
// The overloads without an ApplicationId (the calling application's own pages; The Great Ace Attorney Chronicles, SDK 10, imports these beside the ones above)
HLE_FORCE("_ZN2nn2ec30ShowShopApplicationInformationEv", "shop applet, out of scope; cut: no-op", void, h_ec_AppInfo3, void) { DM_APPLET("shop"); LOG("ec::ShowShopApplicationInformation (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec30ShowShopApplicationInformationERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_AppInfo4, void) { DM_APPLET("shop"); LOG("ec::ShowShopApplicationInformation (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListEv", "shop applet, out of scope; cut: no-op", void, h_ec_Sub5, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub6, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListERKNS0_8CourseIdE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub7, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec24ShowShopSubscriptionListERKNS0_8CourseIdERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_Sub8, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec26ShowShopConsumableItemListEv", "shop applet, out of scope; cut: no-op", void, h_ec_ConsList3, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec26ShowShopConsumableItemListERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsList4, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemList (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec28ShowShopConsumableItemDetailERKNS0_12ConsumableIdENS0_5NsUidE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsDet3, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemDetail (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec28ShowShopConsumableItemDetailERKNS0_12ConsumableIdENS0_5NsUidERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_ConsDet4, void) { DM_APPLET("shop"); LOG("ec::ShowShopConsumableItemDetail (cut): the host has no shop"); }
HLE_FORCE("_ZN2nn2ec27ShowShopSubscriptionDetailsERKNS0_8CourseIdENS0_5NsUidERKNS_7account10UserHandleE", "shop applet, out of scope; cut: no-op", void, h_ec_SubDet, void) { DM_APPLET("shop"); LOG("ec::ShowShopSubscriptionDetails (cut): the host has no shop"); }
// The shop service accessor (Unreal's online subsystem, DRAGON QUEST XI S: FOnlineSubsystemSwitch::Init calls InitializeForShopServiceAccessors, whose SDK version connects to the ec service:
// an abort in sm::Initialize at the start of nnMain). The library init succeeds with nothing to set up; an accessor never initializes, so no request is ever sent. The accessor and its async
// response are stubbed whole: the SDK's destructor calls Finalize and aborts unless it succeeds. A response is complete at once and holds nothing
HLE_FORCE("_ZN2nn2ec33InitializeForShopServiceAccessorsEPvm", "shop service accessors' library init (ec service); offline, nothing to set up", Result, h_ec_InitSsa, void *mem, size_t n) { (void)mem; (void)n; return RES_OK; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessorC1Ev", "shop service accessor: no session (cut)", void, h_ec_SsaCtor, void *self) { (void)self; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessorD1Ev", "shop service accessor: no session to close (cut)", void, h_ec_SsaDtor, void *self) { (void)self; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor10InitializeERKNS0_11ShopServiceE", "shop service accessor: the shop is unavailable offline (cut)", Result, h_ec_SsaInit, void *self, const void *svc) { (void)self; (void)svc; return RES_FAIL; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor7RequestEPNS1_13AsyncResponseERKNS_7account3UidENS0_11ShopService6MethodEPKcRKNS1_8PostDataENS_8TimeSpanE", "shop request: never sent (cut)",
          Result, h_ec_SsaRequest, void *self, void *resp, const void *uid, int method, const char *path, const void *post, int64_t timeout) {
    (void)self; (void)resp; (void)uid; (void)method; (void)path; (void)post; (void)timeout; return RES_FAIL;
}
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor13AsyncResponseC1Ev", "shop async response: no request (cut)", void, h_ec_ArCtor, void *self) { (void)self; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor13AsyncResponseD1Ev", "shop async response: nothing to detach (cut)", void, h_ec_ArDtor, void *self) { (void)self; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor13AsyncResponse7TryWaitEv", "shop async response: complete at once (cut)", unsigned, h_ec_ArTryWait, void *self) { (void)self; return 1; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor13AsyncResponse7GetSizeEPm", "shop async response: no data (cut)", Result, h_ec_ArGetSize, void *self, size_t *n) { (void)self; if (n) *n = 0; return RES_FAIL; }
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor13AsyncResponse3GetEPvm", "shop async response: no data (cut)", Result, h_ec_ArGet, void *self, void *buf, size_t n) { (void)self; (void)buf; (void)n; return RES_FAIL; }
// DRAGON QUEST VII Reimagined (Unreal Engine 4.27) also imports these three: the library's finalize (its SDK version closes the ec session the init never opened) and two response calls
HLE_FORCE("_ZN2nn2ec31FinalizeForShopServiceAccessorsEv", "shop service accessors' library finalize: nothing was set up", void, h_ec_FinSsa, void) {}
HLE_FORCE("_ZN2nn2ec19ShopServiceAccessor13AsyncResponse6CancelEv", "shop async response: nothing to cancel (cut)", void, h_ec_ArCancel, void *self) { (void)self; }
HLE_FORCE("_ZNK2nn2ec19ShopServiceAccessor13AsyncResponse12GetErrorCodeEv", "shop async response: no error code (cut)", uint64_t, h_ec_ArErr, const void *self) { (void)self; return 0; }
// The "add-on content lost" error screen is an applet too: logged only
HLE_FORCE("_ZN2nn3aoc25ShowAddOnContentLostErrorEPKii", "add-on content lost error applet; the host only logs it", void, h_aoc_LostErr, const int *idx, int n) { DM_APPLET("error"); (void)idx; LOG("aoc::ShowAddOnContentLostError (%d entries) (cut)", n); }

// ---------- err error applet: printed by the host ----------
HLE_FORCE("_ZN2nn3err9ShowErrorENS_6ResultE", "error display applet; the host prints the module / description number", void, h_err_ShowError, Result r) { DM_APPLET("error");
    LOG("err::ShowError Result=%#x (module %u, description %u)", r, r & 0x1ff, (r >> 9) & 0x1fff);
}

// ---------- ngc profanity filter: the word list is in a system data save (mounted via IPC); single-player has no text visible to others, always judged as no profanity ----------
// The class is polymorphic, and games call it through its vtable too: Monster Hunter Rise checks the hunter's name with slot 8, MaskProfanityWordsInText(int *, char16_t *, uint32_t)
// (main+0x6bca160). The constructor used to do nothing, the object kept what the heap held there, and that call jumped into the heap. It now gets a host vtable: each slot of the SDK's
// (_ZTVN2nn3ngc15ProfanityFilterE) is matched by address with the class's methods and given the host's version (no word list: nothing is profanity, nothing is masked); others return 0
static Result ngc_v_zero(void) { return RES_OK; }
static void ngc_v_none(void *self) { (void)self; }
static Result ngc_v_check(void *self, uint32_t *masks, uint32_t pat, const void **words, uint64_t n) { (void)self; (void)pat; (void)words; for (uint64_t i = 0; masks && i < n; i++) masks[i] = 0; return RES_OK; }
static Result ngc_v_mask(void *self, int *masked, void *text, uint32_t region) { (void)self; (void)text; (void)region; if (masked) *masked = 0; return RES_OK; }
// The slots' adapters: the vtable holds guest-visible addresses, so each host version is a run-time token whose adapter reads AAPCS64's registers
#define P(i) ((void *)(uintptr_t)cpu->x[i])
static void ad_ngc_zero(NxwGuestCpu *cpu) { cpu->x[0] = ngc_v_zero(); }
static void ad_ngc_none(NxwGuestCpu *cpu) { ngc_v_none(P(0)); }
static void ad_ngc_check(NxwGuestCpu *cpu) { cpu->x[0] = ngc_v_check(P(0), P(1), (uint32_t)cpu->x[2], P(3), cpu->x[4]); }
static void ad_ngc_mask(NxwGuestCpu *cpu) { cpu->x[0] = ngc_v_mask(P(0), P(1), P(2), (uint32_t)cpu->x[3]); }
#undef P
static void *ngc_vtable(void) {
    enum { NV = 40 }; static void **vt; static int built;
    if (built) return built > 0 ? &vt[2] : NULL;
    void **sdk = guest_sym("_ZTVN2nn3ngc15ProfanityFilterE");
    if (!sdk) { built = -1; LOG("[RED] ProfanityFilter: the SDK has no vtable symbol; objects keep none"); return NULL; }
    if (!(vt = calloc(NV + 2, sizeof *vt))) die("ngc: no memory for the host vtable");   // host heap: below 4 GB, as the guest's own pointers
    void *zero = (void *)(uintptr_t)nxrt_host_fn_token("ngc::ProfanityFilter host slot (0)", ad_ngc_zero);
    void *none = (void *)(uintptr_t)nxrt_host_fn_token("ngc::ProfanityFilter host slot (no-op)", ad_ngc_none);
    void *check = (void *)(uintptr_t)nxrt_host_fn_token("ngc::ProfanityFilter host CheckProfanityWords", ad_ngc_check);
    void *mask = (void *)(uintptr_t)nxrt_host_fn_token("ngc::ProfanityFilter host MaskProfanityWordsInText", ad_ngc_mask);
    const struct { const char *name; void *fn; } M[] = {
        {"_ZN2nn3ngc15ProfanityFilterD1Ev", none}, {"_ZN2nn3ngc15ProfanityFilterD0Ev", none}, {"_ZN2nn3ngc15ProfanityFilter8FinalizeEv", none},
        {"_ZN2nn3ngc15ProfanityFilter19CheckProfanityWordsEPjjPPKcm", check}, {"_ZN2nn3ngc15ProfanityFilter19CheckProfanityWordsEPjjPPKDsm", check},
        {"_ZN2nn3ngc15ProfanityFilter19CheckProfanityWordsEPjjPPKtm", check},
        {"_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPcj", mask}, {"_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPDsj", mask},
        {"_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPtj", mask},
    };
    void *addr[sizeof M / sizeof *M]; for (unsigned k = 0; k < sizeof M / sizeof *M; k++) addr[k] = guest_sym(M[k].name);
    module_t *mod = module_of(sdk); int n = 0, named = 0;
    vt[0] = sdk[0]; vt[1] = sdk[1];   // offset to top, type info
    // A slot holds SDK code, or the host's version where the loader bound the slot by name to a forced take-over (Initialize, GetContentVersion, CheckProfanityWords): those stay.
    // The table ends at the first value that is neither (Monster Hunter Rise's SDK: 9 slots, then a 1)
    for (; n < NV && (uintptr_t)sdk[2 + n] >= 0x10000; n++) {
        if (module_of(sdk[2 + n]) != mod) { if (module_of(sdk[2 + n])) break; vt[2 + n] = sdk[2 + n]; continue; }   // another guest module: not this class's table
        vt[2 + n] = zero;
        for (unsigned k = 0; k < sizeof M / sizeof *M; k++) if (addr[k] && sdk[2 + n] == addr[k]) { vt[2 + n] = M[k].fn; named++; break; }
    }
    built = 1; LOG("ngc: ProfanityFilter host vtable of %d slots (%d matched to the class's methods)", n, named);
    return &vt[2];
}
HLE_FORCE("_ZN2nn3ngc15ProfanityFilterC1Ev", "profanity filter, the word list is in a system save; the host builds none and gives the object a host vtable", void, h_ngc_Ctor, void *self) {
    void *vt = ngc_vtable(); if (vt && self) memcpy(self, &vt, sizeof vt);
}
HLE_FORCE("_ZN2nn3ngc15ProfanityFilterD1Ev", "profanity filter", void, h_ngc_Dtor, void *self) { (void)self; }
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter10InitializeEPvm", "profanity filter init; the host has no word list", Result, h_ngc_Init, void *self, void *work, uint64_t sz) { (void)self; (void)work; (void)sz; return RES_OK; }
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter17GetContentVersionEv", "word list version; the host returns 0", uint32_t, h_ngc_Ver, void *self) { (void)self; return 0; }
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter19CheckProfanityWordsEPjjPPKcm", "profanity check; never a hit", Result, h_ngc_Check, void *self, uint32_t *masks, uint32_t pat, const char **words, uint64_t n) {
    (void)self; (void)pat; (void)words;
    for (uint64_t i = 0; i < n; i++) masks[i] = 0;
    return RES_OK;
}
// Called by name as well as through the vtable (The Great Ace Attorney Chronicles, SDK 10, imports these three): the SDK's versions read the word list the host never mounted
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPcj", "profanity masking (UTF-8); nothing is masked", Result, h_ngc_Mask8, void *self, int *masked, char *text, uint32_t region) { return ngc_v_mask(self, masked, text, region); }
// The UTF-16 forms by name: Dark Souls Remastered (SDK 4) masks the character name with the unsigned short one right after the name entry; the SDK's version allocated from a heap the host never set up
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPtj", "profanity masking (UTF-16); nothing is masked", Result, h_ngc_Mask16, void *self, int *masked, uint16_t *text, uint32_t region) { return ngc_v_mask(self, masked, text, region); }
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter24MaskProfanityWordsInTextEPiPDsj", "profanity masking (char16_t); nothing is masked", Result, h_ngc_Mask16c, void *self, int *masked, uint16_t *text, uint32_t region) { return ngc_v_mask(self, masked, text, region); }
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter11SetMaskModeENS1_8MaskModeE", "profanity mask mode; nothing is masked whatever the mode", void, h_ngc_MaskMode, void *self, int mode) { (void)self; (void)mode; }
HLE_FORCE("_ZN2nn3ngc15ProfanityFilter8FinalizeEv", "profanity filter shutdown; the host mounted no word list", void, h_ngc_Fini, void *self) { (void)self; }

// ---------- nsd server name resolution / pctl free communication: networking, out of scope ----------
HLE_FORCE("_ZN2nn3nsd7ResolveEPNS0_4FqdnERKS1_", "server name resolution, networking out of scope; cut", Result, h_nsd_Resolve, void *out, const void *in) { (void)out; (void)in; return RES_FAIL; }
HLE_FORCE("_ZN2nn3nsd9ResolveExEPNS0_4FqdnERKS1_", "server name resolution, networking out of scope; cut", Result, h_nsd_ResolveEx, void *out, const void *in) { (void)out; (void)in; return RES_FAIL; }
// bool arguments only look at the low 8 bits; returns bool. Link Play asks TryBegin before it opens (research/06 §3.1): while co-op is configured (DM_COOP,
// docs/coop-step1b.md) the three communication queries answer allowed, else never
#include "coop.h"
coop_node *dm_coop(void);   // hle_offline.c: NULL without DM_COOP
static unsigned pctl_coop(void) { return dm_coop() != NULL; }
// The permission query answers as TryBegin / IsFreeCommunicationAvailable do: the SDK's version calls the pctl service (pctl::detail::ipc::GetService), which the host never opens (DRAGON QUEST XI S imports it; from PR #38)
HLE_FORCE("_ZN2nn4pctl32CheckFreeCommunicationPermissionEb", "parental controls · permission to communicate with others; never (DM_COOP: allowed), no system UI", unsigned, h_pctl_CheckFree, unsigned show_ui) { (void)show_ui; return pctl_coop(); }
HLE_FORCE("_ZN2nn4pctl25TryBeginFreeCommunicationEb", "parental controls · communication with others; never allowed (DM_COOP: allowed)", unsigned, h_pctl_TryBegin, unsigned show_ui) { (void)show_ui; return pctl_coop(); }
// Parental control of the 3D (VR goggles) mode, Super Mario Odyssey: no restriction, nothing to configure
HLE_FORCE("_ZN2nn4pctl26GetStereoVisionRestrictionEv", "parental controls · 3D view restriction; never restricted", unsigned, h_pctl_GetStereo, void) { return 0; }
HLE_FORCE("_ZN2nn4pctl26SetStereoVisionRestrictionEb", "parental controls · 3D view restriction; the host does not store it", void, h_pctl_SetStereo, unsigned on) { (void)on; }
HLE_FORCE("_ZN2nn4pctl42ConfirmStereoVisionRestrictionConfigurableEv", "parental controls · whether the 3D view restriction can be changed; yes", Result, h_pctl_ConfStereo, void) { return RES_OK; }
HLE_FORCE("_ZN2nn4pctl53RequestStereoVisionRestrictionConfigurationPermissionEv", "parental controls · permission to change the 3D restriction; granted at once", Result, h_pctl_ReqStereo, void) { return RES_OK; }
HLE_FORCE("_ZN2nn4pctl20EndFreeCommunicationEv", "parental controls · communication with others", void, h_pctl_End, void) {}
// Engage asks on entering a battle map (no UI); without this the SDK connects to the pctl service through sm, which the host does not have, and aborts
HLE_FORCE("_ZN2nn4pctl28IsFreeCommunicationAvailableEv", "parental controls · whether communication with others is available; never (DM_COOP: yes)", unsigned, h_pctl_IsFree, void) { return pctl_coop(); }
// The Legend of Zelda: Breath of the Wild: checks the parental-control permission for stereoscopic 3D before turning on the options' Labo VR mode (returns bool; the argument = whether to
// show UI, the game passes 0). The host has no VR display (pl::BeginVrMode refuses, hle_sdk16.c): not permitted, so the game clears its VR flag and stays 2D
HLE_FORCE("_ZN2nn4pctl27CheckStereoVisionPermissionEb", "parental controls · stereoscopic 3D permission; the host has no VR display, not permitted", unsigned, h_pctl_Stereo, unsigned show_ui) { (void)show_ui; return 0; }

// swkbd software keyboard: see hle_swkbd.c

// ---------- From BananaNative (MIT, moceure), ported for Mario Kart World 1.8.0 on x86-64 ----------
#include <pthread.h>
uint32_t dm_sf_host_session(const char *name);   // hle_sf.c
#ifndef DM_SYNC_NEVER
#define DM_SYNC_NEVER 0x4e436d44u   // 'DmCN'
#endif
// ---------- chat: GameChat (Switch 2 SDKs) ----------
// Hyrule Warriors: Age of Imprisonment initializes it at boot (main+0x14480b8); the SDK's Initialize connects to the chat:u service, which the host does not serve, and aborts (Result 0xe15).
// The host has no GameChat: the library is ready and no chat is running, so the game's screen sharing has nobody to share with. The SDK's other calls go through the service object that
// Initialize would have made, so each one a game imports needs a hook here
HLE_FORCE("_ZN2nn4chat10InitializeEv", "GameChat (chat:u): ready, no chat", void, h_chat_Init, void) {}
HLE_FORCE("_ZN2nn4chat10IsChattingEv", "GameChat: not chatting", unsigned, h_chat_IsChatting, void) { return 0; }
HLE_FORCE("_ZN2nn4chat20SetScreenSharingModeENS0_17ScreenSharingModeE", "GameChat screen sharing mode: no chat to share with", void, h_chat_ShareMode, int mode) { (void)mode; }
// Mario Kart World 1.8.0 also attaches the room-info change event at boot (the SDK reads the service object Initialize would have made: a null read at sdk+0x6bc148) and sets its
// sharing key: no room ever changes (the event is bound, never signalled), and a key has nobody to share with (0: success, for a void or a Result return alike)
HLE_FORCE("_ZN2nn4chat28AttachToRoomInfoChangedEventEPNS_2os11SystemEventE", "GameChat room info change event: bound, never signalled (no chat)", uint32_t, h_chat_RoomEv, void *ev) { hle_bind_system_event(ev, 0); return RES_OK; }
HLE_FORCE("_ZN2nn4chat13SetSharingKeyERKNS0_10SharingKeyE", "GameChat sharing key: no chat to share with", uint32_t, h_chat_SetKey, const void *k) { (void)k; return 0; }
HLE_FORCE("_ZN2nn4chat15ClearSharingKeyEv", "GameChat sharing key cleared: no chat", uint32_t, h_chat_ClearKey, void) { return 0; }
// The room: none. RoomInfo's header (Mario Kart World 1.8.0 reads +0x28 the owner, +0x30 a flag, +0x38 the member count, +0x40 the members, 16 bytes each; its callers' frames
// leave it at most 0xf0 bytes) is zeroed, so no member is listed
HLE_FORCE("_ZN2nn4chat11GetRoomInfoEPNS0_8RoomInfoE", "GameChat room: none (no members)", uint32_t, h_chat_RoomInfo, void *info) { memset(info, 0, 0x40); return 0; }
// The chat applets (start a chat, the camera sharing guide, the camera settings) have no chat to start: they close at once
HLE_FORCE("_ZN2nn4chat13ShowChatStartERKNS_7account3UidE", "GameChat start applet: no chat, closes at once", uint32_t, h_chat_ShowStart, const void *uid) { (void)uid; return 0; }
HLE_FORCE("_ZN2nn4chat22ShowCameraSharingGuideEv", "GameChat camera sharing guide: no chat, closes at once", uint32_t, h_chat_ShowGuide, void) { return 0; }
HLE_FORCE("_ZN2nn4chat36ShowLocalCameraConfigurationVersion0ENS0_25CameraImageProcessingModeE", "GameChat camera settings applet: no camera, closes at once", uint32_t, h_chat_ShowCamCfg, int mode) { (void)mode; return 0; }
HLE_FORCE("_ZN2nn4chat46BeginFullScreenWithLocalCameraAvailableSectionEb", "GameChat full-screen camera section: no camera", void, h_chat_FsBegin, unsigned on) { (void)on; }
HLE_FORCE("_ZN2nn4chat44EndFullScreenWithLocalCameraAvailableSectionEv", "GameChat full-screen camera section: no camera", void, h_chat_FsEnd, void) {}

// Camera videos: no camera. Mario Kart World 1.8.0 sets up its local and remote camera storage at boot whatever the chat state (main+0xd32270, main+0xd31b20: 384 x 216, 3 pictures),
// makes a texture of each picture (nn::gs::NativeImageHandle → nvnNativeImage → nvnTextureInitializeFromNativeImage) and aborts if one fails; the SDK's own CalculateLocal-
// CameraVideoBufferSize aborts first (sdk+0x6bf2c8) because Initialize above never counted GameChat initialized. The host keeps each storage's pictures in the game's buffer as
// linear RGBA8 (black), which hle_nvn.c's native images make textures over (dm_chat_camera_image), and no picture ever comes: streaming starts nothing and the sync handle a
// RequestNext*CameraImage gives back never signals (DM_SYNC_NEVER), so the game keeps polling and shows no camera
#define CAM_SLOTS 16
#define CAM_TAG 0x43480000u   // 'CH' << 16: a camera handle (slot << 8 | picture); apart from upstream's decoder native image handles (below 0x10100; that decoder is not on this branch)
typedef struct { const void *type; uint8_t *mem; uint64_t size; uint32_t w, h, n; } chat_cam;
static chat_cam g_cam[CAM_SLOTS];
static pthread_mutex_t g_cam_mu = PTHREAD_MUTEX_INITIALIZER;
static uint32_t cam_stride(uint32_t w) { return (w * 4 + 255) & ~255u; }
static uint64_t cam_picture(uint32_t w, uint32_t h) { return ((uint64_t)cam_stride(w) * h + 0xfff) & ~(uint64_t)0xfff; }
static uint64_t cam_size(int w, int h, int n) {
    if (w < 32 || w > 1920 || h < 32 || h > 1080 || n < 1 || n > 4) { LOG("[RED] GameChat camera video %dx%d x%d: outside the SDK's range (32..1920 x 32..1080, 1..4 pictures)", w, h, n); return 0; }
    return (uint64_t)n * cam_picture((uint32_t)w, (uint32_t)h);
}
static uint32_t cam_init(const void *type, int w, int h, int n, void *mem, uint64_t size) {
    uint64_t need = cam_size(w, h, n);
    if (!need || !mem || size < need) { LOG("[RED] GameChat camera video storage %dx%d x%d: buffer %p of %#llx bytes, %#llx needed", w, h, n, mem, (unsigned long long)size, (unsigned long long)need); return RES_FAIL; }
    memset(mem, 0, need);   // black pictures
    pthread_mutex_lock(&g_cam_mu);
    int k = 0; while (k < CAM_SLOTS && g_cam[k].type && g_cam[k].type != type) k++;
    if (k < CAM_SLOTS) g_cam[k] = (chat_cam){type, mem, size, (uint32_t)w, (uint32_t)h, (uint32_t)n};
    pthread_mutex_unlock(&g_cam_mu);
    if (k == CAM_SLOTS) { LOG("[RED] GameChat: more than %d camera video storages", CAM_SLOTS); return RES_FAIL; }
    return 0;
}
static void cam_fini(const void *type) {
    pthread_mutex_lock(&g_cam_mu);
    for (int k = 0; k < CAM_SLOTS; k++) if (g_cam[k].type == type) g_cam[k] = (chat_cam){0};
    pthread_mutex_unlock(&g_cam_mu);
}
static uint32_t cam_handle(const void *type, int i) {
    uint32_t r = 0;
    pthread_mutex_lock(&g_cam_mu);
    for (int k = 0; k < CAM_SLOTS; k++) if (g_cam[k].type == type && i >= 0 && (uint32_t)i < g_cam[k].n) r = CAM_TAG | (uint32_t)k << 8 | (uint32_t)i;
    pthread_mutex_unlock(&g_cam_mu);
    if (!r) LOG("[RED] GameChat camera image handle %d of a storage the host does not hold (%p)", i, type);
    return r;
}
// A camera handle's picture for hle_nvn.c's native images: 1 and its memory, size and layout; 0 for a handle that is not a camera picture
int dm_chat_camera_image(uint32_t handle, uint8_t **mem, uint64_t *size, uint32_t *w, uint32_t *h, uint32_t *stride) {
    if ((handle & 0xffff0000u) != CAM_TAG) return 0;
    uint32_t k = handle >> 8 & 0xff, i = handle & 0xff; int ok = 0;
    pthread_mutex_lock(&g_cam_mu);
    if (k < CAM_SLOTS && g_cam[k].type && i < g_cam[k].n) {
        uint64_t pic = cam_picture(g_cam[k].w, g_cam[k].h);
        *mem = g_cam[k].mem + i * pic; *size = pic; *w = g_cam[k].w; *h = g_cam[k].h; *stride = cam_stride(g_cam[k].w); ok = 1;
    }
    pthread_mutex_unlock(&g_cam_mu);
    return ok;
}
static void cam_never(void *sync) { uint32_t *s = sync; s[0] = DM_SYNC_NEVER; s[1] = s[2] = s[3] = 0; }
HLE_FORCE("_ZN2nn4chat35CalculateLocalCameraVideoBufferSizeEiii", "GameChat camera pictures: the host's linear RGBA8 layout", uint64_t, h_chat_LocalSize, int w, int h, int n) { return cam_size(w, h, n); }
HLE_FORCE("_ZN2nn4chat36CalculateRemoteCameraVideoBufferSizeEiii", "GameChat camera pictures: the host's linear RGBA8 layout", uint64_t, h_chat_RemoteSize, int w, int h, int n) { return cam_size(w, h, n); }
HLE_FORCE("_ZN2nn4chat33InitializeLocalCameraVideoStorageEPNS0_20LocalCameraVideoTypeEiiiPvm", "GameChat camera storage: black pictures in the game's buffer (no camera)",
          uint32_t, h_chat_LocalInit, void *t, int w, int h, int n, void *mem, uint64_t size) { return cam_init(t, w, h, n, mem, size); }
HLE_FORCE("_ZN2nn4chat34InitializeRemoteCameraVideoStorageEPNS0_21RemoteCameraVideoTypeEiiiPvm", "GameChat camera storage: black pictures in the game's buffer (no camera)",
          uint32_t, h_chat_RemoteInit, void *t, int w, int h, int n, void *mem, uint64_t size) { return cam_init(t, w, h, n, mem, size); }
HLE_FORCE("_ZN2nn4chat31FinalizeLocalCameraVideoStorageEPNS0_20LocalCameraVideoTypeE", "GameChat camera storage released", void, h_chat_LocalFini, void *t) { cam_fini(t); }
HLE_FORCE("_ZN2nn4chat32FinalizeRemoteCameraVideoStorageEPNS0_21RemoteCameraVideoTypeE", "GameChat camera storage released", void, h_chat_RemoteFini, void *t) { cam_fini(t); }
HLE_FORCE("_ZN2nn4chat25GetLocalCameraImageHandleEPKNS0_20LocalCameraVideoTypeEi", "GameChat camera picture: a handle the host's nvnNativeImage knows", uint32_t, h_chat_LocalImg, const void *t, int i) { return cam_handle(t, i); }
HLE_FORCE("_ZN2nn4chat26GetRemoteCameraImageHandleEPKNS0_21RemoteCameraVideoTypeEi", "GameChat camera picture: a handle the host's nvnNativeImage knows", uint32_t, h_chat_RemoteImg, const void *t, int i) { return cam_handle(t, i); }
HLE_FORCE("_ZN2nn4chat25StartLocalCameraStreamingEv", "GameChat camera streaming: no camera, nothing streams", uint32_t, h_chat_LocalStart, void) { return 0; }
HLE_FORCE("_ZN2nn4chat24StopLocalCameraStreamingEv", "GameChat camera streaming: no camera", uint32_t, h_chat_LocalStop, void) { return 0; }
HLE_FORCE("_ZN2nn4chat26StartRemoteCameraStreamingERKNS_7account23NetworkServiceAccountIdE", "GameChat camera streaming: nobody to stream from", uint32_t, h_chat_RemoteStart, const void *id) { (void)id; return 0; }
HLE_FORCE("_ZN2nn4chat25StopRemoteCameraStreamingERKNS_7account23NetworkServiceAccountIdE", "GameChat camera streaming: nobody to stream from", uint32_t, h_chat_RemoteStop, const void *id) { (void)id; return 0; }
// (out sync handle, storage, picture, a handle to wait for first, passed by value in x3/x4[, the remote's account]): the picture never comes
HLE_FORCE("_ZN2nn4chat27RequestNextLocalCameraImageEPNS_2gs16NativeSyncHandleEPNS0_20LocalCameraVideoTypeEiS2_", "GameChat camera picture: never comes (no camera)",
          uint32_t, h_chat_LocalNext, void *sync, void *t, int i) { (void)t; (void)i; cam_never(sync); return 0; }
HLE_FORCE("_ZN2nn4chat28RequestNextRemoteCameraImageEPNS_2gs16NativeSyncHandleEPNS0_21RemoteCameraVideoTypeEiS2_RKNS_7account23NetworkServiceAccountIdE", "GameChat camera picture: never comes (nobody streams)",
          uint32_t, h_chat_RemoteNext, void *sync, void *t, int i) { (void)t; (void)i; cam_never(sync); return 0; }

// ---------- camera: the Switch 2's USB camera ----------
// The camera library is the game's own (Mario Kart World 1.8.0 links it into main: nn::camera::Initialize at main+0xcdebc0, called at boot by its camera manager whatever the
// chat state). It takes a context from the sdk (nn::camera::sdk::CreateCameraContext, a session through the graphics server's user session, which the host does not have: Result
// 0x30af3, and the game aborts) and then talks HIPC on it with nn::sf::hipc::SendSyncRequest. The host hands out a session of its own (hle_sf.c): every request succeeds with zeroed
// output, which reads as no camera (command 3 "Default" gives context 0)
HLE_FORCE("_ZN2nn6camera3sdk19CreateCameraContextEPNS_5gsdrv18UniqueNativeHandleE", "a host session answered with zeroed output: no camera", uint32_t, h_cam_Context, uint32_t *out) {
    *out = dm_sf_host_session("camera"); return RES_OK;
}

#include "hle_misc.adapters.inc"
