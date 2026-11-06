// ASTC -> BC7 with a persistent cache (vk_astc.h; docs/m5-vulkan.md §4.3). astc-encoder 5.7.0 (Apache-2.0, third_party/astcenc) decodes, bc7enc
// (MIT / Unlicense, third_party/bc7enc) encodes. Written from their public headers.
#include "vk_astc.h"
#include "nxrt/path_sep.h"
#include "third_party/astcenc/astcenc.h"
#include "third_party/bc7enc/bc7enc.h"
#include "third_party/bc7enc/bc7decomp.h"
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shlobj.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <vector>

extern "C" void nxrt_logf(const char *fmt, ...);
extern "C" uint64_t now_ns(void);
#define ALOG(...) nxrt_logf("[vk] " __VA_ARGS__)

// The cache key covers the encoder: change it when the BC7 settings change, and every cached file is made again
static const uint64_t VKA_VERSION = 0x5637414bull + 1;   // "KA7V" + revision
static const uint32_t MAGIC = 0x43374342u;               // "BC7C"
struct file_hdr { uint32_t magic, version, w, h, slices, quality; uint64_t size, check; };

static wchar_t g_dir[MAX_PATH];
static std::atomic<uint64_t> s_hits_high, s_hits_fast, s_miss, s_fast_ns, s_fast_tex, s_high_done, s_high_ns, s_high_tex, s_queued, s_bytes;

// ---------- Hashing (64-bit multiply-mix over 8-byte words, two seeds -> 128 bits) ----------
static inline uint64_t mix(uint64_t a, uint64_t b) { unsigned __int128 m = (unsigned __int128)a * b; return (uint64_t)m ^ (uint64_t)(m >> 64); }
static uint64_t hash64(const uint8_t *p, size_t n, uint64_t seed) {
    uint64_t h = seed ^ (n * 0x9E3779B97F4A7C15ull);
    size_t i = 0;
    for (; i + 16 <= n; i += 16) { uint64_t a, b; memcpy(&a, p + i, 8); memcpy(&b, p + i + 8, 8); h = mix(a ^ 0xa0761d6478bd642full ^ h, b ^ 0xe7037ed1a0b428dbull); }
    uint64_t t[2] = {0, 0}; memcpy(t, p + i, n - i);
    return mix(h ^ t[0] ^ 0x8ebc6af09c88c6e3ull, t[1] ^ 0x589965cc75374cc3ull ^ n);
}
static vka_key make_key(const uint8_t *astc, size_t n, uint32_t w, uint32_t h, uint32_t s, int bw, int bh, int srgb) {
    uint64_t shape = (uint64_t)w | (uint64_t)h << 20 | (uint64_t)s << 40 | (uint64_t)bw << 52 | (uint64_t)bh << 56 | (uint64_t)srgb << 60;
    vka_key k; k.lo = hash64(astc, n, VKA_VERSION ^ shape); k.hi = hash64(astc, n, ~VKA_VERSION ^ (shape * 0x2545F4914F6CDD1Dull)); return k;
}

static void path_of(const vka_key &k, int q, wchar_t *out) { _snwprintf(out, MAX_PATH, L"%ls" NXRT_WSEP L"%016llx%016llx-q%d.bc7", g_dir, (unsigned long long)k.hi, (unsigned long long)k.lo, q); }
static int cache_read(const vka_key &k, int q, uint8_t *out, size_t size, uint32_t w, uint32_t h, uint32_t s) {
    if (!g_dir[0]) return 0;
    wchar_t p[MAX_PATH]; path_of(k, q, p);
    FILE *f = _wfopen(p, L"rb"); if (!f) return 0;
    file_hdr hd; int ok = fread(&hd, sizeof hd, 1, f) == 1 && hd.magic == MAGIC && hd.version == (uint32_t)VKA_VERSION && hd.w == w && hd.h == h && hd.slices == s &&
                          hd.size == size && fread(out, 1, size, f) == size && hash64(out, size, 7) == hd.check;
    fclose(f);
    return ok;
}
static void cache_write(const vka_key &k, int q, const uint8_t *data, size_t size, uint32_t w, uint32_t h, uint32_t s) {
    if (!g_dir[0]) return;
    wchar_t p[MAX_PATH], t[MAX_PATH]; path_of(k, q, p); _snwprintf(t, MAX_PATH, L"%ls.%lu.tmp", p, GetCurrentThreadId());
    file_hdr hd = {MAGIC, (uint32_t)VKA_VERSION, w, h, s, (uint32_t)q, size, hash64(data, size, 7)};
    FILE *f = _wfopen(t, L"wb"); if (!f) return;
    int ok = fwrite(&hd, sizeof hd, 1, f) == 1 && fwrite(data, 1, size, f) == size;
    ok = fclose(f) == 0 && ok;
    if (ok && MoveFileExW(t, p, MOVEFILE_REPLACE_EXISTING)) s_bytes += size; else DeleteFileW(t);
}

// ---------- Decoding: one astc-encoder context per thread and block size ----------
struct ctx_ent { int bw, bh, srgb; astcenc_context *c; };
static thread_local std::vector<ctx_ent> t_ctx;
static astcenc_context *ctx_get(int bw, int bh, int srgb) {
    for (auto &e : t_ctx) if (e.bw == bw && e.bh == bh && e.srgb == srgb) return e.c;
    astcenc_config cfg; astcenc_context *c = nullptr;
    if (astcenc_config_init(srgb ? ASTCENC_PRF_LDR_SRGB : ASTCENC_PRF_LDR, (unsigned)bw, (unsigned)bh, 1, ASTCENC_PRE_FASTEST, ASTCENC_FLG_DECOMPRESS_ONLY, &cfg) != ASTCENC_SUCCESS) return nullptr;
    if (astcenc_context_alloc(&cfg, 1, &c, nullptr) != ASTCENC_SUCCESS) return nullptr;
    t_ctx.push_back({bw, bh, srgb, c});
    return c;
}
extern "C" int vka_decode_rgba8(const uint8_t *astc, uint32_t w, uint32_t h, uint32_t slices, int bw, int bh, int srgb, uint8_t *rgba) {
    astcenc_context *c = ctx_get(bw, bh, srgb);
    if (!c || !w || !h || !slices) return -1;
    size_t blocks = (size_t)((w + bw - 1) / bw) * ((h + bh - 1) / bh), slice_bytes = blocks * 16;
    for (uint32_t z = 0; z < slices; z++) {
        void *plane = rgba + (size_t)z * w * h * 4;
        astcenc_image img = {w, h, 1, ASTCENC_TYPE_U8, &plane};
        astcenc_swizzle sw = {ASTCENC_SWZ_R, ASTCENC_SWZ_G, ASTCENC_SWZ_B, ASTCENC_SWZ_A};
        if (astcenc_decompress_image(c, astc + z * slice_bytes, slice_bytes, &img, &sw, 0) != ASTCENC_SUCCESS) return -1;
        astcenc_decompress_reset(c);
    }
    return 0;
}

// NXRT_VK_ASTC_ASYNC (on unless 0): nothing is transcoded where a large texture is first used. It starts out black and the workers' results replace it,
// smallest first, and go to the cache: first use of a big texture must not stall the frame (a whole level of an 8192x8192 takes seconds on a handheld)
static int g_async;

// ---------- Encoding: bc7enc, fast (mode 6 alone) or high quality (modes 1 and 6, partitions, uber level 1); the fast pass on a few helper threads ----------
static void params(int q, bc7enc_compress_block_params *p) {
    bc7enc_compress_block_params_init(p);
    if (q == VKA_FAST) { p->m_max_partitions = 0; p->m_uber_level = 0; p->m_try_least_squares = false; p->m_mode17_partition_estimation_filterbank = true; bc7enc_compress_block_params_init_linear_weights(p); }
    else { p->m_max_partitions = BC7ENC_MAX_PARTITIONS; p->m_uber_level = 1; p->m_try_least_squares = true; bc7enc_compress_block_params_init_linear_weights(p); }   // RGB error: the reference is the ASTC decode, texel for texel
}
static void encode_rows(const uint8_t *rgba, uint32_t w, uint32_t h, uint32_t by0, uint32_t by1, const bc7enc_compress_block_params *p, uint8_t *bc7) {
    uint32_t bx_n = (w + 3) / 4;
    uint8_t px[64];
    for (uint32_t by = by0; by < by1; by++)
        for (uint32_t bx = 0; bx < bx_n; bx++) {
            for (uint32_t y = 0; y < 4; y++) for (uint32_t x = 0; x < 4; x++) {   // edge texels repeat into the blocks past the image
                uint32_t sx = bx * 4 + x, sy = by * 4 + y; if (sx >= w) sx = w - 1; if (sy >= h) sy = h - 1;
                memcpy(px + (y * 4 + x) * 4, rgba + ((size_t)sy * w + sx) * 4, 4);
            }
            bc7enc_compress_block(bc7 + ((size_t)by * bx_n + bx) * 16, px, p);
        }
}
static std::once_flag g_bc7_once;
static unsigned g_helpers;
extern "C" int vka_encode_bc7(const uint8_t *rgba, uint32_t w, uint32_t h, int quality, uint8_t *bc7) {
    std::call_once(g_bc7_once, [] { bc7enc_compress_block_init(); unsigned n = std::thread::hardware_concurrency(); g_helpers = g_async ? 0 : n >= 16 ? 3 : n >= 8 ? 1 : 0; });
    bc7enc_compress_block_params p; params(quality, &p);
    uint32_t rows = (h + 3) / 4;
    if (quality != VKA_FAST || g_helpers == 0 || (uint64_t)w * h < 256 * 256) { encode_rows(rgba, w, h, 0, rows, &p, bc7); return 0; }
    // The fast pass runs where the texture is first used: split over at most 4 threads (this PC must not see long all-core loads)
    unsigned n = g_helpers + 1; std::vector<std::thread> th;
    for (unsigned i = 1; i < n; i++) th.emplace_back([=, &p] { encode_rows(rgba, w, h, rows * i / n, rows * (i + 1) / n, &p, bc7); });
    encode_rows(rgba, w, h, 0, rows / n, &p, bc7);
    for (auto &t : th) t.join();
    return 0;
}
extern "C" int vka_decode_bc7_block(const uint8_t *block, uint8_t rgba[64]) { return bc7decomp::unpack_bc7(block, (bc7decomp::color_rgba *)rgba) ? 0 : -1; }
extern "C" size_t vka_bc7_size(uint32_t w, uint32_t h, uint32_t slices) { return (size_t)((w + 3) / 4) * ((h + 3) / 4) * 16 * slices; }

// ---------- The high-quality worker ----------
struct job { vka_key key; std::vector<uint8_t> astc; uint32_t w, h, s; int bw, bh, srgb; };
struct ready { vka_key key; std::vector<uint8_t> bc7; };
static std::mutex g_mu; static std::condition_variable g_cv, g_idle_cv;
static std::deque<job> g_jobs; static std::deque<ready> g_ready; static int g_busy; static bool g_stop;
enum { MAX_WORKERS = 6 }; static std::thread g_workers[MAX_WORKERS]; static int g_nworkers;

static int transcode(const uint8_t *astc, uint32_t w, uint32_t h, uint32_t s, int bw, int bh, int srgb, int q, uint8_t *bc7) {
    std::vector<uint8_t> rgba((size_t)w * h * 4 * s);
    if (vka_decode_rgba8(astc, w, h, s, bw, bh, srgb, rgba.data())) return -1;
    size_t per = vka_bc7_size(w, h, 1);
    for (uint32_t z = 0; z < s; z++) vka_encode_bc7(rgba.data() + (size_t)z * w * h * 4, w, h, q, bc7 + z * per);
    return 0;
}
static void worker_main() {
    SetThreadDescription(GetCurrentThread(), L"vk astc->bc7");
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_LOWEST);
    std::unique_lock<std::mutex> lk(g_mu);
    for (;;) {
        g_cv.wait(lk, [] { return g_stop || !g_jobs.empty(); });
        if (g_stop) break;
        auto best = g_jobs.begin();
        if (g_async) for (auto it = g_jobs.begin(); it != g_jobs.end(); ++it) if ((uint64_t)it->w * it->h * it->s < (uint64_t)best->w * best->h * best->s) best = it;
        job j = std::move(*best); g_jobs.erase(best); g_busy++;
        lk.unlock();
        uint64_t t0 = now_ns();
        std::vector<uint8_t> out(vka_bc7_size(j.w, j.h, j.s));
        int ok = !transcode(j.astc.data(), j.w, j.h, j.s, j.bw, j.bh, j.srgb, g_async ? VKA_FAST : VKA_HIGH, out.data());
        if (ok) cache_write(j.key, VKA_HIGH, out.data(), out.size(), j.w, j.h, j.s);
        s_high_ns += now_ns() - t0; s_high_done++; s_high_tex += (uint64_t)j.w * j.h * j.s;
        lk.lock();
        if (ok) g_ready.push_back({j.key, std::move(out)});
        g_busy--; g_idle_cv.notify_all();
    }
}

// Until the workers are done: the level's average colour, from up to 8x8 of its blocks (a black texture on a track reads as a black world)
static void flat_colour(const uint8_t *astc, uint32_t w, uint32_t h, int bw, int bh, int srgb, uint8_t *bc7, size_t out) {
    uint32_t nx = (w + bw - 1) / bw, ny = (h + bh - 1) / bh, gx = nx < 8 ? nx : 8, gy = ny < 8 ? ny : 8;
    std::vector<uint8_t> blocks((size_t)gx * gy * 16), rgba((size_t)gx * bw * gy * bh * 4);
    for (uint32_t y = 0; y < gy; y++) for (uint32_t x = 0; x < gx; x++)
        memcpy(&blocks[((size_t)y * gx + x) * 16], astc + ((size_t)(y * ny / gy) * nx + x * nx / gx) * 16, 16);
    memset(bc7, 0, out);
    if (vka_decode_rgba8(blocks.data(), gx * bw, gy * bh, 1, bw, bh, srgb, rgba.data())) return;
    uint64_t sum[4] = {0, 0, 0, 0}; size_t n = rgba.size() / 4;
    for (size_t i = 0; i < n; i++) for (int c = 0; c < 4; c++) sum[c] += rgba[i * 4 + c];
    uint8_t px[64], block[16];
    for (int i = 0; i < 16; i++) for (int c = 0; c < 4; c++) px[i * 4 + c] = (uint8_t)(sum[c] / n);
    if (vka_encode_bc7(px, 4, 4, VKA_FAST, block)) return;
    for (size_t at = 0; at + 16 <= out; at += 16) memcpy(bc7 + at, block, 16);
}

extern "C" int vka_get_bc7(const uint8_t *astc, uint32_t w, uint32_t h, uint32_t s, int bw, int bh, int srgb, uint8_t *bc7, vka_key *key_out) {
    size_t n = (size_t)((w + bw - 1) / bw) * ((h + bh - 1) / bh) * 16 * s, out = vka_bc7_size(w, h, s);
    vka_key k = make_key(astc, n, w, h, s, bw, bh, srgb);
    if (key_out) *key_out = k;
    if (cache_read(k, VKA_HIGH, bc7, out, w, h, s)) { s_hits_high++; return VKA_HIGH; }
    int fast = cache_read(k, VKA_FAST, bc7, out, w, h, s);
    if (fast) { s_hits_fast++; if (g_async) return VKA_HIGH; }
    else if (g_async && (uint64_t)w * h * s > 128 * 128) { s_miss++; flat_colour(astc, w, h, bw, bh, srgb, bc7, out); }
    else {
        s_miss++;
        uint64_t t0 = now_ns();
        if (transcode(astc, w, h, s, bw, bh, srgb, VKA_FAST, bc7)) return -1;
        s_fast_ns += now_ns() - t0; s_fast_tex += (uint64_t)w * h * s;
        cache_write(k, VKA_FAST, bc7, out, w, h, s);
        if (g_async) return VKA_HIGH;
    }
    {   // the high-quality pass, once per key
        std::lock_guard<std::mutex> lk(g_mu);
        bool queued = false; for (auto &j : g_jobs) if (j.key.lo == k.lo && j.key.hi == k.hi) queued = true;
        if (!queued && g_nworkers) { g_jobs.push_back({k, std::vector<uint8_t>(astc, astc + n), w, h, s, bw, bh, srgb}); s_queued++; g_cv.notify_one(); }
    }
    return VKA_FAST;
}

extern "C" int vka_poll(vka_ready_fn fn, void *user, int max) {
    int done = 0;
    while (done < max) {
        ready r;
        { std::lock_guard<std::mutex> lk(g_mu); if (g_ready.empty()) break; r = std::move(g_ready.front()); g_ready.pop_front(); }
        fn(r.key, r.bc7.data(), r.bc7.size(), user); done++;
    }
    return done;
}
extern "C" void vka_wait_idle(void) { std::unique_lock<std::mutex> lk(g_mu); g_idle_cv.wait(lk, [] { return g_jobs.empty() && !g_busy; }); }

extern "C" int vka_init(const char *dir) {
    if (dir && *dir) MultiByteToWideChar(CP_UTF8, 0, dir, -1, g_dir, MAX_PATH);
    else {
        const char *e = getenv("NXRT_VK_CACHE_DIR"); wchar_t base[MAX_PATH] = L"";
        if (e && *e) MultiByteToWideChar(CP_UTF8, 0, e, -1, base, MAX_PATH);
        else { wchar_t *la = nullptr; if (SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &la) == S_OK) { _snwprintf(base, MAX_PATH, L"%ls" NXRT_WSEP L"pokopia-native", la); CreateDirectoryW(base, nullptr); _snwprintf(base, MAX_PATH, L"%ls" NXRT_WSEP L"pokopia-native" NXRT_WSEP L"vulkan", la); CoTaskMemFree(la); } }
        if (base[0]) { CreateDirectoryW(base, nullptr); _snwprintf(g_dir, MAX_PATH, L"%ls" NXRT_WSEP L"astc-bc7", base); }
    }
    if (g_dir[0]) CreateDirectoryW(g_dir, nullptr);
    { std::lock_guard<std::mutex> lk(g_mu); g_stop = false; }
    const char *ae = getenv("NXRT_VK_ASTC_ASYNC"), *we = getenv("NXRT_VK_ASTC_WORKERS");
    g_async = !(ae && *ae == '0');
    int want = we && *we ? atoi(we) : g_async ? 3 : 1;
    if (want < 1) want = 1; if (want > MAX_WORKERS) want = MAX_WORKERS;
    while (g_nworkers < want) g_workers[g_nworkers++] = std::thread(worker_main);
    ALOG("ASTC -> BC7 cache %ls (%ls, %d workers)", g_dir[0] ? g_dir : L"(none)", g_async ? L"all in the background" : L"fast pass at first use, high quality in the background", g_nworkers);
    return 0;
}
extern "C" void vka_shutdown(void) {
    { std::lock_guard<std::mutex> lk(g_mu); g_stop = true; g_jobs.clear(); }
    g_cv.notify_all();
    for (int i = 0; i < g_nworkers; i++) if (g_workers[i].joinable()) g_workers[i].join();
    g_nworkers = 0;
    std::lock_guard<std::mutex> lk(g_mu); g_ready.clear();
}
extern "C" void vka_stats(vka_stats_t *s) {
    s->hits_high = s_hits_high; s->hits_fast = s_hits_fast; s->misses = s_miss; s->fast_ns = s_fast_ns; s->fast_texels = s_fast_tex;
    s->high_done = s_high_done; s->high_ns = s_high_ns; s->high_texels = s_high_tex; s->queued = s_queued; s->cache_bytes = s_bytes;
}
