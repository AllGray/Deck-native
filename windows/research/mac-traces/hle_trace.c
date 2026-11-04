// Research tracing for the Windows port's open questions (windows-native research/05-mac-traces.md). It lives only in the
// nxnative-trace copy on the Mac and is off unless DM_TRACE=<directory> is set; results are written into that directory.
//   always (with DM_TRACE): FPCR at every gate (svc, counter read, spin wait) per guest thread, and the calls per gate
//   DM_TRACE_PROBES=<list>: persistent probes (brk #0xf0e2), entries "=symbol" or "module:offset", comma separated. Each hit
//     logs time, thread, lr, the frame's return address, fpcr and one value (x1 for __cxa_throw, the moved value for
//     msr/mrs fpcr/fpsr, x0 otherwise). The displaced instruction runs from a slot near the module that branches back;
//     a first instruction that is b, bl, adr or adrp is emulated instead
//   DM_TRACE_COV=<directory with fde_<module>.txt>: one-shot brk #0xf0e1 on every FDE start and every nonzero text word outside
//     the FDEs; the first execution logs it and puts the word back through a writable alias. Every br/blr (brk #0xf0e3) is
//     emulated for its first DM_TRACE_IND executions (default 8), logging the target, then put back
//   DM_TRACE_HZ=<n>: a sampler thread suspends each thread n times a second, counts guest pcs per text word and fpcr per thread
// The writer thread appends the logs every 5 s and rewrites the tables every 30 s; touching <dir>/flush writes everything
// and answers with <dir>/flushed.
#include "hle.h"
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/thread_info.h>
#include <libkern/OSCacheControl.h>
#include <sys/ucontext.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <stdatomic.h>
#include <unistd.h>

int g_trace_on;
static char g_dir[512];
extern uint64_t g_t0_ns;
uint32_t thr_cur_handle(void);
void crash_set_trap_hook(int (*fn)(ucontext_t *uc));
int (*crash_get_trap_hook(void))(ucontext_t *uc);
static int (*g_prev_trap)(ucontext_t *uc);

#define BRK_COV 0xD43E1C20u   // brk #0xf0e1
#define BRK_PRB 0xD43E1C40u   // brk #0xf0e2
#define BRK_IND 0xD43E1C60u   // brk #0xf0e3
#define KBIT (1ull << 63)     // keeps table keys nonzero
enum { ST_NONE, ST_ARMED, ST_HIT, ST_PROBE, ST_IND, ST_INDDONE };

static inline uint32_t t_ms(void) { return (uint32_t)((now_ns() - g_t0_ns) / 1000000ull); }

// ---------- modules ----------
typedef struct {
    module_t *m; uint64_t base, text, rx;   // text = text_end; rx = text rounded up to whole pages
    uint8_t *alias;                         // writable second mapping of [base, base + rx)
    _Atomic uint8_t *st;                    // per text word: ST_*
    uint32_t *orig;                         // the words as they were before arming
    _Atomic uint8_t *icnt;                  // per br/blr word: emulated executions so far
    uint32_t *samp;                         // sampler pcs per text word
    uint64_t narm_entry, narm_out, narm_ind;
} tmod_t;
#define TMAX 8
static tmod_t g_tm[TMAX]; static int g_ntm;
static inline int tm_text(uint64_t a) {
    for (int i = 0; i < g_ntm; i++) if (a >= g_tm[i].base && a < g_tm[i].base + g_tm[i].text) return i;
    return -1;
}
static inline int tm_any(uint64_t a) {   // anywhere in the module's mapping (patch areas included)
    for (int i = 0; i < g_ntm; i++) {
        module_t *m = g_tm[i].m; uint64_t b = g_tm[i].base;
        if (a >= b && a < b + m->size) return i;
        if (m->patch_off < 0 && a >= b + m->patch_off && a < b + m->patch_off + m->patch_len) return i;
    }
    return -1;
}

// ---------- lock-free tables and logs ----------
typedef struct { _Atomic uint64_t k, n; } kv_t;
static void kv_add(kv_t *t, unsigned cap, uint64_t key) {
    unsigned h = (unsigned)((key * 0x9E3779B97F4A7C15ull) >> 40) & (cap - 1);
    for (unsigned i = 0; i < cap; i++, h = (h + 1) & (cap - 1)) {
        uint64_t k = atomic_load_explicit(&t[h].k, memory_order_relaxed);
        if (k == key) { atomic_fetch_add_explicit(&t[h].n, 1, memory_order_relaxed); return; }
        if (!k) {
            uint64_t z = 0;
            if (atomic_compare_exchange_strong(&t[h].k, &z, key) || z == key) { atomic_fetch_add_explicit(&t[h].n, 1, memory_order_relaxed); return; }
        }
    }
}
#define KV_PATCH 65536
static kv_t g_kv_patch[KV_PATCH];     // sampled pcs in a module but outside its text (nxnative's patch areas): module << 56 | offset from the base
// The rewriter's branches out of the text (b / bl from an original site to its block in a patch area), sorted by target: a patch-area pc
// belongs to the site whose block starts at or before it
typedef struct { int64_t tgt; uint32_t site; } rwb_t;
static rwb_t *g_rwb[8]; static size_t g_nrwb[8];
static int rwb_cmp(const void *a, const void *b) { int64_t x = ((const rwb_t *)a)->tgt, y = ((const rwb_t *)b)->tgt; return x < y ? -1 : x > y; }
#define KV_GATEFP 1024
#define KV_GATES 4096
#define KV_COVFP 64
static kv_t g_kv_gatefp[KV_GATEFP];   // (guest thread handle << 32 | fpcr) at gates
static kv_t g_kv_gates[KV_GATES];     // (module << 60 | kind << 56 | arg << 48 | original offset) per gate
static kv_t g_kv_covfp[KV_COVFP];     // fpcr at first executions (coverage traps)

typedef struct { _Atomic uint32_t ms1; uint16_t probe, tid; uint32_t fpcr, pad; uint64_t lr, lr2, extra; } prec_t;   // probe hit
typedef struct { _Atomic uint32_t ms1; uint16_t tid; uint8_t kind, pad; uint64_t at; } crec_t;                       // first execution
typedef struct { _Atomic uint32_t ms1; uint16_t tid, pad; uint64_t at, target; } irec_t;                             // indirect branch target
typedef struct { void *buf; size_t rec, cap; _Atomic uint64_t n; uint64_t written; const char *file; } tlog_t;
static tlog_t g_lprb = { .rec = sizeof(prec_t), .cap = 1u << 23, .file = "probe_log.bin" };
static tlog_t g_lcov = { .rec = sizeof(crec_t), .cap = 1u << 22, .file = "cov_log.bin" };
static tlog_t g_lind = { .rec = sizeof(irec_t), .cap = 1u << 22, .file = "ind_log.bin" };
static void *tlog_slot(tlog_t *l) {
    uint64_t i = atomic_fetch_add_explicit(&l->n, 1, memory_order_relaxed);
    return l->buf && i < l->cap ? (char *)l->buf + i * l->rec : NULL;
}
static void tlog_init(tlog_t *l) {
    l->buf = mmap(NULL, l->rec * l->cap, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (l->buf == MAP_FAILED) die("trace: log mmap");
}
static _Atomic uint64_t g_secs[4096][3];   // per second since process start: counter reads, svcs, other gates

// ---------- probes ----------
enum { EMU_SLOT, EMU_B, EMU_BL, EMU_ADR };
typedef struct { uint64_t at, target; uint32_t orig, *slot; int emu, rd, x1; char label[64]; _Atomic uint64_t hits; } tprobe_t;
#define TPMAX 64
static tprobe_t g_tp[TPMAX]; static int g_ntp;

static uint64_t reg_of(_STRUCT_ARM_THREAD_STATE64 *ss, unsigned r) { return r == 31 ? 0 : r == 29 ? ss->__fp : r == 30 ? ss->__lr : ss->__x[r]; }
static void probe_fire(int pi, ucontext_t *uc) {
    tprobe_t *p = &g_tp[pi];
    _STRUCT_ARM_THREAD_STATE64 *ss = &uc->uc_mcontext->__ss;
    atomic_fetch_add_explicit(&p->hits, 1, memory_order_relaxed);
    uint32_t in = p->orig; uint64_t extra;
    if ((in & 0xFFFFFFC0u) == 0xD51B4400u) extra = reg_of(ss, in & 31);                       // msr fpcr / fpsr, xT: the value written
    else if ((in & 0xFFFFFFE0u) == 0xD53B4400u) extra = uc->uc_mcontext->__ns.__fpcr;          // mrs xT, fpcr: the value read
    else if ((in & 0xFFFFFFE0u) == 0xD53B4420u) extra = uc->uc_mcontext->__ns.__fpsr;          // mrs xT, fpsr
    else extra = p->x1 ? ss->__x[1] : ss->__x[0];
    prec_t *r = tlog_slot(&g_lprb);
    if (r) {
        uint64_t fp = ss->__fp, w = 0; vm_size_t got = 0;
        if (fp > 0x100000000ull && !(fp & 7) && !vm_read_overwrite(mach_task_self(), fp + 8, 8, (vm_address_t)&w, &got) && got == 8) r->lr2 = w; else r->lr2 = 0;
        r->probe = (uint16_t)pi; r->tid = (uint16_t)thr_cur_handle(); r->fpcr = uc->uc_mcontext->__ns.__fpcr;
        r->lr = ss->__lr; r->extra = extra;
        atomic_store_explicit(&r->ms1, t_ms() + 1, memory_order_release);
    }
    switch (p->emu) {
        case EMU_B: ss->__pc = p->target; break;
        case EMU_BL: ss->__lr = p->at + 4; ss->__pc = p->target; break;
        case EMU_ADR: if (p->rd == 29) ss->__fp = p->target; else if (p->rd == 30) ss->__lr = p->target; else if (p->rd != 31) ss->__x[p->rd] = p->target;
                      ss->__pc = p->at + 4; break;
        default: ss->__pc = (uint64_t)p->slot;
    }
}

// A page for the probes' slots within reach of a b back (±128 MB). trace_install reserves one per module before its large
// allocations, which would otherwise take the free space right after the modules
static struct { uint64_t a; int used; } g_spg[8]; static int g_nspg;
static uint32_t *slot_near(uint64_t at, int reserve) {
    for (int i = 0; i < g_nspg && !reserve; i++) {
        int64_t d = (int64_t)(g_spg[i].a - at);
        if (d > -(120ll << 20) && d < (120ll << 20) && g_spg[i].used < 2000) return (uint32_t *)g_spg[i].a + 2 * g_spg[i].used++;
    }
    if (g_nspg == 8) die("trace: too many slot pages");
    int mi = tm_text(at); module_t *m = g_tm[mi].m; uint64_t b = g_tm[mi].base;
    for (int k = 0; k < 4096; k++) {
        uint64_t cand[2] = { ((b + m->size + 0x3FFF) & ~0x3FFFull) + (uint64_t)k * 0x4000, (b & ~0x3FFFull) - (uint64_t)(k + 1) * 0x4000 + (m->patch_off < 0 ? (uint64_t)m->patch_off : 0) };
        for (int j = 0; j < 2; j++) {
            mach_vm_address_t a = cand[j];
            int64_t d = (int64_t)(a - at);
            if (d <= -(120ll << 20) || d >= (120ll << 20)) continue;
            if (mach_vm_allocate(mach_task_self(), &a, 0x4000, VM_FLAGS_FIXED) == KERN_SUCCESS) {
                g_spg[g_nspg].a = a; g_spg[g_nspg].used = !reserve; g_nspg++;
                return (uint32_t *)a;
            }
        }
    }
    die("trace: no slot page near %#llx", (unsigned long long)at);
}
static void probe_add(uint64_t at, const char *label) {
    int mi = tm_text(at);
    if (mi < 0) { LOG("trace: probe %s at %#llx is not in a module's text, skipped", label, (unsigned long long)at); return; }
    if (g_ntp == TPMAX) die("trace: too many probes");
    tmod_t *t = &g_tm[mi]; uint64_t w = (at - t->base) >> 2;
    tprobe_t *p = &g_tp[g_ntp];
    p->at = at; p->orig = *(uint32_t *)at; snprintf(p->label, sizeof p->label, "%s", label);
    p->x1 = !strcmp(label, "__cxa_throw");
    uint32_t in = p->orig;
    if ((in & 0x7C000000u) == 0x14000000u) {   // b / bl
        int64_t imm = ((int64_t)(int32_t)(in << 6)) >> 4; p->target = at + imm; p->emu = (in >> 31) ? EMU_BL : EMU_B;
    } else if ((in & 0x1F000000u) == 0x10000000u) {   // adr / adrp
        int64_t imm = (int64_t)(((in >> 5) & 0x7FFFF) << 2 | ((in >> 29) & 3)); imm = (imm << 43) >> 43;
        p->target = (in >> 31) ? (at & ~0xFFFull) + (uint64_t)(imm << 12) : at + imm; p->emu = EMU_ADR; p->rd = in & 31;
    } else if ((in & 0xFF000010u) == 0x54000000u || (in & 0x7E000000u) == 0x34000000u || (in & 0x7E000000u) == 0x36000000u || (in & 0x3B000000u) == 0x18000000u) {
        LOG("trace: probe %s: first instruction %08x is pc-relative, skipped", label, in); return;
    } else {
        p->emu = EMU_SLOT; p->slot = slot_near(at, 0);
        int64_t back = (int64_t)(at + 4) - (int64_t)((uint64_t)p->slot + 4);
        p->slot[0] = in; p->slot[1] = 0x14000000u | (uint32_t)((back >> 2) & 0x3FFFFFF);
    }
    atomic_store(&t->st[w], ST_PROBE);
    LOG("trace: probe %d %s at %s+%#llx (instruction %08x, %s)", g_ntp, label, t->m->name, (unsigned long long)(at - t->base), in,
        p->emu == EMU_SLOT ? "slot" : "emulated");
    g_ntp++;
}

// ---------- coverage ----------
static unsigned g_ind_max = 8;
static void put_back(tmod_t *t, uint64_t w) {
    _Atomic uint32_t *a = (_Atomic uint32_t *)(t->alias + w * 4);
    if (atomic_load_explicit(a, memory_order_relaxed) != t->orig[w]) atomic_store_explicit(a, t->orig[w], memory_order_relaxed);
    sys_icache_invalidate((void *)(t->base + w * 4), 4);
}
static void cov_log(uint64_t pc, int kind, ucontext_t *uc) {
    kv_add(g_kv_covfp, KV_COVFP, (uint64_t)uc->uc_mcontext->__ns.__fpcr | KBIT);
    crec_t *r = tlog_slot(&g_lcov); if (!r) return;
    r->tid = (uint16_t)thr_cur_handle(); r->kind = (uint8_t)kind; r->at = pc;
    atomic_store_explicit(&r->ms1, t_ms() + 1, memory_order_release);
}
static int trace_trap(ucontext_t *uc) {
    _STRUCT_ARM_THREAD_STATE64 *ss = &uc->uc_mcontext->__ss;
    uint64_t pc = ss->__pc; int mi = tm_text(pc);
    if (mi < 0) goto prev;
    tmod_t *t = &g_tm[mi]; uint64_t w = (pc - t->base) >> 2;
    uint8_t s = t->st ? atomic_load(&t->st[w]) : ST_NONE;
    if (s == ST_PROBE) { for (int i = 0; i < g_ntp; i++) if (g_tp[i].at == pc) { probe_fire(i, uc); return 1; } goto prev; }
    if (s == ST_ARMED || s == ST_HIT) {
        uint8_t was = ST_ARMED;
        if (atomic_compare_exchange_strong(&t->st[w], &was, ST_HIT)) cov_log(pc, 1, uc);
        put_back(t, w);
        return 1;
    }
    if (s == ST_IND) {
        unsigned k = atomic_fetch_add(&t->icnt[w], 1);
        if (k == 0) cov_log(pc, 2, uc);
        if (k < g_ind_max) {
            uint32_t in = t->orig[w]; uint64_t tg = reg_of(ss, (in >> 5) & 31);
            irec_t *r = tlog_slot(&g_lind);
            if (r) { r->tid = (uint16_t)thr_cur_handle(); r->at = pc; r->target = tg; atomic_store_explicit(&r->ms1, t_ms() + 1, memory_order_release); }
            if (k + 1 >= g_ind_max) { atomic_store(&t->st[w], ST_INDDONE); put_back(t, w); }
            if ((in & 0xFFFFFC1Fu) == 0xD63F0000u) ss->__lr = pc + 4;   // blr
            ss->__pc = tg;
            return 1;
        }
        atomic_store(&t->st[w], ST_INDDONE);
        put_back(t, w); return 1;
    }
    if (s == ST_INDDONE) { put_back(t, w); return 1; }
prev:
    return g_prev_trap ? g_prev_trap(uc) : 0;
}

static void cov_arm(tmod_t *t, const char *dir) {
    char p[700]; snprintf(p, sizeof p, "%s/fde_%s.txt", dir, t->m->name);
    FILE *f = fopen(p, "r"); if (!f) { LOG("trace: no %s, %s not armed", p, t->m->name); return; }
    uint64_t nw = t->text / 4; uint8_t *in_fde = calloc(nw, 1); char line[128]; unsigned nf = 0;
    while (fgets(line, sizeof line, f)) {
        unsigned long long s, e; if (line[0] == '#' || sscanf(line, "%llx %llx", &s, &e) != 2 || e > t->text || s >= e) continue;
        memset(in_fde + s / 4, 1, (e - s) / 4); nf++;
    }
    fclose(f);
    // second pass marks starts (a start may sit right after another FDE's end)
    f = fopen(p, "r");
    while (fgets(line, sizeof line, f)) { unsigned long long s, e; if (line[0] != '#' && sscanf(line, "%llx %llx", &s, &e) == 2 && e <= t->text && s < e) in_fde[s / 4] = 2; }
    fclose(f);
    for (uint64_t w = 3; w < nw; w++) {   // words 0..2: the module header (a branch, the MOD0 offset, a data word)
        if (atomic_load(&t->st[w]) == ST_PROBE) continue;
        uint32_t in = t->orig[w]; if (!in) continue;   // udf #0: padding
        int ind = (in & 0xFFFFFC1Fu) == 0xD63F0000u || (in & 0xFFFFFC1Fu) == 0xD61F0000u;   // blr / br
        if (ind && g_ind_max) { atomic_store(&t->st[w], ST_IND); t->narm_ind++; }
        else if (in_fde[w] == 2) { atomic_store(&t->st[w], ST_ARMED); t->narm_entry++; }
        else if (!in_fde[w]) { atomic_store(&t->st[w], ST_ARMED); t->narm_out++; }
    }
    free(in_fde);
    for (uint64_t w = 0; w < nw; w++) {
        uint8_t s = atomic_load(&t->st[w]);
        if (s == ST_ARMED) ((uint32_t *)t->alias)[w] = BRK_COV;
        else if (s == ST_IND) ((uint32_t *)t->alias)[w] = BRK_IND;
    }
    LOG("trace: %s: %u FDEs; armed %llu FDE starts, %llu words outside FDEs, %llu br/blr", t->m->name, nf,
        (unsigned long long)t->narm_entry, (unsigned long long)t->narm_out, (unsigned long long)t->narm_ind);
}

// ---------- gates ----------
void trace_gate(uint64_t ret, int kind, int arg, uint64_t orig, uint64_t *regs) {
    uint32_t fpcr = (uint32_t)regs[98];   // the gate frame: nzcv / fpsr / fpcr at +768 / +776 / +784
    kv_add(g_kv_gatefp, KV_GATEFP, ((uint64_t)thr_cur_handle() << 32 | fpcr) | KBIT);
    int mi = tm_any(ret);
    kv_add(g_kv_gates, KV_GATES, KBIT | (uint64_t)(mi & 7) << 60 | (uint64_t)(kind & 15) << 56 | (uint64_t)(arg & 0xff) << 48 | (orig & 0xFFFFFFFFFFFFull));
    uint64_t s = (now_ns() - g_t0_ns) / 1000000000ull;
    if (s < 4096) atomic_fetch_add_explicit(&g_secs[s][kind == 1 || kind == 2 ? 0 : kind == 0 ? 1 : 2], 1, memory_order_relaxed);
}

// ---------- sampler ----------
typedef struct { uint64_t tid; char name[64]; uint64_t n, nguest; struct { uint32_t fpcr; uint64_t n; } fp[6]; } tthr_t;
#define TTMAX 2048
static tthr_t g_tt[TTMAX]; static _Atomic int g_ntt;
static _Atomic uint64_t g_ssec[4096][3];   // per second: samples in guest text, elsewhere in a guest module (patch areas), outside the modules
static int tt_find(uint64_t tid) {
    int n = atomic_load(&g_ntt);
    for (int i = 0; i < n; i++) if (g_tt[i].tid == tid) return i;
    if (n == TTMAX) return -1;
    g_tt[n].tid = tid; atomic_store(&g_ntt, n + 1); return n;
}
static void *sampler_main(void *a) {
    unsigned hz = (unsigned)(uintptr_t)a; useconds_t us = hz ? 1000000 / hz : 1000;
    pthread_setname_np("dm trace sampler");
    thread_act_t self = mach_thread_self();
    thread_act_array_t ths = NULL; mach_msg_type_number_t nth = 0; int idx[1024]; uint64_t last = 0;
    for (;;) {
        uint64_t now = now_ns();
        if (!ths || now - last > 100000000ull) {
            if (ths) { for (mach_msg_type_number_t i = 0; i < nth; i++) mach_port_deallocate(mach_task_self(), ths[i]); vm_deallocate(mach_task_self(), (vm_address_t)ths, nth * sizeof *ths); }
            if (task_threads(mach_task_self(), &ths, &nth) != KERN_SUCCESS) { ths = NULL; nth = 0; }
            if (nth > 1024) nth = 1024;   // (ports beyond are leaked; a host has far fewer threads)
            for (mach_msg_type_number_t i = 0; i < nth; i++) {
                thread_identifier_info_data_t ii; mach_msg_type_number_t c = THREAD_IDENTIFIER_INFO_COUNT; idx[i] = -1;
                if (ths[i] == self || thread_info(ths[i], THREAD_IDENTIFIER_INFO, (thread_info_t)&ii, &c) != KERN_SUCCESS) continue;
                idx[i] = tt_find(ii.thread_id);
                thread_extended_info_data_t ei; c = THREAD_EXTENDED_INFO_COUNT;
                if (idx[i] >= 0 && thread_info(ths[i], THREAD_EXTENDED_INFO, (thread_info_t)&ei, &c) == KERN_SUCCESS && ei.pth_name[0]) strlcpy(g_tt[idx[i]].name, ei.pth_name, sizeof g_tt[0].name);
            }
            last = now;
        }
        uint64_t sec = (now - g_t0_ns) / 1000000000ull; if (sec > 4095) sec = 4095;
        for (mach_msg_type_number_t i = 0; i < nth; i++) {
            if (idx[i] < 0 || thread_suspend(ths[i]) != KERN_SUCCESS) continue;
            arm_thread_state64_t st; arm_neon_state64_t ns; mach_msg_type_number_t c = ARM_THREAD_STATE64_COUNT; int neon = 0;
            uint64_t pc = thread_get_state(ths[i], ARM_THREAD_STATE64, (thread_state_t)&st, &c) == KERN_SUCCESS ? __darwin_arm_thread_state64_get_pc(st) : 0;
            int mi = pc ? tm_any(pc) : -1;
            if (mi >= 0) { c = ARM_NEON_STATE64_COUNT; neon = thread_get_state(ths[i], ARM_NEON_STATE64, (thread_state_t)&ns, &c) == KERN_SUCCESS; }
            thread_resume(ths[i]);
            if (!pc) continue;
            tthr_t *tt = &g_tt[idx[i]]; tt->n++;
            if (mi < 0) { atomic_fetch_add_explicit(&g_ssec[sec][2], 1, memory_order_relaxed); continue; }
            tt->nguest++;
            tmod_t *t = &g_tm[mi];
            if (pc >= t->base && pc < t->base + t->text) { if (t->samp) t->samp[(pc - t->base) >> 2]++; atomic_fetch_add_explicit(&g_ssec[sec][0], 1, memory_order_relaxed); }
            else { atomic_fetch_add_explicit(&g_ssec[sec][1], 1, memory_order_relaxed); kv_add(g_kv_patch, KV_PATCH, KBIT | (uint64_t)mi << 56 | ((pc - t->base) & 0xFFFFFFFFFFFFull)); }
            if (neon) {
                uint32_t f = ns.__fpcr; int k = 0;
                while (k < 6 && tt->fp[k].n && tt->fp[k].fpcr != f) k++;
                if (k < 6) { tt->fp[k].fpcr = f; tt->fp[k].n++; }
            }
        }
        usleep(us);
    }
    return NULL;
}

// ---------- writer ----------
static FILE *out_open(const char *name, char *tmp, size_t n) { snprintf(tmp, n, "%s/%s.tmp", g_dir, name); return fopen(tmp, "w"); }
static void out_close(FILE *f, const char *name, const char *tmp) { char p[700]; fclose(f); snprintf(p, sizeof p, "%s/%s", g_dir, name); rename(tmp, p); }
static void log_append(tlog_t *l) {
    uint64_t n = atomic_load(&l->n); if (n > l->cap) n = l->cap;
    uint64_t end = l->written;
    while (end < n && atomic_load_explicit((_Atomic uint32_t *)((char *)l->buf + end * l->rec), memory_order_acquire)) end++;
    if (end == l->written) return;
    char p[700]; snprintf(p, sizeof p, "%s/%s", g_dir, l->file);
    FILE *f = fopen(p, "ab"); if (!f) return;
    fwrite((char *)l->buf + l->written * l->rec, l->rec, end - l->written, f); fclose(f);
    l->written = end;
}
static const char *kind_name(int k) { static const char *n[] = {"svc", "cntpct", "cntvct", "obs", "spin"}; return k >= 0 && k < 5 ? n[k] : "?"; }
static void write_tables(int final) {
    char tmp[700]; FILE *f;
    if ((f = out_open("gate_fpcr.tsv", tmp, sizeof tmp))) {
        fprintf(f, "thread\tfpcr\tgates\n");
        for (int i = 0; i < KV_GATEFP; i++) { uint64_t k = g_kv_gatefp[i].k; if (k) fprintf(f, "%#llx\t%#llx\t%llu\n", (unsigned long long)((k & ~KBIT) >> 32), (unsigned long long)(uint32_t)k, (unsigned long long)g_kv_gatefp[i].n); }
        out_close(f, "gate_fpcr.tsv", tmp);
    }
    if ((f = out_open("gates.tsv", tmp, sizeof tmp))) {
        fprintf(f, "module\toffset\tkind\targ\tcalls\n");
        for (int i = 0; i < KV_GATES; i++) {
            uint64_t k = g_kv_gates[i].k; if (!k) continue;
            int mi = (int)((k >> 60) & 7); int kind = (int)((k >> 56) & 15);
            fprintf(f, "%s\t%#llx\t%s\t%d\t%llu\n", mi < g_ntm ? g_tm[mi].m->name : "?", (unsigned long long)(k & 0xFFFFFFFFFFFFull), kind_name(kind), (int)((k >> 48) & 0xff), (unsigned long long)g_kv_gates[i].n);
        }
        out_close(f, "gates.tsv", tmp);
    }
    if ((f = out_open("cov_fpcr.tsv", tmp, sizeof tmp))) {
        fprintf(f, "fpcr\tfirst_executions\n");
        for (int i = 0; i < KV_COVFP; i++) { uint64_t k = g_kv_covfp[i].k; if (k) fprintf(f, "%#llx\t%llu\n", (unsigned long long)(k & ~KBIT), (unsigned long long)g_kv_covfp[i].n); }
        out_close(f, "cov_fpcr.tsv", tmp);
    }
    if ((f = out_open("secs.tsv", tmp, sizeof tmp))) {
        fprintf(f, "second\tcounter_reads\tsvcs\tother_gates\tsamples_text\tsamples_module_other\tsamples_host\n");
        for (int s = 0; s < 4096; s++) {
            uint64_t a = g_secs[s][0], b = g_secs[s][1], c = g_secs[s][2], d = g_ssec[s][0], e = g_ssec[s][1], g = g_ssec[s][2];
            if (a | b | c | d | e | g) fprintf(f, "%d\t%llu\t%llu\t%llu\t%llu\t%llu\t%llu\n", s, (unsigned long long)a, (unsigned long long)b, (unsigned long long)c, (unsigned long long)d, (unsigned long long)e, (unsigned long long)g);
        }
        out_close(f, "secs.tsv", tmp);
    }
    if ((f = out_open("probes.tsv", tmp, sizeof tmp))) {
        fprintf(f, "index\tlabel\tmodule\toffset\tinstruction\thits\n");
        for (int i = 0; i < g_ntp; i++) { int mi = tm_text(g_tp[i].at);
            fprintf(f, "%d\t%s\t%s\t%#llx\t%08x\t%llu\n", i, g_tp[i].label, g_tm[mi].m->name, (unsigned long long)(g_tp[i].at - g_tm[mi].base), g_tp[i].orig, (unsigned long long)g_tp[i].hits); }
        out_close(f, "probes.tsv", tmp);
    }
    if ((f = out_open("threads.tsv", tmp, sizeof tmp))) {
        fprintf(f, "thread_id\tname\tsamples\tsamples_in_guest\tfpcr:samples...\n");
        int n = atomic_load(&g_ntt);
        for (int i = 0; i < n; i++) {
            fprintf(f, "%llu\t%s\t%llu\t%llu", (unsigned long long)g_tt[i].tid, g_tt[i].name, (unsigned long long)g_tt[i].n, (unsigned long long)g_tt[i].nguest);
            for (int k = 0; k < 6 && g_tt[i].fp[k].n; k++) fprintf(f, "\t%#x:%llu", g_tt[i].fp[k].fpcr, (unsigned long long)g_tt[i].fp[k].n);
            fputc('\n', f);
        }
        out_close(f, "threads.tsv", tmp);
    }
    // the exception types thrown: __cxa_throw's x1 is the type_info, whose +8 points at the mangled name
    for (int pi = 0; pi < g_ntp; pi++) if (g_tp[pi].x1 && (f = out_open("throw_types.tsv", tmp, sizeof tmp))) {
        uint64_t seen[256]; int ns = 0; uint64_t n = atomic_load(&g_lprb.n); if (n > g_lprb.cap) n = g_lprb.cap;
        fprintf(f, "type_info\tname\n");
        for (uint64_t i = 0; i < n && ns < 256; i++) {
            prec_t *r = (prec_t *)g_lprb.buf + i; if (!atomic_load(&r->ms1) || r->probe != pi) continue;
            int k = 0; while (k < ns && seen[k] != r->extra) k++;
            if (k < ns) continue;
            seen[ns++] = r->extra;
            uint64_t np = 0; char nm[200] = ""; vm_size_t got = 0;
            if (!vm_read_overwrite(mach_task_self(), r->extra + 8, 8, (vm_address_t)&np, &got) && np && !vm_read_overwrite(mach_task_self(), np, sizeof nm - 1, (vm_address_t)nm, &got)) nm[sizeof nm - 1] = 0;
            for (char *c = nm; *c; c++) if (*c < 0x20 || *c > 0x7e) { *c = 0; break; }
            fprintf(f, "%#llx\t%s\n", (unsigned long long)r->extra, nm);
        }
        out_close(f, "throw_types.tsv", tmp);
    }
    (void)final;
    if ((f = out_open("samples_patch.tsv", tmp, sizeof tmp))) {   // pcs outside the text, each with the original site whose patch block holds it
        fprintf(f, "module\toffset\tsamples\tsite\tblock\n");
        for (int i = 0; i < KV_PATCH; i++) {
            uint64_t k = g_kv_patch[i].k; if (!k) continue;
            int mi = (int)((k >> 56) & 7); int64_t off = (int64_t)((k & 0xFFFFFFFFFFFFull) << 16) >> 16;
            long long site = -1, blk = 0; size_t lo = 0, hi = g_nrwb[mi];
            while (lo < hi) { size_t md = (lo + hi) / 2; if (g_rwb[mi][md].tgt <= off) lo = md + 1; else hi = md; }
            if (lo && off - g_rwb[mi][lo - 1].tgt < 0x1000) { site = g_rwb[mi][lo - 1].site; blk = g_rwb[mi][lo - 1].tgt; }
            fprintf(f, "%s\t%lld\t%llu\t%lld\t%lld\n", g_tm[mi].m->name, (long long)off, (unsigned long long)g_kv_patch[i].n, site, blk);
        }
        out_close(f, "samples_patch.tsv", tmp);
    }
    for (int mi = 0; mi < g_ntm; mi++) {   // sampled pcs per text word (nonzero only)
        tmod_t *t = &g_tm[mi]; if (!t->samp) continue;
        char name[64]; snprintf(name, sizeof name, "samples_%s.tsv", t->m->name);
        if (!(f = out_open(name, tmp, sizeof tmp))) continue;
        for (uint64_t w = 0; w < t->text / 4; w++) if (t->samp[w]) fprintf(f, "%llx\t%u\n", (unsigned long long)(w * 4), t->samp[w]);
        out_close(f, name, tmp);
    }
}
static void *writer_main(void *a) {
    (void)a; pthread_setname_np("dm trace writer");
    char fl[700], fd[700]; snprintf(fl, sizeof fl, "%s/flush", g_dir); snprintf(fd, sizeof fd, "%s/flushed", g_dir);
    for (unsigned tick = 1;; tick++) {
        usleep(200000);
        struct stat sb; int flush = !stat(fl, &sb);
        if (flush || tick % 25 == 0) { log_append(&g_lprb); log_append(&g_lcov); log_append(&g_lind); }
        if (flush || tick % 150 == 0) write_tables(flush);
        if (flush) {
            unlink(fl);
            FILE *f = fopen(fd, "w");
            if (f) { fprintf(f, "%u ms; logs probe %llu cov %llu ind %llu (caps %zu %zu %zu)\n", t_ms(), (unsigned long long)atomic_load(&g_lprb.n), (unsigned long long)atomic_load(&g_lcov.n),
                             (unsigned long long)atomic_load(&g_lind.n), g_lprb.cap, g_lcov.cap, g_lind.cap); fclose(f); }
            LOG("trace: flushed");
        }
    }
    return NULL;
}

// ---------- install ----------
static uint8_t *alias_of(uint64_t base, uint64_t size) {   // as hle_cheats.c alias_setup: same physical pages, writable
    mach_vm_address_t dst = 0; vm_prot_t cur = 0, mx = 0;
    if (mach_vm_remap(mach_task_self(), &dst, size, 0, VM_FLAGS_ANYWHERE, mach_task_self(), base, FALSE, &cur, &mx, VM_INHERIT_NONE)) return NULL;
    if (mach_vm_protect(mach_task_self(), dst, size, 0, VM_PROT_READ | VM_PROT_WRITE)) { mach_vm_deallocate(mach_task_self(), dst, size); return NULL; }
    return (uint8_t *)dst;
}
void trace_install(void) {
    const char *dir = getenv("DM_TRACE"); if (!dir || !*dir) return;
    strlcpy(g_dir, dir, sizeof g_dir); mkdir(g_dir, 0755);
    const char *cov = getenv("DM_TRACE_COV"), *prb = getenv("DM_TRACE_PROBES"), *hz = getenv("DM_TRACE_HZ"), *ind = getenv("DM_TRACE_IND");
    if (ind) g_ind_max = (unsigned)atoi(ind);
    if (!cov) g_ind_max = 0;
    char tmp[700]; FILE *f = out_open("modules.tsv", tmp, sizeof tmp);
    if (f) fprintf(f, "name\tbase\tsize\ttext_end\tpatch_off\tbuild_id\n");
    for (int i = 0; i < g_nmod && g_ntm < TMAX; i++) {
        module_t *m = &g_mod[i]; if (m->nro) continue;
        tmod_t *t = &g_tm[g_ntm++];
        t->m = m; t->base = (uint64_t)m->base; t->text = m->text_end; t->rx = (m->text_end + 0x3FFF) & ~0x3FFFull;
        if (f) fprintf(f, "%s\t%#llx\t%#zx\t%#llx\t%lld\t%s\n", m->name, (unsigned long long)t->base, m->size, (unsigned long long)t->text, (long long)m->patch_off, m->build_id);
    }
    if (f) out_close(f, "modules.tsv", tmp);
    if (prb) for (int i = 0; i < g_ntm; i++) slot_near(g_tm[i].base + g_tm[i].text / 2, 1);   // before the large allocations below
    tlog_init(&g_lprb); tlog_init(&g_lcov); tlog_init(&g_lind);
    for (int i = 0; i < g_ntm; i++) { g_tm[i].st = calloc(g_tm[i].text / 4, 1); if (hz) g_tm[i].samp = calloc(g_tm[i].text / 4, sizeof *g_tm[i].samp); }
    // What nn::os reports: these are leaf functions without state, so the host can call them before the game runs (and before anything is armed)
    if ((f = out_open("info.tsv", tmp, sizeof tmp))) {
        uint64_t (*gf)(void) = guest_sym("_ZN2nn2os22GetSystemTickFrequencyEv"), (*gfc)(void) = guest_sym("nnosGetSystemTickFrequency");
        int64_t (*cv)(int64_t) = guest_sym("_ZN2nn2os17ConvertToTimeSpanENS0_4TickE");
        uint64_t guest_ticks(void); uint64_t t0 = guest_ticks(), n0 = now_ns(); usleep(200000); uint64_t t1 = guest_ticks(), n1 = now_ns();
        fprintf(f, "nn::os::GetSystemTickFrequency()\t%llu\n", gf ? (unsigned long long)gf() : 0ull);
        fprintf(f, "nnosGetSystemTickFrequency()\t%llu\n", gfc ? (unsigned long long)gfc() : 0ull);
        fprintf(f, "nn::os::ConvertToTimeSpan(31250000 ticks) ns\t%lld\n", cv ? (long long)cv(31250000) : 0ll);
        fprintf(f, "nn::os::ConvertToTimeSpan(19200000 ticks) ns\t%lld\n", cv ? (long long)cv(19200000) : 0ll);
        fprintf(f, "host counter gate rate (ticks per second, measured over 0.2 s)\t%.0f\n", (double)(t1 - t0) * 1e9 / (double)(n1 - n0));
        out_close(f, "info.tsv", tmp);
    }
    for (int i = 0; i < g_ntm; i++) {   // the original words, before any probe or brk
        tmod_t *t = &g_tm[i]; t->orig = malloc(t->text); memcpy(t->orig, (void *)t->base, t->text);
        size_t cap = 4096; g_rwb[i] = malloc(cap * sizeof *g_rwb[i]);
        for (uint64_t w = 0; w < t->text / 4; w++) {   // b / bl leaving the text: the rewriter's branches to its blocks
            uint32_t in = t->orig[w]; if ((in & 0x7C000000u) != 0x14000000u) continue;
            int64_t tg = (int64_t)(w * 4) + (((int64_t)(int32_t)(in << 6)) >> 4);
            if (tg >= 0 && tg < (int64_t)t->text) continue;
            if (g_nrwb[i] == cap) g_rwb[i] = realloc(g_rwb[i], (cap *= 2) * sizeof *g_rwb[i]);
            g_rwb[i][g_nrwb[i]++] = (rwb_t){ tg, (uint32_t)(w * 4) };
        }
        qsort(g_rwb[i], g_nrwb[i], sizeof *g_rwb[i], rwb_cmp);
        if (cov || prb) { t->alias = alias_of(t->base, t->rx); if (!t->alias) die("trace: no writable alias of %s", t->m->name); }
        if (cov) t->icnt = calloc(t->text / 4, 1);
    }
    if (prb) {
        char *s = strdup(prb);
        for (char *e = strtok(s, ","); e; e = strtok(NULL, ",")) {
            uint64_t at = 0; char label[64];
            if (*e == '=') { at = (uint64_t)guest_sym(e + 1); snprintf(label, sizeof label, "%s", e + 1); if (!at) { LOG("trace: no symbol %s", e + 1); continue; } }
            else { char *c = strchr(e, ':'); if (!c) continue; *c = 0;
                   for (int i = 0; i < g_ntm; i++) if (!strcmp(g_tm[i].m->name, e)) at = g_tm[i].base + strtoull(c + 1, 0, 16);
                   snprintf(label, sizeof label, "%s+%s", e, c + 1); }
            if (at) probe_add(at, label);
        }
        free(s);
        for (int i = 0; i < g_ntp; i++) { tmod_t *t = &g_tm[tm_text(g_tp[i].at)]; ((uint32_t *)t->alias)[(g_tp[i].at - t->base) >> 2] = BRK_PRB; }
        // the slot pages: executable from now on
        for (int i = 0; i < g_ntp; i++) if (g_tp[i].slot) { uint64_t pg = (uint64_t)g_tp[i].slot & ~0x3FFFull; mprotect((void *)pg, 0x4000, PROT_READ | PROT_EXEC); sys_icache_invalidate((void *)pg, 0x4000); }
    }
    if (cov) for (int i = 0; i < g_ntm; i++) cov_arm(&g_tm[i], cov);
    for (int i = 0; i < g_ntm; i++) {
        tmod_t *t = &g_tm[i]; sys_icache_invalidate((void *)t->base, t->text);
        if ((cov || prb) && t->alias) {   // the alias really is the code: a word written there is seen at the original address
            uint64_t w = 3; while (w < t->text / 4 && atomic_load(&t->st[w]) == ST_NONE) w++;
            if (w < t->text / 4 && ((uint32_t *)t->base)[w] == t->orig[w]) die("trace: the alias of %s does not reach its code", t->m->name);
        }
    }
    g_prev_trap = crash_get_trap_hook(); crash_set_trap_hook(trace_trap);
    g_trace_on = 1;
    pthread_t th;
    if (hz) { if (pthread_create(&th, NULL, sampler_main, (void *)(uintptr_t)atoi(hz))) die("trace sampler"); pthread_detach(th); }
    if (pthread_create(&th, NULL, writer_main, NULL)) die("trace writer"); pthread_detach(th);
    LOG("trace: on, into %s (probes %d, coverage %s, br/blr targets %u per site, sampler %s Hz)", g_dir, g_ntp, cov ? cov : "off", g_ind_max, hz ? hz : "off");
}
