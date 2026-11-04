// The swap-in runtime (docs/m4-swapin.md): what lifted code needs from a runtime (src/sem/include/nxw/rt.h), served by
// nxnative's own handlers, plus the patching of the chosen functions' entries. Lives in the swap-in dylib with the lifted code.
//
//   native -> lifted: the function's entry word becomes a B to its thunk (swapin_entry.S), which saves the native registers into
//                     the thread's NxwGuestCpu and calls fn_<addr> on a host stack of the thread's own
//   lifted -> lifted: through the D4 table (rt_code_table), which holds exactly the swapped functions
//   lifted -> native: a table miss (rt_miss) runs the target natively (nxw_swapin_call_native), on the guest's stack
#include "swapin.h"
#include "nxw/rt.h"

#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

// The thread's state: NxwGuestCpu first (the 800-byte frame nxnative's handlers take), then the host stack's current top
typedef struct SwapThread {
    NxwGuestCpu cpu;
    uint64_t host_sp;       // +896 (swapin_entry.S HOST_SP): where the next entry's host frames start
    uint64_t stack_lo;
    uint32_t handle;
} SwapThread;
_Static_assert(offsetof(SwapThread, host_sp) == 896, "swapin_entry.S HOST_SP");
_Static_assert(offsetof(NxwGuestCpu, pc) == 800 && offsetof(NxwGuestCpu, tpidr_el0) == 808 && offsetof(NxwGuestCpu, excl_addr) == 824,
               "swapin_entry.S offsets");

#define HOST_STACK (16u << 20)
#define GUARD 0x4000u

// {our key, nxnative's K18, KTLS, KTLR} as TSD byte offsets (read by swapin_entry.S)
uint64_t nxw_swapin_tsd[4];
static pthread_key_t g_key;

// The D4 table for lifted code: the swapped functions only
const uint32_t *rt_code_table;
uint64_t rt_code_base, rt_code_words, rt_host_base;
volatile uint8_t rt_trace_on;

// From nxlift --swapin (nxw_swapin_table.o) and swapin_entry.S
extern const NxwSwapinFn nxw_swapin_table[];
extern const uint32_t nxw_swapin_count;
extern const NxwSwapinLifted nxw_swapin_modules[];
extern const uint32_t nxw_swapin_module_count;
extern const char nxw_swapin_build_id[];
extern const uint32_t nxw_swapin_thunk_template[];
extern void nxw_swapin_entry(void);
extern void nxw_swapin_native_fn(NxwGuestCpu *cpu);
extern char __dso_handle;

static const NxwSwapinHost *H;
static _Atomic uint8_t *g_seen;   // first entry of a lifted function (rt_trace_entry), one byte per code word
static _Atomic uint32_t g_threads;

static void slog(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    H->log(fmt, ap);
    va_end(ap);
}

// The runtime's own large allocations (the tables, the host stacks) go above 2^39, never where the guest's address space would go
// next: nxnative places its 11 GB guest block at the first fit from 0x7000000000, so anything of ours there would move every guest
// heap address against the native run, and with it pointer-keyed hash orders (found by the first-entry order trace)
static void *alloc_high(size_t bytes) {
    static _Atomic uint64_t next = 0x8000000000ull;
    bytes = (bytes + 0x3FFF) & ~0x3FFFull;
    for (int k = 0; k < 4096; k++) {
        mach_vm_address_t a = atomic_fetch_add(&next, bytes + 0x4000);   // a guard page between
        if (mach_vm_allocate(mach_task_self(), &a, bytes, VM_FLAGS_FIXED) == KERN_SUCCESS) return (void *)a;
    }
    return NULL;
}

// ---------------------------------------------------------------- threads
SwapThread *nxw_swapin_thread_new(void) {
    // Never freed: a thread exits from inside an SVC (pthread_exit), possibly on this very stack. Not from malloc either: the
    // host heap also holds what the guest sees (nxnative's TLS blocks and thread objects), which must not move against the
    // native run
    uint8_t *m = alloc_high(HOST_STACK + GUARD + 0x4000);
    if (!m || mprotect(m, GUARD, PROT_NONE)) abort();
    SwapThread *t = (SwapThread *)(m + GUARD + HOST_STACK);   // the state in the page above the stack
    t->stack_lo = (uint64_t)(m + GUARD);
    t->host_sp = ((uint64_t)(m + GUARD + HOST_STACK)) & ~15ull;
    t->cpu.excl_addr = ~0ull;
    t->handle = H->thread_handle ? H->thread_handle() : 0;
    pthread_setspecific(g_key, t);
    atomic_fetch_add(&g_threads, 1);
    return t;
}

static inline uint8_t *tsd_base(void) {
    uint64_t b;
    __asm__ volatile("mrs %0, tpidrro_el0" : "=r"(b));
    return (uint8_t *)(b & ~7ull);
}
// The guest's x18 and tpidr_el0 into nxnative's slots (native code and the handlers read them there), and back
static inline void slots_out(const NxwGuestCpu *c) {
    uint8_t *b = tsd_base();
    *(uint64_t *)(b + nxw_swapin_tsd[1]) = c->x[18];
    *(uint64_t *)(b + nxw_swapin_tsd[2]) = c->tpidr_el0;
}
static inline void slots_in(NxwGuestCpu *c) {
    uint8_t *b = tsd_base();
    c->x[18] = *(uint64_t *)(b + nxw_swapin_tsd[1]);
    c->tpidr_el0 = *(uint64_t *)(b + nxw_swapin_tsd[2]);
    c->tpidrro_el0 = *(uint64_t *)(b + nxw_swapin_tsd[3]);
}

// ---------------------------------------------------------------- where an address is
static const NxwSwapinMapped *module_at(uint64_t a) {
    for (int i = 0; i < H->nmod; i++)
        if (a >= H->mod[i].base && a < H->mod[i].base + H->mod[i].size) return &H->mod[i];
    return NULL;
}
static uint32_t *g_by_addr;   // indices into nxw_swapin_table, by guest address
static int by_addr_cmp(const void *a, const void *b) {
    uint64_t x = nxw_swapin_table[*(const uint32_t *)a].guest, y = nxw_swapin_table[*(const uint32_t *)b].guest;
    return x < y ? -1 : x > y;
}
// The lifted function whose code holds a, or -1
static int64_t lifted_at(uint64_t a) {
    uint32_t lo = 0, hi = nxw_swapin_count;
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (nxw_swapin_table[g_by_addr[mid]].guest <= a) lo = mid + 1;
        else hi = mid;
    }
    if (!lo) return -1;
    const NxwSwapinFn *f = &nxw_swapin_table[g_by_addr[lo - 1]];
    return a < f->guest + f->size ? (int64_t)g_by_addr[lo - 1] : -1;
}
static void where(uint64_t a, char *out, size_t n) {
    const NxwSwapinMapped *m = module_at(a);
    if (!m) { snprintf(out, n, "%#llx", (unsigned long long)a); return; }
    int64_t f = lifted_at(a);
    if (f >= 0) snprintf(out, n, "%s+%#llx (fn %s+%#llx)", m->name, (unsigned long long)(a - m->base), m->name,
                         (unsigned long long)(nxw_swapin_table[f].guest - m->base));
    else snprintf(out, n, "%s+%#llx", m->name, (unsigned long long)(a - m->base));
}

// ---------------------------------------------------------------- the runtime contract (nxw/rt.h)
void rt_svc(NxwGuestCpu *cpu, uint32_t imm) {
    const NxwSwapinMapped *m = module_at(cpu->pc);
    const uint64_t orig = m ? cpu->pc - m->base : cpu->pc;
    slots_out(cpu);
    H->thr_svc_enter((uint64_t *)cpu, orig);   // the frame is the context of a thread paused in an SVC (hle_thread.c)
    H->svc_dispatch((int)imm, (uint64_t *)cpu, orig);
    H->thr_svc_leave();
    slots_in(cpu);
}

uint64_t rt_mrs(NxwGuestCpu *cpu, uint32_t sysreg) {
    if (sysreg == 0x5f01 || sysreg == 0x5f02) {   // cntpct_el0, cntvct_el0: nxnative's counter gate (hle.c gate_dispatch)
        H->guest_clock_read_wait();
        return H->guest_ticks();
    }
    rt_trap(cpu, cpu->pc, NXW_TRAP_SYSTEM | (sysreg << 8));
}

void rt_msr(NxwGuestCpu *cpu, uint32_t sysreg, uint64_t value) {
    if (sysreg == 0x5a20) {   // fpcr: the hardware register is the guest's (lifted code runs on the guest's thread)
        cpu->fpcr = value;
        __asm__ volatile("msr fpcr, %0" ::"r"(value));
        return;
    }
    rt_trap(cpu, cpu->pc, NXW_TRAP_SYSTEM | (sysreg << 8));
}

void rt_yield(NxwGuestCpu *cpu, uint32_t kind, uint64_t site) {
    if (kind != NXW_YIELD_SPIN) {   // YIELD and WFE run natively as hints
        __asm__ volatile("yield");
        return;
    }
    // a spin-loop head: nxnative's spin gate (hle.c G_SPIN)
    const NxwSwapinMapped *m = module_at(site);
    const uint64_t orig = m ? site - m->base : site;
    slots_out(cpu);
    H->thr_svc_enter((uint64_t *)cpu, orig);
    H->spin_wait(site, orig);
    H->thr_svc_leave();
}

// DM_SWAPIN_STATS=<file>: native calls from lifted code by target, written every 10 s (a lock-free table: target, calls)
#define NSTAT 16384
static struct { _Atomic uint64_t at, n; } g_stat[NSTAT];
static int g_stats_on;
static void stat_note(uint64_t at) {
    for (unsigned i = (unsigned)((at * 0x9E3779B97F4A7C15ull) >> 50) & (NSTAT - 1), k = 0; k < NSTAT; k++, i = (i + 1) & (NSTAT - 1)) {
        uint64_t cur = atomic_load_explicit(&g_stat[i].at, memory_order_relaxed);
        if (!cur && !atomic_compare_exchange_strong(&g_stat[i].at, &cur, at) && cur != at) continue;
        if (cur && cur != at) continue;
        atomic_fetch_add_explicit(&g_stat[i].n, 1, memory_order_relaxed);
        return;
    }
}
static void *stats_main(void *path) {
    for (;;) {
        sleep(10);
        char tmp[1100];
        snprintf(tmp, sizeof tmp, "%s.tmp", (const char *)path);
        FILE *f = fopen(tmp, "w");
        if (!f) continue;
        fprintf(f, "target\tcalls\n");
        for (unsigned i = 0; i < NSTAT; i++)
            if (atomic_load(&g_stat[i].at)) {
                char w[128];
                where(atomic_load(&g_stat[i].at), w, sizeof w);
                fprintf(f, "%s\t%llu\n", w, (unsigned long long)atomic_load(&g_stat[i].n));
            }
        fclose(f);
        rename(tmp, (const char *)path);
    }
    return NULL;
}

// A table miss runs the target natively, except a PLT stub whose GOT slot holds a swapped function: that one runs lifted, with
// x16 and x17 as the stub leaves them (adrp x16; ldr x17, [x16, #o]; add x16, x16, #o; br x17). Without the shortcut every
// cross-module call between swapped functions went native and back (two transitions), and the boot ran seconds late
NxwGuestFn rt_miss(NxwGuestCpu *cpu, uint64_t target) {
    const uint32_t *w = (const uint32_t *)target;
    if (module_at(target) && (w[0] & 0x9f00001fu) == 0x90000010u && (w[1] & 0xffc003ffu) == 0xf9400211u &&
        (w[2] & 0xffc003ffu) == 0x91000210u && w[3] == 0xd61f0220u) {
        const int64_t page_off = (int64_t)((uint64_t)((w[0] >> 5 & 0x7ffff) << 2 | (w[0] >> 29 & 3)) << 43) >> 31;
        const uint64_t page = (target & ~0xfffull) + (uint64_t)page_off;
        const uint64_t t = *(volatile const uint64_t *)(page + (w[1] >> 10 & 0xfff) * 8);
        const uint64_t i = (t - rt_code_base) >> 2;
        if (i < rt_code_words && rt_code_table[i]) {
            cpu->x[16] = page + (w[2] >> 10 & 0xfff);
            cpu->x[17] = t;
            return (NxwGuestFn)(rt_host_base + rt_code_table[i]);
        }
    }
    if (g_stats_on) stat_note(target);
    cpu->pc = target;
    // Host code (HLE, NVN) gets the full transition too: it may call back into swapped code, whose entry fills this thread's
    // NxwGuestCpu, so only a complete save of the registers on the way back leaves cpu right (a lighter path that moved only
    // the argument registers lost the caller's x9-x30 that way and crashed in the NVN layer)
    return nxw_swapin_native_fn;
}

static void guest_backtrace(const NxwGuestCpu *cpu) {
    char w[128];
    uint64_t fp = cpu->x[29];
    where(cpu->x[30], w, sizeof w);
    slog("  lr %s", w);
    for (int i = 0; i < 24 && fp && !(fp & 15); i++) {
        if (!module_at(((uint64_t *)fp)[1]) && i) break;
        where(((uint64_t *)fp)[1], w, sizeof w);
        slog("  #%d %s", i, w);
        fp = ((uint64_t *)fp)[0];
    }
}

void rt_trap(NxwGuestCpu *cpu, uint64_t pc, uint32_t why) {
    char w[128];
    where(pc, w, sizeof w);
    slog("[RED] TRAP in lifted code at %s: why %u detail %#x (thread %u)", w, why & 0xff, why >> 8, H->thread_handle ? H->thread_handle() : 0);
    for (int i = 0; i < 31; i += 4)
        slog("  x%-2d %016llx %016llx %016llx %016llx", i, (unsigned long long)cpu->x[i], (unsigned long long)cpu->x[i + 1],
             (unsigned long long)cpu->x[i + 2], i + 3 < 31 ? (unsigned long long)cpu->x[i + 3] : (unsigned long long)cpu->sp);
    guest_backtrace(cpu);
    abort();
}

void rt_nonlocal(NxwGuestCpu *cpu) {   // never swapped in (init excludes the four context functions); a call means a bug here
    rt_trap(cpu, cpu->pc, NXW_TRAP_UNREACHABLE);
}

void rt_trace_entry(NxwGuestCpu *cpu, uint64_t entry) {
    (void)cpu;
    const uint64_t i = (entry - rt_code_base) >> 2;
    if (i >= rt_code_words || atomic_load_explicit(&g_seen[i], memory_order_relaxed)) return;
    if (!atomic_exchange_explicit(&g_seen[i], 1, memory_order_relaxed) && H->cov_note) H->cov_note(entry, 3);
}

// ---------------------------------------------------------------- patching
enum { X_FLAGS, X_OBS, X_USER, X_NOTHUNK, X_N };
static const char *const k_xname[X_N] = {"context functions (SP/x30 rewriters)", "nxnative observation gates inside",
                                         "excluded by the list", "no thunk page within reach"};
// program.db's flags of the four sdk functions that rewrite SP or x30 (nxanalyze/db.hpp): never swapped (rt_nonlocal)
#define FN_CONTEXT (0x40u | 0x100u | 0x200u | 0x80000u)

static uint8_t *alias_of(uint64_t base, uint64_t size) {   // a writable second mapping of the code (as hle_trace.c)
    mach_vm_address_t dst = 0;
    vm_prot_t cur = 0, mx = 0;
    if (mach_vm_remap(mach_task_self(), &dst, size, 0, VM_FLAGS_ANYWHERE, mach_task_self(), base, FALSE, &cur, &mx, VM_INHERIT_NONE)) return NULL;
    if (mach_vm_protect(mach_task_self(), dst, size, 0, VM_PROT_READ | VM_PROT_WRITE)) return NULL;
    return (uint8_t *)dst;
}

// A thunk page range near the module, which every text word reaches with a B (±128 MB), in the gaps between the modules (after
// its mapping, else below its base), never past the last module's end, where nxnative's guest address space goes
static uint8_t *thunks_near(const NxwSwapinMapped *m, size_t bytes) {
    uint64_t top = 0;
    for (int i = 0; i < H->nmod; i++)
        if (H->mod[i].base + H->mod[i].size > top) top = H->mod[i].base + H->mod[i].size;
    const uint64_t text_end = m->base + m->text_end, reach = 124ull << 20;
    for (uint64_t a = (m->base + m->size + 0x3FFF) & ~0x3FFFull; a + bytes <= top && a + bytes - m->base < reach; a += 0x4000) {
        mach_vm_address_t p = a;
        if (mach_vm_allocate(mach_task_self(), &p, bytes, VM_FLAGS_FIXED) == KERN_SUCCESS) return (uint8_t *)p;
    }
    for (uint64_t a = (m->base - bytes) & ~0x3FFFull; a > 0x4000 && text_end - a < reach; a -= 0x4000) {
        mach_vm_address_t p = a;
        if (mach_vm_allocate(mach_task_self(), &p, bytes, VM_FLAGS_FIXED) == KERN_SUCCESS) return (uint8_t *)p;
    }
    return NULL;
}

// The functions the list names: "all", "module <name>", "<module> <offset>", "<absolute>"; "-" first excludes; # comments
static void read_list(const char *path, uint8_t *pick) {
    FILE *f = fopen(path, "r");
    if (!f) { slog("[RED] cannot read the list %s: nothing swapped", path); return; }
    char line[256];
    unsigned bad = 0;
    while (fgets(line, sizeof line, f)) {
        char *h = strchr(line, '#');
        if (h) *h = 0;
        char *s = line;
        while (*s == ' ' || *s == '\t') s++;
        uint8_t v = 1;
        if (*s == '-') v = 2, s++;
        char a[64] = {0}, b[64] = {0};
        int n = sscanf(s, "%63s %63s", a, b);
        if (n < 1) continue;
        if (!strcmp(a, "all")) {
            for (uint32_t i = 0; i < nxw_swapin_count; i++) if (pick[i] != 2) pick[i] = v;
            continue;
        }
        if (!strcmp(a, "module") && n == 2) {
            for (uint32_t i = 0; i < nxw_swapin_count; i++) {
                const NxwSwapinMapped *m = module_at(nxw_swapin_table[i].guest);
                if (m && !strcmp(m->name, b) && pick[i] != 2) pick[i] = v;
            }
            continue;
        }
        uint64_t addr = 0;
        if (n == 2) {
            for (int i = 0; i < H->nmod; i++) if (!strcmp(H->mod[i].name, a)) addr = H->mod[i].base + strtoull(b, NULL, 16);
        }
        if (!addr) addr = strtoull(a, NULL, 16);
        int64_t f = lifted_at(addr);
        if (f < 0 || nxw_swapin_table[f].guest != addr) { bad++; continue; }
        if (v == 2 || pick[f] != 2) pick[f] = v;
    }
    fclose(f);
    if (bad) slog("%u list entries name no lifted function", bad);
}

// Functions holding one of nxnative's observation gates (<module>.obs.tsv): their host callbacks would not run in lifted code
static void exclude_obs(uint8_t *why) {
    for (int i = 0; i < H->nmod; i++) {
        char p[1200];
        snprintf(p, sizeof p, "%s/%s.obs.tsv", H->module_dir, H->mod[i].name);
        FILE *f = fopen(p, "r");
        if (!f) continue;
        char line[512];
        while (fgets(line, sizeof line, f)) {
            unsigned long long off;
            if (line[0] == '#' || sscanf(line, "%llx", &off) != 1) continue;
            int64_t k = lifted_at(H->mod[i].base + off);
            if (k >= 0) why[k] = X_OBS + 1;
        }
        fclose(f);
    }
}

int nxw_swapin_init(const NxwSwapinHost *host) {
    H = host;
    if (host->api != NXW_SWAPIN_API) {
        fprintf(stderr, "[dm] swapin: host API %u, dylib API %u: refused\n", host->api, NXW_SWAPIN_API);
        return 1;
    }
    // the code was lifted for these bases: nxnative must have mapped every module there
    for (uint32_t i = 0; i < nxw_swapin_module_count; i++) {
        const NxwSwapinLifted *l = &nxw_swapin_modules[i];
        int ok = 0;
        for (int k = 0; k < host->nmod; k++)
            if (!strcmp(host->mod[k].name, l->name)) ok = host->mod[k].base == l->base && host->mod[k].text_end == l->text_size;
        if (!ok) {
            slog("[RED] %s was lifted for base %#llx (text %#llx); nxnative mapped it elsewhere (DM_SWAPIN_BASES): refused", l->name,
                 (unsigned long long)l->base, (unsigned long long)l->text_size);
            return 1;
        }
    }
    if (pthread_key_create(&g_key, NULL)) return 1;
    nxw_swapin_tsd[0] = (uint64_t)g_key * 8;
    nxw_swapin_tsd[1] = host->tsd_k18, nxw_swapin_tsd[2] = host->tsd_ktls, nxw_swapin_tsd[3] = host->tsd_ktlr;
    // a key the TSD fast path cannot reach would read another slot: check it the way nxnative's loader checks its own
    pthread_setspecific(g_key, (void *)0x5a5a);
    if (*(uint64_t *)(tsd_base() + nxw_swapin_tsd[0]) != 0x5a5a) { slog("[RED] TSD layout mismatch: refused"); return 1; }
    pthread_setspecific(g_key, NULL);

    // the D4 table over every module's mapping
    uint64_t lo = ~0ull, hi = 0;
    for (int i = 0; i < host->nmod; i++) {
        if (host->mod[i].base < lo) lo = host->mod[i].base;
        if (host->mod[i].base + host->mod[i].size > hi) hi = host->mod[i].base + host->mod[i].size;
    }
    rt_code_base = lo, rt_code_words = (hi - lo) >> 2;
    uint32_t *table = alloc_high(rt_code_words * 4);
    g_seen = alloc_high(rt_code_words);
    if (!table || !g_seen) { slog("[RED] no room for the tables"); return 1; }
    rt_code_table = table;
    rt_host_base = (uint64_t)&__dso_handle;

    g_by_addr = malloc(sizeof *g_by_addr * (nxw_swapin_count + 1));
    for (uint32_t i = 0; i < nxw_swapin_count; i++) g_by_addr[i] = i;
    qsort(g_by_addr, nxw_swapin_count, sizeof *g_by_addr, by_addr_cmp);

    // the choice
    uint8_t *pick = calloc(nxw_swapin_count + 1, 1), *why = calloc(nxw_swapin_count + 1, 1);
    const char *list = getenv("DM_SWAPIN_LIST");
    if (list && *list) read_list(list, pick);
    exclude_obs(why);
    for (uint32_t i = 0; i < nxw_swapin_count; i++) {
        if (nxw_swapin_table[i].flags & FN_CONTEXT) why[i] = X_FLAGS + 1;
        if (pick[i] == 2 && !why[i]) why[i] = X_USER + 1;
    }

    // thunks and entries, module by module
    unsigned nx[X_N] = {0}, nswap = 0;
    for (int k = 0; k < host->nmod; k++) {
        const NxwSwapinMapped *m = &host->mod[k];
        size_t n = 0;
        for (uint32_t i = 0; i < nxw_swapin_count; i++)
            if (pick[i] == 1 && !why[i] && module_at(nxw_swapin_table[i].guest) == m) n++;
        if (!n) continue;
        const size_t bytes = (n * 48 + 0x3FFF) & ~0x3FFFull;
        uint8_t *th = thunks_near(m, bytes);
        uint8_t *alias = th ? alias_of(m->base, (m->text_end + 0x3FFF) & ~0x3FFFull) : NULL;
        if (!th || !alias) slog("[RED] %s: no thunk pages or no writable alias of its code", m->name);
        size_t j = 0;
        for (uint32_t i = 0; i < nxw_swapin_count; i++) {
            const NxwSwapinFn *f = &nxw_swapin_table[i];
            if (pick[i] != 1 || why[i] || module_at(f->guest) != m) continue;
            if (!th || !alias) { why[i] = X_NOTHUNK + 1; continue; }
            uint8_t *t = th + 48 * j++;
            memcpy(t, nxw_swapin_thunk_template, 16);
            uint64_t *lit = (uint64_t *)(t + 16);
            lit[0] = (uint64_t)f->fn, lit[1] = (uint64_t)nxw_swapin_entry, lit[2] = f->guest, lit[3] = 0;
            const int64_t d = (int64_t)((uint64_t)t - f->guest);
            if (d >= (128ll << 20) || d < -(128ll << 20)) { why[i] = X_NOTHUNK + 1; continue; }
            ((uint32_t *)alias)[(f->guest - m->base) >> 2] = 0x14000000u | ((uint32_t)(d >> 2) & 0x3FFFFFFu);
            table[(f->guest - lo) >> 2] = (uint32_t)((uint64_t)f->fn - rt_host_base);
            nswap++;
        }
        if (th) {
            mprotect(th, bytes, PROT_READ | PROT_EXEC);
            sys_icache_invalidate(th, bytes);
        }
        sys_icache_invalidate((void *)m->base, m->text_end);
        slog("%s: %zu functions swapped in, thunks at %p", m->name, j, (void *)th);
    }
    for (uint32_t i = 0; i < nxw_swapin_count; i++)
        if (pick[i] == 1 && why[i]) nx[why[i] - 1]++;
    slog("lifted for main %s: %u functions in the dylib, %u swapped in", nxw_swapin_build_id, nxw_swapin_count, nswap);
    for (int r = 0; r < X_N; r++)
        if (nx[r]) slog("  not swapped: %u, %s", nx[r], k_xname[r]);
    const char *out = getenv("DM_SWAPIN_APPLIED");
    if (out && *out) {   // what ran lifted, for the driver
        FILE *f = fopen(out, "w");
        if (f) {
            for (uint32_t i = 0; i < nxw_swapin_count; i++) {
                const NxwSwapinMapped *m = module_at(nxw_swapin_table[i].guest);
                if (pick[i] == 1 && m) fprintf(f, "%s %llx%s\n", m->name, (unsigned long long)(nxw_swapin_table[i].guest - m->base), why[i] ? " #excluded" : "");
            }
            fclose(f);
        }
    }
    free(pick), free(why);
    if (host->cov_note) rt_trace_on = 1;
    const char *st = getenv("DM_SWAPIN_STATS");
    if (st && *st) {
        pthread_t th;
        g_stats_on = 1;
        if (!pthread_create(&th, NULL, stats_main, (void *)st)) pthread_detach(th);
    }
    return 0;
}
