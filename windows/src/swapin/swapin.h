/* The Mac swap-in harness (DESIGN D15.2, docs/m4-swapin.md): lifted code compiled for arm64 runs inside the real game under
   nxnative, function by function. This header is the contract between the swap-in dylib (src/swapin: the lifted functions, their
   table and this runtime) and nxnative's side (nxnative/hle_swapin.c, copied into a copy of nxnative), which loads the dylib.
   C only; arm64 macOS only. */
#ifndef NXW_SWAPIN_H
#define NXW_SWAPIN_H
#include <stdarg.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NXW_SWAPIN_API 2

/* A module as nxnative mapped it */
typedef struct {
    char name[16];
    uint64_t base, size;   /* the mapping: image and the rewriter's patch area */
    uint64_t text_end;     /* the original text's end (module offset) */
} NxwSwapinMapped;

/* What nxnative hands the dylib (function pointers into the host, so the dylib links against nothing of nxnative's) */
typedef struct {
    uint32_t api;                 /* NXW_SWAPIN_API */
    int nmod;
    NxwSwapinMapped mod[8];
    uint64_t tsd_k18, tsd_ktls, tsd_ktlr;   /* nxnative's thread slots as TSD byte offsets (key × 8): x18, tpidr_el0, tpidrro_el0 */
    const char *module_dir;       /* the rewritten modules' folder (<module>.obs.tsv) */
    void (*log)(const char *fmt, va_list ap);
    uint64_t (*svc_dispatch)(int num, uint64_t *regs, uint64_t orig);
    void (*thr_svc_enter)(uint64_t *regs, uint64_t pc);
    void (*thr_svc_leave)(void);
    uint64_t (*guest_ticks)(void);
    void (*guest_clock_read_wait)(void);
    void (*spin_wait)(uint64_t gate, uint64_t site);
    uint32_t (*thread_handle)(void);
    /* the trace module's coverage log (hle_trace.c), or null: kind 3 = first entry of a lifted function, 4 = first entry of a
       function run through its thunk (the entry address) */
    void (*cov_note)(uint64_t pc, int kind);
} NxwSwapinHost;

/* The dylib's entry point: reads DM_SWAPIN_LIST and patches the chosen functions' entries. 0 = done; nonzero = refused (logged) */
typedef int (*NxwSwapinInit)(const NxwSwapinHost *host);
int nxw_swapin_init(const NxwSwapinHost *host);

/* The table nxlift --swapin writes (nxlift/lift.hpp swapin_table_object) */
typedef struct {
    uint64_t guest;
    void (*fn)(void *cpu);
    uint32_t flags;   /* program.db's function flags (nxanalyze/db.hpp fn_*) */
    uint32_t size;
} NxwSwapinFn;
typedef struct {
    char name[16];
    uint64_t base, text_size;
} NxwSwapinLifted;

#ifdef __cplusplus
}
#endif
#endif
