// The swap-in transitions (docs/m4-swapin.md §2), arm64 macOS. Offsets are NxwGuestCpu's (src/abi/include/nxw/guest_cpu.h):
// x0..x30 at +0, q0..q31 at +256, nzcv / fpsr / fpcr / sp at +768 / +776 / +784 / +792, pc +800, tpidr_el0 +808,
// tpidrro_el0 +816, excl_addr +824; SwapThread's host_sp follows the 896-byte NxwGuestCpu at +896 (swapin_rt.c).
// nxw_swapin_tsd = {our key, nxnative's K18, KTLS, KTLR} as TSD byte offsets: the guest's x18, tpidr_el0 and tpidrro_el0 live
// in nxnative's thread slots (its rewriter moved them there), never in the hardware registers.
#define CPU_SP 792
#define HOST_SP 896

    .text

// One per swapped function, in a page near its module (a B from the entry reaches it): 16 bytes of code, then {the lifted
// function, nxw_swapin_entry, the guest address, 0}. The guest's x16 and x17 go below its sp: at a function entry that is free.
    .globl _nxw_swapin_thunk_template
    .p2align 4
_nxw_swapin_thunk_template:
    stp     x16, x17, [sp, #-16]
    adr     x16, 1f
    ldr     x17, [x16, #8]
    br      x17
1:  .quad 0, 0, 0, 0

.macro TSD_BASE reg
    mrs     \reg, tpidrro_el0
    and     \reg, \reg, #0xfffffffffffffff8
.endm

// x16 = the thunk's literals; the guest's x16 and x17 at [sp - 16]. Saves the guest's registers into this thread's NxwGuestCpu,
// runs the lifted function on the thread's host stack, then loads the registers back and returns to the guest's x30.
    .globl _nxw_swapin_entry
    .p2align 4
_nxw_swapin_entry:
    stp     x14, x15, [sp, #-32]
    TSD_BASE x15
    adrp    x14, _nxw_swapin_tsd@PAGE
    ldr     x14, [x14, _nxw_swapin_tsd@PAGEOFF]
    ldr     x15, [x15, x14]
    cbz     x15, 9f
1:  stp     x0, x1, [x15, #0]
    stp     x2, x3, [x15, #16]
    stp     x4, x5, [x15, #32]
    stp     x6, x7, [x15, #48]
    stp     x8, x9, [x15, #64]
    stp     x10, x11, [x15, #80]
    stp     x12, x13, [x15, #96]
    ldp     x0, x1, [sp, #-32]
    stp     x0, x1, [x15, #112]
    ldp     x0, x1, [sp, #-16]
    stp     x0, x1, [x15, #128]
    stp     x19, x20, [x15, #152]
    stp     x21, x22, [x15, #168]
    stp     x23, x24, [x15, #184]
    stp     x25, x26, [x15, #200]
    stp     x27, x28, [x15, #216]
    stp     x29, x30, [x15, #232]
    stp     q0, q1, [x15, #256]
    stp     q2, q3, [x15, #288]
    stp     q4, q5, [x15, #320]
    stp     q6, q7, [x15, #352]
    stp     q8, q9, [x15, #384]
    stp     q10, q11, [x15, #416]
    stp     q12, q13, [x15, #448]
    stp     q14, q15, [x15, #480]
    stp     q16, q17, [x15, #512]
    stp     q18, q19, [x15, #544]
    stp     q20, q21, [x15, #576]
    stp     q22, q23, [x15, #608]
    stp     q24, q25, [x15, #640]
    stp     q26, q27, [x15, #672]
    stp     q28, q29, [x15, #704]
    stp     q30, q31, [x15, #736]
    mrs     x0, nzcv
    mrs     x1, fpsr
    str     x0, [x15, #768]
    str     x1, [x15, #776]
    mrs     x0, fpcr
    mov     x1, sp
    str     x0, [x15, #784]
    str     x1, [x15, #792]
    TSD_BASE x0
    adrp    x1, _nxw_swapin_tsd@PAGE
    add     x1, x1, _nxw_swapin_tsd@PAGEOFF
    ldp     x2, x3, [x1, #8]
    ldr     x4, [x1, #24]
    ldr     x2, [x0, x2]
    str     x2, [x15, #144]           // x18
    ldr     x3, [x0, x3]
    ldr     x4, [x0, x4]
    str     x3, [x15, #808]           // tpidr_el0
    str     x4, [x15, #816]           // tpidrro_el0
    ldr     x0, [x16, #16]
    str     x0, [x15, #800]           // pc = the function
    mov     x1, #-1
    str     x1, [x15, #824]           // nothing in the exclusive monitor
    ldr     x16, [x16]
    ldr     x0, [x15, #HOST_SP]
    mov     sp, x0
    mov     x19, x15
    mov     x0, x15
    mov     x29, #0                   // host backtraces end at the lifted code
    blr     x16
    mov     x15, x19
    b       _nxw_swapin_resume

9:  // the thread's first entry: nxw_swapin_thread_new (C, on the guest's stack below the 32 bytes in use) makes its state
    sub     sp, sp, #(32 + 704)
    stp     x0, x1, [sp, #0]
    stp     x2, x3, [sp, #16]
    stp     x4, x5, [sp, #32]
    stp     x6, x7, [sp, #48]
    stp     x8, x9, [sp, #64]
    stp     x10, x11, [sp, #80]
    stp     x12, x13, [sp, #96]
    stp     x16, x30, [sp, #112]
    mrs     x0, nzcv
    mrs     x1, fpsr
    stp     x0, x1, [sp, #128]
    stp     q0, q1, [sp, #144]
    stp     q2, q3, [sp, #176]
    stp     q4, q5, [sp, #208]
    stp     q6, q7, [sp, #240]
    stp     q8, q9, [sp, #272]
    stp     q10, q11, [sp, #304]
    stp     q12, q13, [sp, #336]
    stp     q14, q15, [sp, #368]
    stp     q16, q17, [sp, #400]
    stp     q18, q19, [sp, #432]
    stp     q20, q21, [sp, #464]
    stp     q22, q23, [sp, #496]
    stp     q24, q25, [sp, #528]
    stp     q26, q27, [sp, #560]
    stp     q28, q29, [sp, #592]
    stp     q30, q31, [sp, #624]
    bl      _nxw_swapin_thread_new
    mov     x15, x0
    ldp     q0, q1, [sp, #144]
    ldp     q2, q3, [sp, #176]
    ldp     q4, q5, [sp, #208]
    ldp     q6, q7, [sp, #240]
    ldp     q8, q9, [sp, #272]
    ldp     q10, q11, [sp, #304]
    ldp     q12, q13, [sp, #336]
    ldp     q14, q15, [sp, #368]
    ldp     q16, q17, [sp, #400]
    ldp     q18, q19, [sp, #432]
    ldp     q20, q21, [sp, #464]
    ldp     q22, q23, [sp, #496]
    ldp     q24, q25, [sp, #528]
    ldp     q26, q27, [sp, #560]
    ldp     q28, q29, [sp, #592]
    ldp     q30, q31, [sp, #624]
    ldp     x0, x1, [sp, #128]
    msr     nzcv, x0
    msr     fpsr, x1
    ldp     x0, x1, [sp, #0]
    ldp     x2, x3, [sp, #16]
    ldp     x4, x5, [sp, #32]
    ldp     x6, x7, [sp, #48]
    ldp     x8, x9, [sp, #64]
    ldp     x10, x11, [sp, #80]
    ldp     x12, x13, [sp, #96]
    ldp     x16, x30, [sp, #112]
    add     sp, sp, #(32 + 704)
    b       1b

// x15 = the thread's NxwGuestCpu: load every guest register from it (the thread slots too) and return to its x30
    .globl _nxw_swapin_resume
    .p2align 4
_nxw_swapin_resume:
    ldr     x0, [x15, #784]
    mrs     x1, fpcr
    cmp     x0, x1
    b.eq    1f
    msr     fpcr, x0
1:  ldp     q0, q1, [x15, #256]
    ldp     q2, q3, [x15, #288]
    ldp     q4, q5, [x15, #320]
    ldp     q6, q7, [x15, #352]
    ldp     q8, q9, [x15, #384]
    ldp     q10, q11, [x15, #416]
    ldp     q12, q13, [x15, #448]
    ldp     q14, q15, [x15, #480]
    ldp     q16, q17, [x15, #512]
    ldp     q18, q19, [x15, #544]
    ldp     q20, q21, [x15, #576]
    ldp     q22, q23, [x15, #608]
    ldp     q24, q25, [x15, #640]
    ldp     q26, q27, [x15, #672]
    ldp     q28, q29, [x15, #704]
    ldp     q30, q31, [x15, #736]
    TSD_BASE x0
    adrp    x1, _nxw_swapin_tsd@PAGE
    add     x1, x1, _nxw_swapin_tsd@PAGEOFF
    ldp     x2, x3, [x1, #8]
    ldr     x4, [x15, #144]
    str     x4, [x0, x2]              // x18 into its slot
    ldr     x4, [x15, #808]
    str     x4, [x0, x3]              // tpidr_el0 into its slot
    ldr     x0, [x15, #768]
    ldr     x1, [x15, #776]
    msr     nzcv, x0                  // nothing below sets flags
    msr     fpsr, x1
    ldr     x0, [x15, #CPU_SP]
    mov     sp, x0
    ldp     x0, x1, [x15, #0]
    ldp     x2, x3, [x15, #16]
    ldp     x4, x5, [x15, #32]
    ldp     x6, x7, [x15, #48]
    ldp     x8, x9, [x15, #64]
    ldp     x10, x11, [x15, #80]
    ldp     x12, x13, [x15, #96]
    ldp     x16, x17, [x15, #128]
    ldp     x19, x20, [x15, #152]
    ldp     x21, x22, [x15, #168]
    ldp     x23, x24, [x15, #184]
    ldp     x25, x26, [x15, #200]
    ldp     x27, x28, [x15, #216]
    ldp     x29, x30, [x15, #232]
    ldp     x14, x15, [x15, #112]
    ret

// void nxw_swapin_call_native(NxwGuestCpu *cpu, uint64_t target): runs native guest code at target with cpu's registers, on the
// guest's stack, and comes back here when it returns; cpu then holds the registers it left, with x30 as it was at the call (what
// the guest sees after a return). x17 carries the target, as a veneer may. Nested entries (native code calling a swapped
// function) put their host frames below this one: host_sp is this frame while the native code runs.
    .globl _nxw_swapin_call_native
    .p2align 4
_nxw_swapin_call_native:
    stp     x29, x30, [sp, #-176]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    stp     x25, x26, [sp, #64]
    stp     x27, x28, [sp, #80]
    stp     d8, d9, [sp, #96]
    stp     d10, d11, [sp, #112]
    stp     d12, d13, [sp, #128]
    stp     d14, d15, [sp, #144]
    ldr     x9, [x0, #HOST_SP]
    ldr     x10, [x0, #240]
    stp     x9, x10, [sp, #160]       // host_sp before this call, the guest's x30 at the call
    mov     x9, sp
    str     x9, [x0, #HOST_SP]
    mov     x15, x0
    mov     x17, x1
    TSD_BASE x0
    adrp    x1, _nxw_swapin_tsd@PAGE
    add     x1, x1, _nxw_swapin_tsd@PAGEOFF
    ldp     x2, x3, [x1, #8]
    ldr     x4, [x15, #144]
    str     x4, [x0, x2]
    ldr     x4, [x15, #808]
    str     x4, [x0, x3]
    ldr     x0, [x15, #784]
    mrs     x1, fpcr
    cmp     x0, x1
    b.eq    1f
    msr     fpcr, x0
1:  ldr     x0, [x15, #776]
    msr     fpsr, x0
    ldp     q0, q1, [x15, #256]
    ldp     q2, q3, [x15, #288]
    ldp     q4, q5, [x15, #320]
    ldp     q6, q7, [x15, #352]
    ldp     q8, q9, [x15, #384]
    ldp     q10, q11, [x15, #416]
    ldp     q12, q13, [x15, #448]
    ldp     q14, q15, [x15, #480]
    ldp     q16, q17, [x15, #512]
    ldp     q18, q19, [x15, #544]
    ldp     q20, q21, [x15, #576]
    ldp     q22, q23, [x15, #608]
    ldp     q24, q25, [x15, #640]
    ldp     q26, q27, [x15, #672]
    ldp     q28, q29, [x15, #704]
    ldp     q30, q31, [x15, #736]
    ldr     x0, [x15, #CPU_SP]
    mov     sp, x0
    ldr     x0, [x15, #768]
    msr     nzcv, x0
    adr     x30, 2f
    ldp     x0, x1, [x15, #0]
    ldp     x2, x3, [x15, #16]
    ldp     x4, x5, [x15, #32]
    ldp     x6, x7, [x15, #48]
    ldp     x8, x9, [x15, #64]
    ldp     x10, x11, [x15, #80]
    ldp     x12, x13, [x15, #96]
    ldr     x16, [x15, #128]
    ldp     x19, x20, [x15, #152]
    ldp     x21, x22, [x15, #168]
    ldp     x23, x24, [x15, #184]
    ldp     x25, x26, [x15, #200]
    ldp     x27, x28, [x15, #216]
    ldr     x29, [x15, #232]
    ldp     x14, x15, [x15, #112]
    br      x17
2:  stp     x14, x15, [sp, #-16]      // back: below the guest's sp is free after a return
    TSD_BASE x15
    adrp    x14, _nxw_swapin_tsd@PAGE
    ldr     x14, [x14, _nxw_swapin_tsd@PAGEOFF]
    ldr     x15, [x15, x14]
    stp     x0, x1, [x15, #0]
    stp     x2, x3, [x15, #16]
    stp     x4, x5, [x15, #32]
    stp     x6, x7, [x15, #48]
    stp     x8, x9, [x15, #64]
    stp     x10, x11, [x15, #80]
    stp     x12, x13, [x15, #96]
    ldp     x0, x1, [sp, #-16]
    stp     x0, x1, [x15, #112]
    stp     x16, x17, [x15, #128]
    stp     x19, x20, [x15, #152]
    stp     x21, x22, [x15, #168]
    stp     x23, x24, [x15, #184]
    stp     x25, x26, [x15, #200]
    stp     x27, x28, [x15, #216]
    str     x29, [x15, #232]
    stp     q0, q1, [x15, #256]
    stp     q2, q3, [x15, #288]
    stp     q4, q5, [x15, #320]
    stp     q6, q7, [x15, #352]
    stp     q8, q9, [x15, #384]
    stp     q10, q11, [x15, #416]
    stp     q12, q13, [x15, #448]
    stp     q14, q15, [x15, #480]
    stp     q16, q17, [x15, #512]
    stp     q18, q19, [x15, #544]
    stp     q20, q21, [x15, #576]
    stp     q22, q23, [x15, #608]
    stp     q24, q25, [x15, #640]
    stp     q26, q27, [x15, #672]
    stp     q28, q29, [x15, #704]
    stp     q30, q31, [x15, #736]
    mrs     x0, nzcv
    mrs     x1, fpsr
    str     x0, [x15, #768]
    str     x1, [x15, #776]
    mrs     x0, fpcr
    mov     x1, sp
    str     x0, [x15, #784]
    str     x1, [x15, #792]
    TSD_BASE x0
    adrp    x1, _nxw_swapin_tsd@PAGE
    add     x1, x1, _nxw_swapin_tsd@PAGEOFF
    ldp     x2, x3, [x1, #8]
    ldr     x4, [x1, #24]
    ldr     x2, [x0, x2]
    str     x2, [x15, #144]
    ldr     x3, [x0, x3]
    ldr     x4, [x0, x4]
    str     x3, [x15, #808]
    str     x4, [x15, #816]
    mov     x0, #-1
    str     x0, [x15, #824]
    ldr     x9, [x15, #HOST_SP]
    mov     sp, x9
    ldp     x9, x10, [sp, #160]
    str     x9, [x15, #HOST_SP]
    str     x10, [x15, #240]
    ldp     d8, d9, [sp, #96]
    ldp     d10, d11, [sp, #112]
    ldp     d12, d13, [sp, #128]
    ldp     d14, d15, [sp, #144]
    ldp     x19, x20, [sp, #16]
    ldp     x21, x22, [sp, #32]
    ldp     x23, x24, [sp, #48]
    ldp     x25, x26, [sp, #64]
    ldp     x27, x28, [sp, #80]
    ldp     x29, x30, [sp], #176
    ret

// rt_miss's answer for a target that runs native (an NxwGuestFn): the target is in cpu->pc
    .globl _nxw_swapin_native_fn
    .p2align 4
_nxw_swapin_native_fn:
    ldr     x1, [x0, #800]
    b       _nxw_swapin_call_native
