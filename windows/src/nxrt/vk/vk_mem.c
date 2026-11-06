// GPU memory: pool mirrors, sparse backing, the staging ring, dirty-page uploads, the per-frame budget (vk_mem.h; docs/m5-vulkan.md §3).
// nxnative's Metal backend wraps each pool zero-copy (newBufferWithBytesNoCopy, unified memory); a discrete GPU needs copies, and importing guest memory
// as host memory is what research/01 lesson 10 rules out. Behaviour of pool lookups follows nvn_mtl.m (mtl_pool_add, pool_find); the rest is new.
#include "vk_mem.h"
#include "vk_submit.h"
#include "nvn_dirty.h"   // gfx/: the dirty-page tracker the recording layer shares (nxnative's, ported by track A)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void dm_guest_write_prepare(void *addr, size_t len);   // host/crash.c: unprotect before the host writes guest memory
#ifndef _WIN32
#include <sys/mman.h>
#include <stdatomic.h>
#include "addrspace.h"
int dm_guest_readable_run(uint64_t a, uint64_t *end);   // host/hle_svc.c: what VirtualQuery tells on Windows
#endif

enum { PAGE = 4096, GRAN = 2u << 20, HOT_STREAK = 3, HOT_COOL_FRAMES = 240, MAXPOOL = 32768, RUN_MAX = 1024, MAXWIN = 32 };
// A pool larger than the device's largest buffer (AMD: 2 GiB; the game's biggest pool is 3.4 GiB) gets several buffers, windows over it. With sparse
// binding and aliasing the windows overlap and share the same memory, so any range up to the overlap fits in one of them; otherwise they are side by side.
typedef struct {
    uintptr_t lo, hi, base;       // the pool [lo, hi) as given; base = lo rounded down to a page
    uint64_t size, npages;        // bytes the buffer covers (from base, whole pages)
    const void *key;
    VkBuffer buf[MAXWIN]; uint64_t win_lo[MAXWIN], win_n[MAXWIN]; int nwin;   // window k covers [win_lo, win_lo + win_n) of the pool, from base
    int sparse;
    int placed, refs;             // placed: the pool's pages are device memory mapped at its guest address, the CPU's and the GPU's alike, with no copies; refs: pools that share it
    uint64_t sec_off;             // placed: where the pages lie in the guest section, for when the pool goes
    VkDeviceMemory whole;         // placed whole: the pool's one buffer object (small pools); larger ones are placed by the granule, on first use
    VkDeviceSize gran_size_last;
    vk_alloc *gran; uint64_t ngran, bound;   // sparse: one allocation per bound granule, bound into every window that covers it
    vk_alloc mem[MAXWIN];         // not sparse: one allocation per window
    const void *span;             // the dirty tracker's range (registered by the recording layer right after gfx_pool_add)
    uint64_t *up;                 // per page: the tracker's stamp when its device copy was taken (0 = never copied)
    uint32_t *last_dirty; uint8_t *streak, *hot;   // hot pages: written in HOT_STREAK consecutive frames, left writable and copied at every use
    uint8_t *committed;           // per page: seen committed and readable once (pool_sync asks VirtualQuery only for pages not known yet)
    _Atomic uint8_t *gpu_pending;   // per page: GPU writes recorded whose bytes have not reached guest memory yet (the device copy is the newer one)
    uint64_t hot_n, cursor;       // vkm_sync_all's position
} pool_t_;

static pool_t_ *g_pool[MAXPOOL]; static int g_npool;
static SRWLOCK g_lk = SRWLOCK_INIT;
static uint64_t g_frame = 1;
static VkBufferUsageFlags g_usage;
static VkDeviceSize g_gran = GRAN, g_max_buffer;
static int g_alias;   // overlapping windows: sparse binding with aliasing (NXRT_VK_NO_ALIAS=1 turns them off, for tests)

static struct {
    VkBuffer buf; vk_alloc mem; uint8_t *map; VkDeviceSize size, head, tail; int wrapped;
    struct { uint64_t value; int owner; VkDeviceSize begin, end; VkBuffer ovf; vk_alloc ovf_mem; } q[4096]; unsigned qh, qt;   // allocations in order; value 0 until owner commits
    SRWLOCK lk;
} g_st;

static uint64_t g_budget = 64ull << 20, g_budget_used;
static _Atomic uint64_t s_up_bytes, s_up_pages, s_up_runs, s_up_ns, s_sync, s_st_bytes, s_st_ovf, s_st_waits, s_wb_bytes, s_uncommitted;

// ---------- Staging ring ----------
static void st_reclaim(void) {   // caller holds g_st.lk
    uint64_t done = atomic_load(&g_vk.tl_done);
    while (g_st.qt != g_st.qh) {
        unsigned i = g_st.qt % 4096;
        if (!g_st.q[i].value || g_st.q[i].value > done) break;
        if (g_st.q[i].ovf) { vkDestroyBuffer(g_vk.dev, g_st.q[i].ovf, NULL); vk_free_mem(&g_st.q[i].ovf_mem); g_st.q[i].ovf = VK_NULL_HANDLE; }
        else g_st.tail = g_st.q[i].end;
        g_st.qt++;
    }
    if (g_st.qt == g_st.qh) { g_st.head = g_st.tail = 0; g_st.wrapped = 0; }
}
// Called by vk_submit at each commit: that slot's allocations now live until its timeline value
void vkm_committed(int owner, uint64_t value) {
    AcquireSRWLockExclusive(&g_st.lk);
    for (unsigned k = g_st.qt; k != g_st.qh; k++) if (!g_st.q[k % 4096].value && g_st.q[k % 4096].owner == owner) g_st.q[k % 4096].value = value;
    ReleaseSRWLockExclusive(&g_st.lk);
}
int vks_owner(void);
void *vkm_staging(VkDeviceSize n, VkDeviceSize align, VkBuffer *buf, VkDeviceSize *off) {
    int owner = vks_owner();
    if (owner < 0) { VKRED("staging asked for outside a submission"); return NULL; }
    if (!align) align = 16;
    n = (n + 15) & ~(VkDeviceSize)15;
    AcquireSRWLockExclusive(&g_st.lk);
    for (int attempt = 0; attempt < 64; attempt++) {
        st_reclaim();
        if (g_st.qh - g_st.qt >= 4095) goto wait;
        VkDeviceSize a = (g_st.head + align - 1) / align * align;
        int ok = 0;
        if (!g_st.wrapped) {   // free space: [head, size) then [0, tail)
            if (a + n <= g_st.size) ok = 1;
            else if (n <= g_st.tail) { a = 0; g_st.wrapped = 1; ok = 1; }
        } else if (a + n <= g_st.tail) ok = 1;
        if (ok) {
            unsigned i = g_st.qh++ % 4096;
            g_st.q[i].value = 0; g_st.q[i].owner = owner; g_st.q[i].begin = a; g_st.q[i].end = a + n; g_st.q[i].ovf = VK_NULL_HANDLE;
            g_st.head = a + n;
            ReleaseSRWLockExclusive(&g_st.lk);
            atomic_fetch_add(&s_st_bytes, n);
            *buf = g_st.buf; *off = a; return g_st.map + a;
        }
        if (n > g_st.size / 2) break;   // too big for the ring: a buffer of its own
    wait:;
        // the ring is full: wait for the oldest committed allocation's submission; when only the recording's own remain, overflow
        uint64_t v = g_st.qt != g_st.qh ? g_st.q[g_st.qt % 4096].value : 0;
        if (!v) break;
        ReleaseSRWLockExclusive(&g_st.lk);
        atomic_fetch_add(&s_st_waits, 1);
        vks_wait(v, UINT64_MAX);
        AcquireSRWLockExclusive(&g_st.lk);
    }
    // Overflow: a host buffer for this submission alone, released with it
    VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, n, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
    VkBuffer b = VK_NULL_HANDLE; vk_alloc m;
    void *p = NULL;
    if (vkCreateBuffer(g_vk.dev, &bci, NULL, &b) == VK_SUCCESS) {
        VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, b, &mr);
        if (!vk_alloc_mem(&mr, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, &m)) {
            vkBindBufferMemory(g_vk.dev, b, m.mem, m.off); p = m.map;
            unsigned i = g_st.qh++ % 4096;
            g_st.q[i].value = 0; g_st.q[i].owner = owner; g_st.q[i].begin = g_st.q[i].end = 0; g_st.q[i].ovf = b; g_st.q[i].ovf_mem = m;
        } else { vkDestroyBuffer(g_vk.dev, b, NULL); b = VK_NULL_HANDLE; }
    }
    ReleaseSRWLockExclusive(&g_st.lk);
    if (!p) { VKRED("staging: no host memory for %llu bytes", (unsigned long long)n); return NULL; }
    atomic_fetch_add(&s_st_ovf, 1); atomic_fetch_add(&s_st_bytes, n);
    *buf = b; *off = 0; return p;
}
uint64_t vkm_budget_left(void) { return g_budget_used >= g_budget ? 0 : g_budget - g_budget_used; }
void vkm_budget_take(uint64_t n) { g_budget_used += n; }

// ---------- Pools ----------
static int pool_cmp(const void *a, const void *b) { uintptr_t x = (*(pool_t_ *const *)a)->lo, y = (*(pool_t_ *const *)b)->lo; return x < y ? -1 : x > y; }
static pool_t_ *pool_find(uintptr_t a, uint64_t len) {   // caller holds g_lk (shared): binary search on start, then a scan for overlapping pools
    int lo = 0, hi = g_npool - 1, hit = -1;
    while (lo <= hi) { int m = (lo + hi) / 2; if (g_pool[m]->lo <= a) { hit = m; lo = m + 1; } else hi = m - 1; }
    if (hit >= 0 && a + len <= g_pool[hit]->hi) return g_pool[hit];
    for (int i = 0; i < g_npool; i++) if (a >= g_pool[i]->lo && a + len <= g_pool[i]->hi) return g_pool[i];
    return NULL;
}

static int win_of(const pool_t_ *p, uint64_t off) { int k = p->nwin - 1; while (k > 0 && p->win_lo[k] > off) k--; return k; }   // the last window starting at or before off
static int win_for(const pool_t_ *p, uint64_t off, uint64_t n) { int k = win_of(p, off); return off + n <= p->win_lo[k] + p->win_n[k] ? k : -1; }   // the window holding all of it, or -1

// ---------- Pools placed at their guest addresses ----------
// A mirror holds everything twice, which suits a discrete GPU and not unified memory: the Steam Deck ran out of memory for command submissions, the kernel swapping
// GTT buffers out in amdgpu_cs_ioctl. So on Linux a pool's pages become device memory mapped placed over its range in the guest section (VK_EXT_map_memory_placed):
// the game and the GPU read and write the same bytes, and the section's copy is given back. Nothing else needs them, as the guest reaches the pool only through this range
#ifndef _WIN32
static int g_placed;              // NXRT_VK_PLACED=0: mirrors, as on Windows
static uint64_t g_placed_bytes, g_cached_bytes;
enum { COHERENT_AMD = VK_MEMORY_PROPERTY_DEVICE_COHERENT_BIT_AMD | VK_MEMORY_PROPERTY_DEVICE_UNCACHED_BIT_AMD };
static int placed_type(uint32_t bits, VkMemoryPropertyFlags want, VkDeviceSize need) {
    for (uint32_t i = 0; i < g_vk.memp.memoryTypeCount; i++) {
        VkMemoryPropertyFlags f = g_vk.memp.memoryTypes[i].propertyFlags;
        if ((bits >> i & 1) && (f & want) == want && !(f & COHERENT_AMD) && g_vk.memp.memoryHeaps[g_vk.memp.memoryTypes[i].heapIndex].size >= need) return (int)i;
    }
    return -1;
}
// One 2 MB granule of the pool's buffer becomes device memory placed at its guest address, the first time the GPU needs it: the game's pools are far larger than what it
// draws from (Mario Kart World: 4.8 GB in 3,000 pools), and the GPU's 8 GB of GTT would not hold them all. The game is running meanwhile, so the granule is read-only from before the read
// until the map replaces it, and a write in between waits in the signal handler (host/crash.c) and then lands on the new pages. One at a time
extern _Atomic uintptr_t g_placing_lo, g_placing_hi;
static SRWLOCK g_place_lk = SRWLOCK_INIT;
static int gran_place(pool_t_ *p, uint64_t g, const VkMemoryRequirements *gr) {
    const VkMemoryPropertyFlags hc = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    int ty = -1, cached = 0;
    if (g_cached_bytes + g_gran <= (2500ull << 20)) { ty = placed_type(gr->memoryTypeBits, hc | VK_MEMORY_PROPERTY_HOST_CACHED_BIT, g_gran); cached = ty >= 0; }   // the game reads these too
    if (ty < 0) ty = placed_type(gr->memoryTypeBits, hc | VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, g_gran);
    if (ty < 0) ty = placed_type(gr->memoryTypeBits, hc, g_gran);
    if (ty < 0) return -1;
    // RADV maps a whole buffer object at the placed address, whatever size is asked for: the last granule's memory ends at the pool's end, rounded up to the sparse block, and what
    // sticks out past the pool goes back to the section right after the map (a 2 MB object over a short tail zeroed the next 2 MB of the game's heap)
    uint64_t o = g * g_gran, n = o + g_gran <= p->size ? g_gran : p->size - o, a = p->base + o;
    VkDeviceSize block = gr->alignment ? gr->alignment : 65536, bo = (n + block - 1) / block * block;
    p->gran_size_last = bo;
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, bo, (uint32_t)ty};
    VkDeviceMemory m = VK_NULL_HANDLE;
    if (vkAllocateMemory(g_vk.dev, &ai, NULL, &m) != VK_SUCCESS) return -1;
    void *tmp = NULL;
    if (vkMapMemory(g_vk.dev, m, 0, VK_WHOLE_SIZE, 0, &tmp) != VK_SUCCESS) { vkFreeMemory(g_vk.dev, m, NULL); return -1; }
    AcquireSRWLockExclusive(&g_place_lk);
    atomic_store(&g_placing_hi, a + n); atomic_store(&g_placing_lo, a);
    mprotect((void *)(uintptr_t)a, n, PROT_READ);
    int rd = as_section_read(p->sec_off + o, tmp, n);
    if (!rd && getenv("NXRT_VK_PL_CHECK")) {
        const uint8_t *g = (const uint8_t *)(uintptr_t)a, *t = tmp; uint64_t diff = 0, first = n, nz = 0;
        plat_guard pg; PLAT_GUARD_PUSH(&pg);
        if (!sigsetjmp(pg.jb, 0)) { for (uint64_t i = 0; i < n; i++) { if (g[i] != t[i]) { if (!diff) first = i; diff++; } if (g[i]) nz++; } PLAT_GUARD_POP(&pg); }
        else { VKRED("placed check: guest %#llx not readable", (unsigned long long)a); }
        static _Atomic int cnt; if (diff || atomic_fetch_add(&cnt, 1) < 8) VKLOG("placed check: pool %p gran %llu at %#llx+%#llx sec %#llx: guest nonzero %llu, differ %llu (first %#llx)", p->key, (unsigned long long)(a - p->base) / g_gran, (unsigned long long)a, (unsigned long long)n, (unsigned long long)(p->sec_off + o), (unsigned long long)nz, (unsigned long long)diff, (unsigned long long)first);
    }
    vkUnmapMemory(g_vk.dev, m);
    if (rd) { mprotect((void *)(uintptr_t)a, n, PROT_READ | PROT_WRITE); atomic_store(&g_placing_lo, 0); ReleaseSRWLockExclusive(&g_place_lk); vkFreeMemory(g_vk.dev, m, NULL); return -1; }
    VkMemoryMapPlacedInfoEXT pi = {VK_STRUCTURE_TYPE_MEMORY_MAP_PLACED_INFO_EXT, NULL, (void *)(uintptr_t)a};
    VkMemoryMapInfoKHR mi = {VK_STRUCTURE_TYPE_MEMORY_MAP_INFO_KHR, &pi, VK_MEMORY_MAP_PLACED_BIT_EXT, m, 0, n};
    void *out = NULL; VkResult r = vkMapMemory2KHR(g_vk.dev, &mi, &out);
    if (r != VK_SUCCESS || out != (void *)(uintptr_t)a) {
        mprotect((void *)(uintptr_t)a, n, PROT_READ | PROT_WRITE); atomic_store(&g_placing_lo, 0); ReleaseSRWLockExclusive(&g_place_lk);
        VKRED("pool %p: vkMapMemory2 placed at %#llx failed (%d)", p->key, (unsigned long long)a, r); vkFreeMemory(g_vk.dev, m, NULL); return -1;
    }
    if (bo > n && as_section_map(a + n, bo - n, p->sec_off + o + n)) VKRED("pool %p: the section past its end could not be mapped back", p->key);
    atomic_store(&g_placing_lo, 0); ReleaseSRWLockExclusive(&g_place_lk);
    as_section_punch(p->sec_off + o, n);
    if (p->span) nvn_dirty_protect((const void *)(uintptr_t)a, (size_t)n);   // the map replaced the tracker's protection
    p->gran[g].mem = m; p->gran[g].off = 0; p->gran[g].size = bo;
    g_placed_bytes += bo; if (cached) g_cached_bytes += bo;
    return 0;
}
// A small pool is placed whole when it is made, in one buffer object of its exact size. Most of the game's 3,000 pools are like that, and 2 MB for each would be 6 GB
static int pool_place_whole(pool_t_ *p) {
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, p->buf[0], &mr);
    const VkMemoryPropertyFlags hc = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    VkDeviceSize sz = (mr.size + PAGE - 1) / PAGE * PAGE;
    if (sz != p->size) return -1;   // the object would stick out past the pool
    int ty = -1, cached = 0;
    if (g_cached_bytes + sz <= (2500ull << 20)) { ty = placed_type(mr.memoryTypeBits, hc | VK_MEMORY_PROPERTY_HOST_CACHED_BIT, sz); cached = ty >= 0; }
    if (ty < 0) ty = placed_type(mr.memoryTypeBits, hc | VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, sz);
    if (ty < 0) ty = placed_type(mr.memoryTypeBits, hc, sz);
    if (ty < 0) return -1;
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, sz, (uint32_t)ty};
    if (vkAllocateMemory(g_vk.dev, &ai, NULL, &p->whole) != VK_SUCCESS) { p->whole = VK_NULL_HANDLE; return -1; }
    uint64_t a = p->base, n = p->size;
    void *tmp = NULL;
    if (vkMapMemory(g_vk.dev, p->whole, 0, VK_WHOLE_SIZE, 0, &tmp) != VK_SUCCESS) goto fail;
    AcquireSRWLockExclusive(&g_place_lk);
    atomic_store(&g_placing_hi, a + n); atomic_store(&g_placing_lo, a);
    mprotect((void *)(uintptr_t)a, n, PROT_READ);
    int rd = as_section_read(p->sec_off, tmp, n);
    vkUnmapMemory(g_vk.dev, p->whole);
    VkResult r = VK_ERROR_UNKNOWN; void *out = NULL;
    if (!rd) {
        vkBindBufferMemory(g_vk.dev, p->buf[0], p->whole, 0);
        VkMemoryMapPlacedInfoEXT pi = {VK_STRUCTURE_TYPE_MEMORY_MAP_PLACED_INFO_EXT, NULL, (void *)(uintptr_t)a};
        VkMemoryMapInfoKHR mi = {VK_STRUCTURE_TYPE_MEMORY_MAP_INFO_KHR, &pi, VK_MEMORY_MAP_PLACED_BIT_EXT, p->whole, 0, n};
        r = vkMapMemory2KHR(g_vk.dev, &mi, &out);
    }
    if (rd || r != VK_SUCCESS || out != (void *)(uintptr_t)a) {
        mprotect((void *)(uintptr_t)a, n, PROT_READ | PROT_WRITE); atomic_store(&g_placing_lo, 0); ReleaseSRWLockExclusive(&g_place_lk);
        VKRED("pool %p: placing it whole at %#llx failed (%d)", p->key, (unsigned long long)a, r);
        goto fail;
    }
    atomic_store(&g_placing_lo, 0); ReleaseSRWLockExclusive(&g_place_lk);
    as_section_punch(p->sec_off, n);
    g_placed_bytes += sz; if (cached) g_cached_bytes += sz;
    return 0;
fail:
    vkFreeMemory(g_vk.dev, p->whole, NULL); p->whole = VK_NULL_HANDLE;
    return -1;
}
// Puts the section's pages back with what the pool holds. The device memory may still be in use by the GPU, and goes when pool_free runs
static void pool_unplace(pool_t_ *p) {
    if (p->whole) {
        if (as_section_write(p->sec_off, (const void *)(uintptr_t)p->base, p->size)) VKRED("pool %p: its contents did not go back to the guest section", p->key);
        VkMemoryUnmapInfoKHR ui = {VK_STRUCTURE_TYPE_MEMORY_UNMAP_INFO_KHR, NULL, VK_MEMORY_UNMAP_RESERVE_BIT_EXT, p->whole};
        vkUnmapMemory2KHR(g_vk.dev, &ui);
        if (as_section_map(p->base, p->size, p->sec_off)) VKRED("pool %p: the guest section could not be mapped back at %#llx", p->key, (unsigned long long)p->base);
        return;
    }
    for (uint64_t g = 0; g < p->ngran; g++) {
        if (!p->gran[g].mem) continue;
        uint64_t o = g * g_gran, n = o + g_gran <= p->size ? g_gran : p->size - o, a = p->base + o;
        if (as_section_write(p->sec_off + o, (const void *)(uintptr_t)a, n)) VKRED("pool %p: its contents did not go back to the guest section", p->key);
        VkMemoryUnmapInfoKHR ui = {VK_STRUCTURE_TYPE_MEMORY_UNMAP_INFO_KHR, NULL, VK_MEMORY_UNMAP_RESERVE_BIT_EXT, p->gran[g].mem};
        vkUnmapMemory2KHR(g_vk.dev, &ui);
        uint64_t whole = p->gran[g].size > n ? p->gran[g].size : n;   // the unmap took all of the object's range, the part past the pool as well
        if (as_section_map(a, whole, p->sec_off + o)) VKRED("pool %p: the guest section could not be mapped back at %#llx", p->key, (unsigned long long)a);
    }
}
#endif

static void pool_free(pool_t_ *p) {
    for (int k = 0; k < p->nwin; k++) if (p->buf[k]) vkDestroyBuffer(g_vk.dev, p->buf[k], NULL);
    if (p->whole) vkFreeMemory(g_vk.dev, p->whole, NULL);
    else if (p->placed && p->sparse) { for (uint64_t g = 0; g < p->ngran; g++) if (p->gran[g].mem) vkFreeMemory(g_vk.dev, p->gran[g].mem, NULL); free(p->gran); }
    else if (p->sparse) { for (uint64_t g = 0; g < p->ngran; g++) vk_free_mem(&p->gran[g]); free(p->gran); }
    else for (int k = 0; k < p->nwin; k++) vk_free_mem(&p->mem[k]);
    free(p->up); free(p->last_dirty); free(p->streak); free(p->hot); free((void *)p->gpu_pending); free(p->committed); free(p);
}
static void pool_free_cb(void *arg) { pool_free(arg); }
static int pool_bind(pool_t_ *p, uint64_t o, uint64_t n);

void vkm_pool_add(const void *mem, uint64_t size) {
    if (!g_vk.up || !mem || !size) return;
    pool_t_ *p = calloc(1, sizeof *p);
    p->lo = (uintptr_t)mem; p->hi = p->lo + size; p->key = mem;
    p->base = p->lo & ~(uintptr_t)(PAGE - 1);
    p->size = ((p->hi - p->base) + PAGE - 1) & ~(uint64_t)(PAGE - 1); p->npages = p->size / PAGE;
    p->up = calloc(p->npages, sizeof *p->up); p->last_dirty = calloc(p->npages, sizeof *p->last_dirty); p->streak = calloc(p->npages, 1); p->hot = calloc(p->npages, 1); p->gpu_pending = calloc(p->npages, 1);
    p->committed = calloc(p->npages, 1);
    p->sparse = g_vk.has.sparse_buffer;
#ifndef _WIN32
    if (g_placed) {
        // A range inside a pool already placed (a virtual pool over a physical one) is that pool; one that only overlaps stays a mirror
        int inside = 0, overlap = 0;
        AcquireSRWLockExclusive(&g_lk);
        for (int i = 0; i < g_npool; i++) {
            pool_t_ *q = g_pool[i];
            if (!q->placed || p->lo >= q->base + q->size || p->hi <= q->base) continue;
            if (p->lo >= q->lo && p->hi <= q->hi) { q->refs += q->key == mem; inside = 1; } else overlap = 1;
            break;
        }
        ReleaseSRWLockExclusive(&g_lk);
        if (inside) { free(p->up); free(p->last_dirty); free(p->streak); free(p->hot); free((void *)p->gpu_pending); free(p->committed); free(p); return; }
        if (!overlap && !as_section_offset(p->base, p->size, &p->sec_off)) { p->placed = 1; if (p->size <= (32ull << 20)) p->sparse = 0; else if (!p->sparse) p->placed = 0; }
    }
#endif
    VkDeviceSize bsize = p->sparse ? (p->size + g_gran - 1) / g_gran * g_gran : p->size, w = bsize, ov = 0;
    if (g_max_buffer && bsize > g_max_buffer) {   // windows start on granules (or pages), so a granule never straddles two
        w = p->sparse ? g_max_buffer / g_gran * g_gran : g_max_buffer / PAGE * PAGE;
        if (p->sparse && g_alias) { ov = w / 4 < (256ull << 20) ? w / 4 : (256ull << 20); ov = ov / g_gran * g_gran; }
    }
    for (uint64_t at = 0; w > ov && p->nwin < MAXWIN; at += w - ov) {
        p->win_lo[p->nwin] = at; p->win_n[p->nwin] = bsize - at < w ? bsize - at : w; p->nwin++;
        if (at + w >= bsize) break;
    }
    if (!p->nwin || p->win_lo[p->nwin - 1] + p->win_n[p->nwin - 1] < bsize) {
        VKRED("memory pool %p+%#llx does not fit in %d buffers of the device's largest size (%#llx): not mirrored", mem, (unsigned long long)size, MAXWIN, (unsigned long long)g_max_buffer);
        p->nwin = 0; pool_free(p); return;
    }
    VkBufferCreateFlags fl = p->sparse ? VK_BUFFER_CREATE_SPARSE_BINDING_BIT | VK_BUFFER_CREATE_SPARSE_RESIDENCY_BIT | (ov ? VK_BUFFER_CREATE_SPARSE_ALIASED_BIT : 0) : 0;
    int ok = p->up && p->hot && p->committed;
    for (int k = 0; ok && k < p->nwin; k++) {
        VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, fl, p->win_n[k], g_usage, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
        ok = vkCreateBuffer(g_vk.dev, &bci, NULL, &p->buf[k]) == VK_SUCCESS;
        if (ok) vk_name((uint64_t)p->buf[k], VK_OBJECT_TYPE_BUFFER, "nvn memory pool");
    }
    if (!ok) { VKRED("cannot create the buffer of memory pool %p+%#llx", mem, (unsigned long long)size); pool_free(p); return; }
#ifndef _WIN32
    if (p->placed && !p->sparse) {
        if (pool_place_whole(p)) {   // a mirror of it instead, fully backed
            p->placed = 0;
            VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, p->buf[0], &mr);
            if (vk_alloc_mem(&mr, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, 0, &p->mem[0])) { VKRED("no device memory for memory pool %p+%#llx", mem, (unsigned long long)size); pool_free(p); return; }
            vkBindBufferMemory(g_vk.dev, p->buf[0], p->mem[0].mem, p->mem[0].off);
        }
        p->bound = p->size;
    } else
#endif
    if (p->sparse) { p->ngran = bsize / g_gran; p->gran = calloc(p->ngran, sizeof *p->gran); for (uint64_t g = 0; g < p->ngran; g++) p->gran[g].block = -1; }
    else {   // without sparse binding: all of it now
        for (int k = 0; k < p->nwin; k++) {
            VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, p->buf[k], &mr);
            if (vk_alloc_mem(&mr, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, 0, &p->mem[k])) { VKRED("no device memory for memory pool %p+%#llx", mem, (unsigned long long)size); pool_free(p); return; }
            vkBindBufferMemory(g_vk.dev, p->buf[k], p->mem[k].mem, p->mem[k].off);
        }
        p->bound = p->size;
    }
    if (p->nwin > 1) VKLOG("memory pool %p+%#llx: %d buffers of up to %llu MB%s (the device's largest buffer is %llu MB)", mem, (unsigned long long)size, p->nwin,
                           (unsigned long long)(w >> 20), ov ? ", overlapping by the same memory" : ", side by side", (unsigned long long)(g_max_buffer >> 20));
    const int placed_now = p->placed;
    AcquireSRWLockExclusive(&g_lk);
    if (g_npool < MAXPOOL) { g_pool[g_npool++] = p; qsort(g_pool, (size_t)g_npool, sizeof *g_pool, pool_cmp); p = NULL; }
    ReleaseSRWLockExclusive(&g_lk);
    if (p) { VKRED("too many memory pools (%d)", MAXPOOL); pool_free(p); return; }
    { pool_t_ *q = NULL; if (placed_now && getenv("NXRT_VK_PL_EAGER")) { AcquireSRWLockShared(&g_lk); q = pool_find((uintptr_t)mem, 1); ReleaseSRWLockShared(&g_lk); if (q) pool_bind(q, 0, q->size); } }   // test switch
    static int nlog; if (nlog++ < 32) VKLOG("memory pool %p+%#llx: %s%s", mem, (unsigned long long)size, placed_now ? "placed at its address" : "device-local mirror", g_vk.has.sparse_buffer ? ", backed in 2 MB granules on first use" : "");
}

void vkm_pool_del(const void *mem) {
    pool_t_ *p = NULL;
    AcquireSRWLockExclusive(&g_lk);
    for (int i = 0; i < g_npool; i++) if (g_pool[i]->key == mem) { if (g_pool[i]->refs > 0) { g_pool[i]->refs--; break; } p = g_pool[i]; memmove(&g_pool[i], &g_pool[i + 1], (size_t)(g_npool - i - 1) * sizeof *g_pool); g_npool--; break; }
    ReleaseSRWLockExclusive(&g_lk);
#ifndef _WIN32
    if (p && p->placed) pool_unplace(p);   // the game may reuse this memory at once; the device memory stays until the submissions are past it
#endif
    if (p) vk_retire(pool_free_cb, p);   // submissions recorded so far may still read it
}

// The window of p holding [addr, addr+len): buffer and offset, or 0 when the range spans two windows that do not overlap that far (logged once)
static int pool_window(pool_t_ *p, uint64_t addr, uint64_t len, VkBuffer *buf, VkDeviceSize *off) {
    uint64_t o = addr - p->base; int k = win_for(p, o, len ? len : 1);
    if (k < 0) {
        static _Atomic int said; if (!atomic_exchange(&said, 1))
            VKRED("a range %#llx+%#llx spans two of memory pool %p's buffers (the device's largest buffer is %llu MB): it reads zeros (later ones are not logged)",
                  (unsigned long long)addr, (unsigned long long)len, p->key, (unsigned long long)(g_max_buffer >> 20));
        return 0;
    }
    if (buf) *buf = p->buf[k];
    if (off) *off = o - p->win_lo[k];
    return 1;
}

static int pool_bind(pool_t_ *p, uint64_t o, uint64_t n);
int vkm_addr(uint64_t addr, uint64_t len, VkBuffer *buf, VkDeviceSize *off) {
    AcquireSRWLockShared(&g_lk);
    pool_t_ *p = pool_find((uintptr_t)addr, len ? len : 1);
    int ok = p && pool_window(p, addr, len, buf, off);
    ReleaseSRWLockShared(&g_lk);
    // Whatever asks for a range may have the GPU write it (query results, copies, storage): a placed granule must exist first, or the write is lost and the game reads zeros
    if (ok && p->placed) pool_bind(p, addr - p->base, len ? len : 1);
    return ok;
}

// Sparse backing for [o, o+n) of the buffer: unbound granules get memory and are bound before anything is recorded against them (a bind is a queue
// operation; it is waited for here, which happens once per 2 MB granule)
static SRWLOCK g_bind_lk = SRWLOCK_INIT;
static int pool_bind_locked(pool_t_ *p, uint64_t o, uint64_t n);
// Several threads ask for ranges (the replay's uses, the recording layer's address lookups). Two placing one granule would be worse than wasteful: the second reads the section after the first emptied it
static int pool_bind(pool_t_ *p, uint64_t o, uint64_t n) {
    if (!p->sparse) return 0;
    AcquireSRWLockExclusive(&g_bind_lk);
    int r = pool_bind_locked(p, o, n);
    ReleaseSRWLockExclusive(&g_bind_lk);
    return r;
}
static int pool_bind_locked(pool_t_ *p, uint64_t o, uint64_t n) {
    uint64_t g0 = o / g_gran, g1 = (o + n + g_gran - 1) / g_gran;
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, p->buf[0], &mr);
    VkMemoryRequirements gr = {g_gran, mr.alignment, mr.memoryTypeBits};
    for (;;) {
        uint64_t fresh[32]; int nf = 0, rc = 0;   // granules backed in this round; each is bound into every window that covers it (two where windows overlap)
        for (uint64_t g = g0; g < g1 && g < p->ngran && nf < 32; g++) {
            if (p->gran[g].mem) continue;
#ifndef _WIN32
            if (p->placed) { if (gran_place(p, g, &gr)) { VKRED("no device memory to place pool %p at %#llx", p->key, (unsigned long long)(g * g_gran)); rc = -1; break; } }
            else
#endif
            if (vk_alloc_mem(&gr, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, 0, &p->gran[g])) { VKRED("no device memory to back pool %p at %#llx", p->key, (unsigned long long)(g * g_gran)); rc = -1; break; }
            fresh[nf++] = g;
            p->bound += g_gran;
        }
        if (!nf) return rc;
        VkSparseMemoryBind binds[64]; VkSparseBufferMemoryBindInfo bis[MAXWIN]; int nb = 0, nbi = 0;
        for (int k = 0; k < p->nwin; k++) {
            int first = nb;
            for (int i = 0; i < nf; i++) {
                uint64_t at = fresh[i] * g_gran;
                if (at >= p->win_lo[k] && at < p->win_lo[k] + p->win_n[k] && nb < 64)
                    binds[nb++] = (VkSparseMemoryBind){at - p->win_lo[k], g_gran, p->gran[fresh[i]].mem, p->gran[fresh[i]].off, 0};
            }
            if (nb > first) bis[nbi++] = (VkSparseBufferMemoryBindInfo){p->buf[k], (uint32_t)(nb - first), binds + first};
        }
        VkBindSparseInfo si = {VK_STRUCTURE_TYPE_BIND_SPARSE_INFO, NULL, 0, NULL, (uint32_t)nbi, bis, 0, NULL, 0, NULL, 0, NULL};
        VkFenceCreateInfo fci = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; VkFence f; vkCreateFence(g_vk.dev, &fci, NULL, &f);
        AcquireSRWLockExclusive(&g_vk.qlock);
        VkResult r = vkQueueBindSparse(g_vk.q, 1, &si, f);
        ReleaseSRWLockExclusive(&g_vk.qlock);
        if (r == VK_SUCCESS) vkWaitForFences(g_vk.dev, 1, &f, VK_TRUE, UINT64_MAX); else { VKRED("vkQueueBindSparse failed (%d)", r); vk_lost("a sparse bind", r); rc = -1; }
        vkDestroyFence(g_vk.dev, f, NULL);
        if (rc) return rc;
    }
}

// Guest memory is read with a guard: a page that is not mapped any more (a pool the game freed without finalizing) reads as zeros instead of crashing
#ifdef _WIN32
static int guest_copy(void *dst, const void *src, size_t n) {
    __try { memcpy(dst, src, n); return 0; }
    __except (GetExceptionCode() == EXCEPTION_ACCESS_VIOLATION ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) { memset(dst, 0, n); return -1; }
}
#else
static int guest_copy(void *dst, const void *src, size_t n) {
    plat_guard g; PLAT_GUARD_PUSH(&g);
    if (sigsetjmp(g.jb, 0)) { memset(dst, 0, n); return -1; }
    memcpy(dst, src, n);
    PLAT_GUARD_POP(&g);
    return 0;
}
#endif

static void page_rewritten(pool_t_ *p, uint64_t pg) {   // a page copied before and written again: hot after HOT_STREAK frames in a row
    if (p->last_dirty[pg] == (uint32_t)g_frame) return;
    p->streak[pg] = p->last_dirty[pg] + 1 == (uint32_t)g_frame ? (uint8_t)(p->streak[pg] < 255 ? p->streak[pg] + 1 : 255) : 1;
    p->last_dirty[pg] = (uint32_t)g_frame;
    if (p->streak[pg] >= HOT_STREAK && !p->hot[pg]) { p->hot[pg] = 1; p->hot_n++; }
}
// Whether page pg's device copy is behind guest memory: never copied, hot, or written (a fault stamped it) since its copy; untracked pages always are
static inline int page_needs(pool_t_ *p, uint64_t pg) {
    if (atomic_load(&p->gpu_pending[pg])) return 0;   // a GPU write in flight: the device copy is ahead until its bytes are written back
    if (!p->up[pg] || p->hot[pg] || !p->span) return 1;
    return nvn_dirty_since_span(p->span, (const void *)(p->base + pg * PAGE), PAGE, p->up[pg]);
}
// A clean group of 64 pages at once: one generation scan from the group's oldest copy
static int group_clean(pool_t_ *p, uint64_t g0) {
    if (!p->span || g0 + 64 > p->npages) return 0;
    uint64_t lo = UINT64_MAX;
    for (uint64_t k = g0; k < g0 + 64; k++) { if (!p->up[k] || p->hot[k] || atomic_load(&p->gpu_pending[k])) return 0; if (p->up[k] < lo) lo = p->up[k]; }
    return !nvn_dirty_since_span(p->span, (const void *)(p->base + g0 * PAGE), 64 * PAGE, lo);
}

typedef struct { VkBufferCopy r[256]; int n; VkBuffer src, dst; } copy_batch;
static void batch_flush(copy_batch *b, VkCommandBuffer cb) { if (b->n) vkCmdCopyBuffer(cb, b->src, b->dst, (uint32_t)b->n, b->r); b->n = 0; }
// [off, off+n) of the pool from src at so: one copy per window it crosses (windows that overlap share their memory, so one of them is enough)
static void batch_put(copy_batch *b, pool_t_ *p, VkCommandBuffer cb, VkBuffer src, VkDeviceSize so, uint64_t off, uint64_t n) {
    while (n) {
        int k = win_of(p, off); uint64_t end = p->win_lo[k] + p->win_n[k], m = off + n <= end ? n : end - off;
        if (b->n == 256 || (b->n && (b->src != src || b->dst != p->buf[k]))) batch_flush(b, cb);
        b->src = src; b->dst = p->buf[k]; b->r[b->n++] = (VkBufferCopy){so, off - p->win_lo[k], m};
        so += m; off += m; n -= m;
    }
}
static void pool_fill_zero(pool_t_ *p, VkCommandBuffer cb, uint64_t off, uint64_t n) {
    while (n) { int k = win_of(p, off); uint64_t end = p->win_lo[k] + p->win_n[k], m = off + n <= end ? n : end - off; vkCmdFillBuffer(cb, p->buf[k], off - p->win_lo[k], m, 0); off += m; n -= m; }
}

// Copies the pages among [p0, p1) of pool p whose device copy is behind into cb, until budget bytes; *stop_at (optional) is where it stopped.
// Order per run (nxnative's): take the stamp, write-protect, then read guest memory: a write after the protection faults and is seen at the next check
static uint64_t pool_sync(pool_t_ *p, uint64_t p0, uint64_t p1, VkCommandBuffer cb, uint64_t budget, uint64_t *stop_at) {
    if (p->placed) {   // the GPU reads the guest's own pages: only what a use names has to be placed
        if (p1 > p->npages) p1 = p->npages;
        if (stop_at) *stop_at = p1;
        if (budget == UINT64_MAX && p1 > p0) pool_bind(p, p0 * PAGE, (p1 - p0) * PAGE);
        return 0;
    }
    if (!p->span) p->span = nvn_dirty_span((const void *)p->lo, p->hi - p->lo);
    if (p1 > p->npages) p1 = p->npages;
    copy_batch b; b.n = 0; b.src = b.dst = VK_NULL_HANDLE;
    uint64_t queued = 0, t0 = now_ns(), pg = p0;
    while (pg < p1 && queued < budget) {
        if (!(pg & 63) && pg + 64 <= p1 && group_clean(p, pg)) { pg += 64; continue; }
        if (!page_needs(p, pg)) { pg++; continue; }
        uint64_t run0 = pg;
        while (pg < p1 && pg - run0 < RUN_MAX && page_needs(p, pg)) { if (p->up[pg] && !p->hot[pg]) page_rewritten(p, pg); pg++; }
        // Pages the game never touched are not committed (the heap commits lazily): nothing to read or protect there. A use reads them as zeros and
        // keeps asking (they stay "never copied"); the background sync steps over the whole uncommitted region. VirtualQuery walks the address space's
        // regions, which the tracker's protections split finely: in gameplay it was 70 % of the render thread's time. Pages seen committed are remembered
        // (a pool's memory stays committed while the pool exists) and only a run that starts on a page not known yet is asked about
        uint64_t known = run0; while (known < pg && p->committed[known]) known++;
#ifdef _WIN32
        MEMORY_BASIC_INFORMATION mi;
        if (known > run0) pg = known;   // the known part now, the rest in the next run
        else if (VirtualQuery((const void *)(p->base + run0 * PAGE), &mi, sizeof mi)) {
            uint64_t rend = ((uintptr_t)mi.BaseAddress + mi.RegionSize - p->base) / PAGE;
            int readable = mi.State == MEM_COMMIT && !(mi.Protect & (PAGE_NOACCESS | PAGE_GUARD));
#else
        uint64_t rend_a;
        if (known > run0) pg = known;
        else {
            int readable = dm_guest_readable_run(p->base + run0 * PAGE, &rend_a);
            uint64_t rend = (rend_a - p->base + PAGE - 1) / PAGE;
#endif
            if (rend < pg) pg = rend > run0 ? rend : run0 + 1;
            if (!readable) {
                if (budget == UINT64_MAX) { if (pool_bind(p, run0 * PAGE, (pg - run0) * PAGE)) break; batch_flush(&b, cb); pool_fill_zero(p, cb, run0 * PAGE, (pg - run0) * PAGE); }
                else if (rend > pg) pg = rend < p1 ? rend : p1;
                atomic_fetch_add(&s_uncommitted, pg - run0);
                continue;
            }
            memset(p->committed + run0, 1, (size_t)(pg - run0));
        }
        uint64_t off = run0 * PAGE, n = (pg - run0) * PAGE;
        if (pool_bind(p, off, n)) break;
        uint64_t stamp = nvn_dirty_now();
        for (uint64_t q0 = run0; q0 < pg; ) {   // protect the pages that are not hot
            if (p->hot[q0]) { q0++; continue; }
            uint64_t q1 = q0; while (q1 < pg && !p->hot[q1]) q1++;
            if (p->span) nvn_dirty_protect((const void *)(p->base + q0 * PAGE), (size_t)((q1 - q0) * PAGE));
            q0 = q1;
        }
        VkBuffer sb; VkDeviceSize so;
        uint8_t *dst = vkm_staging(n, 16, &sb, &so);
        if (!dst) { pg = run0; break; }
        guest_copy(dst, (const void *)(p->base + off), (size_t)n);
        for (uint64_t k = run0; k < pg; k++) p->up[k] = stamp;
        batch_put(&b, p, cb, sb, so, off, n);
        queued += n;
        atomic_fetch_add(&s_up_pages, pg - run0); atomic_fetch_add(&s_up_runs, 1);
    }
    batch_flush(&b, cb);
    if (stop_at) *stop_at = pg;
    atomic_fetch_add(&s_up_bytes, queued); atomic_fetch_add(&s_up_ns, now_ns() - t0);
    return queued;
}

int vkm_use(uint64_t addr, uint64_t len, VkBuffer *buf, VkDeviceSize *off) {
    AcquireSRWLockShared(&g_lk);
    pool_t_ *p = pool_find((uintptr_t)addr, len ? len : 1);
    int ok = p && pool_window(p, addr, len, buf, off);
    ReleaseSRWLockShared(&g_lk);
    if (!ok) return 0;
    VkCommandBuffer cb = vks_upload_cb();
    if (!cb) return 1;
    atomic_fetch_add(&s_sync, 1);
    pool_sync(p, (addr - p->base) / PAGE, (addr + (len ? len : 1) - p->base + PAGE - 1) / PAGE, cb, UINT64_MAX, NULL);
    return 1;
}

// Every pool's pages behind guest memory, round robin from where the last call stopped, within the frame's upload budget and a scan limit
static uint64_t g_scan_pages = 262144;   // NXRT_VK_SCAN_PAGES: pages looked at per call (1 GB)
static int g_rr;
uint64_t vkm_sync_all(void) {
    VkCommandBuffer cb = vks_upload_cb();
    if (!cb) return 0;
    uint64_t total = 0, scanned = 0;
    AcquireSRWLockShared(&g_lk);
    for (int k = 0; k < g_npool && vkm_budget_left() && scanned < g_scan_pages; k++) {
        int i = (g_rr + k) % g_npool;
        pool_t_ *p = g_pool[i];
        uint64_t from = p->cursor < p->npages ? p->cursor : 0, to = from + (g_scan_pages - scanned), stop = from;
        uint64_t n = pool_sync(p, from, to, cb, vkm_budget_left(), &stop);
        vkm_budget_take(n); total += n; scanned += stop - from;
        p->cursor = stop >= p->npages ? 0 : stop;
        if (p->cursor) { g_rr = i; break; }   // continue in this pool next time
        g_rr = (i + 1) % g_npool;
    }
    ReleaseSRWLockShared(&g_lk);
    atomic_fetch_add(&s_sync, 1);
    return total;
}

// GPU writes in flight over [a, a+len): +1 when recorded, -1 when their bytes have reached guest memory. The pages are copied again from guest memory after
// that (the write back stamps them), which also brings in anything the CPU wrote beside the GPU's bytes
static void gpu_pending(uint64_t a, uint64_t len, int d) {
    AcquireSRWLockShared(&g_lk);
    pool_t_ *p = pool_find((uintptr_t)a, len);
    if (p) for (uint64_t pg = (a - p->base) / PAGE; pg <= (a + len - 1 - p->base) / PAGE && pg < p->npages; pg++) atomic_fetch_add(&p->gpu_pending[pg], (uint8_t)d);
    ReleaseSRWLockShared(&g_lk);
}

static int pool_placed_at(uint64_t a, uint64_t len) {
    AcquireSRWLockShared(&g_lk);
    pool_t_ *p = pool_find((uintptr_t)a, len ? len : 1);
    int r = p && p->placed;
    ReleaseSRWLockShared(&g_lk);
    return r;
}

// ---------- GPU writes back to guest memory ----------
typedef struct { uint64_t addr, len; VkBuffer buf; vk_alloc mem; const uint8_t *cpu; uint32_t fill; int kind; } wb_t;   // kind 0 readback, 1 known bytes, 2 fill
static void wb_land(void *arg) {
    wb_t *w = arg;
    dm_guest_write_prepare((void *)(uintptr_t)w->addr, (size_t)w->len);
    uint8_t *d = (uint8_t *)(uintptr_t)w->addr;
    if (w->kind == 0) memcpy(d, w->mem.map, (size_t)w->len);
    else if (w->kind == 1) memcpy(d, w->cpu, (size_t)w->len);
    else for (uint64_t i = 0; i < w->len; i++) d[i] = (uint8_t)(w->fill >> 8 * (i & 3));
    gpu_pending(w->addr, w->len, -1);
    atomic_fetch_add(&s_wb_bytes, w->len);
    if (w->buf) { vkDestroyBuffer(g_vk.dev, w->buf, NULL); vk_free_mem(&w->mem); }
    free((void *)w->cpu); free(w);
}
void vkm_gpu_wrote(uint64_t addr, uint64_t len) {
    VkBuffer pb; VkDeviceSize po;
    VkCommandBuffer cb = vks_cb();
    if (!cb || !len || !vkm_addr(addr, len, &pb, &po)) return;
    if (pool_placed_at(addr, len)) {   // already in guest memory: only the host's view of it
        vk_mem_barrier(cb, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_PIPELINE_STAGE_2_HOST_BIT, VK_ACCESS_2_HOST_READ_BIT);
        return;
    }
    wb_t *w = calloc(1, sizeof *w); w->addr = addr; w->len = len;
    VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, len, VK_BUFFER_USAGE_TRANSFER_DST_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
    vkCreateBuffer(g_vk.dev, &bci, NULL, &w->buf);
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, w->buf, &mr);
    if (vk_alloc_mem(&mr, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT, 0, &w->mem) &&
        vk_alloc_mem(&mr, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, 0, &w->mem)) { vkDestroyBuffer(g_vk.dev, w->buf, NULL); free(w); return; }
    vkBindBufferMemory(g_vk.dev, w->buf, w->mem.mem, w->mem.off);
    vk_mem_barrier(cb, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_READ_BIT);
    VkBufferCopy c = {po, 0, len};
    vkCmdCopyBuffer(cb, pb, w->buf, 1, &c);
    vk_mem_barrier(cb, VK_PIPELINE_STAGE_2_COPY_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT, VK_PIPELINE_STAGE_2_HOST_BIT, VK_ACCESS_2_HOST_READ_BIT);
    gpu_pending(addr, len, 1);
    vks_post_fn(wb_land, w);
}
// Writes whose bytes the CPU knows (inline uniform updates, fills): the GPU writes the mirror in order, guest memory gets the same bytes at completion
void vkm_cpu_also(uint64_t addr, uint64_t len, const void *data, int fill, uint32_t value) {
    if (pool_placed_at(addr, len) && !getenv("NXRT_VK_PL_CPUALSO")) return;   // the GPU writes the guest's own pages
    wb_t *w = calloc(1, sizeof *w); w->addr = addr; w->len = len; w->kind = fill ? 2 : 1; w->fill = value;
    if (!fill) { uint8_t *c = malloc((size_t)len); memcpy(c, data, (size_t)len); w->cpu = c; }
    gpu_pending(addr, len, 1);
    vks_post_fn(wb_land, w);
}

// ---------- Frames, statistics ----------
void vkm_stats(vkm_stats_t *s) {
    memset(s, 0, sizeof *s);
    AcquireSRWLockShared(&g_lk);
    for (int i = 0; i < g_npool; i++) { s->pools++; s->pool_bytes += g_pool[i]->hi - g_pool[i]->lo; s->bound_bytes += g_pool[i]->bound; s->sparse_pools += g_pool[i]->sparse; s->hot_pages += g_pool[i]->hot_n; }
    ReleaseSRWLockShared(&g_lk);
    s->up_bytes = atomic_load(&s_up_bytes); s->up_pages = atomic_load(&s_up_pages); s->up_runs = atomic_load(&s_up_runs); s->up_ns = atomic_load(&s_up_ns);
    s->sync_calls = atomic_load(&s_sync); s->staging_bytes = atomic_load(&s_st_bytes); s->staging_overflow = atomic_load(&s_st_ovf); s->staging_waits = atomic_load(&s_st_waits);
    s->writeback_bytes = atomic_load(&s_wb_bytes); s->uncommitted_pages = atomic_load(&s_uncommitted);
    nvn_dirty_stats(&s->faults, &s->preps);
    uint64_t mc, mpg; nvn_dirty_mp_stats(&mc, &mpg, &s->fault_ns);   // the tracker's protection changes (faults and protects)
}

void vkm_frame_tick(uint64_t frame) {
    static vkm_stats_t prev; static uint64_t prev_frame, t_prev;
    g_frame = frame + 1; g_budget_used = 0;
    if (frame % HOT_COOL_FRAMES == 0) {   // hot pages cool down: protected again at their next copy; written again, they come back within HOT_STREAK frames
        AcquireSRWLockShared(&g_lk);
        for (int i = 0; i < g_npool; i++) { pool_t_ *p = g_pool[i]; for (uint64_t k = 0; k < p->npages; k++) if (p->hot[k]) { p->hot[k] = 0; p->up[k] = 0; p->streak[k] = 0; } p->hot_n = 0; }   // copied (and protected) again at the next sync
        ReleaseSRWLockShared(&g_lk);
    }
    if (frame % 300 || frame == prev_frame) return;
    vkm_stats_t s; vkm_stats(&s);
    uint64_t df = frame - prev_frame, now = now_ns(); double secs = t_prev ? (now - t_prev) / 1e9 : 0;
    uint64_t budget = 0, usage = 0; vk_budget(&budget, &usage);
    VKLOG("memory up to frame %llu: %llu pools %.1f MB (backed %.1f MB, %llu sparse); per frame: uploaded %.1f KB in %.1f runs (%.3f ms CPU), %.1f write faults (protection changes %.3f ms), "
          "%.1f host pre-writes, written back %.1f KB, %.0f MB of uncommitted pool stepped over; hot pages %llu; staging overflow %llu waits %llu; video memory %.0f / %.0f MB%s",
          (unsigned long long)frame, (unsigned long long)s.pools, s.pool_bytes / 1048576.0, s.bound_bytes / 1048576.0, (unsigned long long)s.sparse_pools,
          (s.up_bytes - prev.up_bytes) / 1024.0 / df, (double)(s.up_runs - prev.up_runs) / df, (s.up_ns - prev.up_ns) / 1e6 / df,
          (double)(s.faults - prev.faults) / df, (s.fault_ns - prev.fault_ns) / 1e6 / df, (double)(s.preps - prev.preps) / df,
          (s.writeback_bytes - prev.writeback_bytes) / 1024.0 / df, (s.uncommitted_pages - prev.uncommitted_pages) * 4096.0 / 1048576.0 / df, (unsigned long long)s.hot_pages, (unsigned long long)s.staging_overflow,
          (unsigned long long)s.staging_waits, usage / 1048576.0, budget / 1048576.0, secs > 0 ? "" : "");
    prev = s; prev_frame = frame; t_prev = now;
}

int vkm_init(void) {
    InitializeSRWLock(&g_st.lk);
    const char *e = getenv("NXRT_VK_UPLOAD_MB"); if (e && atoi(e) > 0) g_budget = (uint64_t)atoi(e) << 20;
    e = getenv("NXRT_VK_SCAN_PAGES"); if (e && atoi(e) > 0) g_scan_pages = (uint64_t)atoi(e);
    VkDeviceSize ring = g_budget < (16u << 20) ? (16u << 20) : g_budget > (256u << 20) ? (256u << 20) : g_budget;
    e = getenv("NXRT_VK_STAGING_MB"); if (e && atoi(e) > 0) ring = (VkDeviceSize)atoi(e) << 20;
    g_usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT |
              VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT |
              VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT;
    if (g_vk.f12.bufferDeviceAddress) g_usage |= VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    if (g_vk.has.xfb) g_usage |= VK_BUFFER_USAGE_TRANSFORM_FEEDBACK_BUFFER_BIT_EXT | VK_BUFFER_USAGE_TRANSFORM_FEEDBACK_COUNTER_BUFFER_BIT_EXT;
    if (g_vk.has.cond_render) g_usage |= VK_BUFFER_USAGE_CONDITIONAL_RENDERING_BIT_EXT;
    VkPhysicalDeviceMaintenance4Properties m4 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_PROPERTIES};
    VkPhysicalDeviceProperties2 p2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &m4};
    vkGetPhysicalDeviceProperties2(g_vk.pd, &p2);
    g_max_buffer = m4.maxBufferSize;
    e = getenv("NXRT_VK_MAX_BUFFER_MB");   // test switch: pretend the device's largest buffer is smaller (AMD's 2048, to try its pool windows on another card)
    if (e && atoi(e) > 0 && ((VkDeviceSize)atoi(e) << 20) < g_max_buffer) g_max_buffer = (VkDeviceSize)atoi(e) << 20;
    g_alias = g_vk.has.sparse_aliased && !(getenv("NXRT_VK_NO_ALIAS") && atoi(getenv("NXRT_VK_NO_ALIAS")));
#ifndef _WIN32
    { const char *pl = getenv("NXRT_VK_PLACED");
      g_placed = g_vk.has.placed && g_vk.has.map_memory2 && vkMapMemory2KHR && vkUnmapMemory2KHR && !(pl && *pl == '0');
      VKLOG("memory: pools %s", g_placed ? "placed at their guest addresses (no mirrors)" : "mirrored"); }
#endif
    if (g_vk.has.sparse_buffer) {   // the granule is a whole number of the device's sparse blocks
        VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, VK_BUFFER_CREATE_SPARSE_BINDING_BIT | VK_BUFFER_CREATE_SPARSE_RESIDENCY_BIT, GRAN, g_usage, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
        VkBuffer b; VkMemoryRequirements mr;
        if (vkCreateBuffer(g_vk.dev, &bci, NULL, &b) == VK_SUCCESS) { vkGetBufferMemoryRequirements(g_vk.dev, b, &mr); vkDestroyBuffer(g_vk.dev, b, NULL);
            if (mr.alignment > g_gran) g_gran = mr.alignment; else g_gran = (GRAN + mr.alignment - 1) / mr.alignment * mr.alignment; }
    }
    VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, ring, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
    if (vkCreateBuffer(g_vk.dev, &bci, NULL, &g_st.buf) != VK_SUCCESS) return -1;
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(g_vk.dev, g_st.buf, &mr);
    if (vk_alloc_mem(&mr, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, &g_st.mem)) { VKRED("no host memory for the staging ring"); return -1; }
    vkBindBufferMemory(g_vk.dev, g_st.buf, g_st.mem.mem, g_st.mem.off);
    g_st.map = g_st.mem.map; g_st.size = ring;
    vk_name((uint64_t)g_st.buf, VK_OBJECT_TYPE_BUFFER, "staging ring");
    VKLOG("memory: staging ring %llu MB, upload budget %llu MB a frame, pools %s (granule %llu KB), largest buffer %llu MB (maxBufferSize %llu MB), sparse aliasing %s%s",
          (unsigned long long)(ring >> 20), (unsigned long long)(g_budget >> 20), g_vk.has.sparse_buffer ? "sparse" : "fully backed", (unsigned long long)(g_gran >> 10),
          (unsigned long long)(g_max_buffer >> 20), (unsigned long long)(m4.maxBufferSize >> 20), g_vk.has.sparse_aliased ? "yes" : "no", g_alias ? "" : " (pool windows side by side)");
    return 0;
}

void vkm_shutdown(void) {
    AcquireSRWLockExclusive(&g_lk);
    for (int i = 0; i < g_npool; i++) pool_free(g_pool[i]);
    g_npool = 0;
    ReleaseSRWLockExclusive(&g_lk);
    AcquireSRWLockExclusive(&g_st.lk);
    for (unsigned k = g_st.qt; k != g_st.qh; k++) { unsigned i = k % 4096; if (g_st.q[i].ovf) { vkDestroyBuffer(g_vk.dev, g_st.q[i].ovf, NULL); vk_free_mem(&g_st.q[i].ovf_mem); } }
    g_st.qt = g_st.qh = 0;
    if (g_st.buf) { vkDestroyBuffer(g_vk.dev, g_st.buf, NULL); vk_free_mem(&g_st.mem); g_st.buf = VK_NULL_HANDLE; }
    ReleaseSRWLockExclusive(&g_st.lk);
}
