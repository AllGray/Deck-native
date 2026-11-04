# M3: the lifter, v0

Status, 2026-11-03: v0 done. Instruction semantics written once in C++ (`src/sem`), compiled to LLVM bitcode for the lifter
and natively for the interpreter; a lifter (`src/nxlift`) that turns program.db's functions into optimised x86-64 code (COFF
objects, or ORC JIT for tests); the D4 fallback interpreter (`src/interp`). On Windows, **30 real game functions** (25 from sdk,
5 from main) ran lifted, interpreted and against a host reference on 3,000 random inputs each with **0 mismatches**, and every
op the game uses was checked alone on real instruction words, lifted against interpreted, with **0 differences**. **Every
function of main, sdk and subsdk0 (2.0.1) lifts and compiles**, with **0 instructions lacking semantics**.

## 1. What was built

| folder | what |
|---|---|
| `src/sem/include/nxw/sem.hpp` | the `State` a lifted function works on, `nxw_sem_enter` / `leave` / `leave_mask` / `lookup` / `sysreg_inline` |
| `src/sem/include/nxw/rt.h` | the runtime contract: every symbol lifted code and the interpreter need from nxrt, and the trap codes |
| `src/sem/src/common.hpp`, `fpx.hpp` | helpers: registers, flags, operands, bit masks, guest memory, lanes, FP rules, the exact estimates |
| `src/sem/src/int.cpp`, `mem.cpp`, `fp.cpp`, `simd.cpp`, `state.cpp`, `sysreg.cpp` | 721 semantic functions, one per decoder op |
| `src/sem/gen_ops.py` | lists the ops with semantics (from the library's symbols) for the interpreter's dispatch |
| `src/interp/` | `nxw_interp_run` / `nxw_interp_trampoline`; `tests/kat.cpp`, known answers (no game data) |
| `src/nxlift/src/lift.cpp` | program.db function to IR, semantics linking and inlining, alias scopes, O1/O2, objects |
| `src/nxlift/src/inputs.cpp` | the dump and program.db, cross-checked (bases, build IDs) and indexed |
| `src/nxlift/tools/nxlift.cpp` | the CLI: partitions, filters, jobs, `.ll`, report |
| `src/nxlift/tests/lift_tests.cpp` | the JIT harness on real game functions (`NXW_DUMP`) |
| `src/nxlift/src/llvm_stubs.c`, `msvc_stl_shims.cpp` | what LLVM's prebuilt libraries need and the toolchain lacks (section 7) |

About 6,800 lines with the tests. Build: `scripts\build.cmd` builds everything; nxlift needs LLVM's development build in
`..\toolchains\llvm-23.1.4` (`NXW_LLVM_DIR`) and is skipped without it, while sem and interp need only clang.

```
nxlift <ExeFS> --db _local\analyze\2.0.1\program.db --out _local\nxlift\<run> [--module main] [--fn <hex addr>] [--sym <name>]
       [--count N] [--closure] [--O1|--O2] [--ll] [--ll-pre] [--no-obj] [--jobs N (<= 4)] [--partition 1000] [--dis <hex addr>]
set NXW_DUMP=<ExeFS> & build\Release\bin\nxlift-tests.exe        (NXW_TEST_ITERS, NXW_TEST_ONLY, NXW_TEST_OP_WORDS, NXW_TEST_OP_RUNS)
build\Release\bin\interp-kat.exe
```

## 2. Design as built

### 2.1 Semantics, written once

One function per decoder op, `extern "C" uint64_t nxw_sem_<Op>(State* s, uint32_t word, uint64_t pc)`. It decodes its own fields
(the generated `Fields<Op>`, or shared field helpers for the AdvSIMD families), reads registers from `s`, computes, writes
back. The return value is 0, or the condition for conditional branches, or the target for B/BL/BR/BLR/RET. The lifter calls it
with a **constant** word and pc, so after inlining every field, register index and size switch folds away and only the
operation is left: `add x0, x1, #16` becomes one `add` on SSA values. The interpreter calls the same function with a run-time
word. The family helpers (three-same, long, narrow, pairwise, across-lanes, by-element, shift-by-immediate, ...) take a lambda,
so most ops are one line, for example `V3(SQADD, true, sat128<T>(i128(a) + b))`.

- **State** is a local copy of the registers: x0–x30 (x[31] unused: register 31 is SP or zero by instruction), SP, q0–q31 as
  16-byte vectors, NZCV as four `bool`s, FPCR, FPSR, TPIDR_EL0/TPIDRRO_EL0 and the exclusive monitor. Lifted code keeps it in an
  `alloca`, which SROA turns into SSA values (D2: flags are four SSA bits inside a function, packed only at exits).
- **Guest memory** is identity-mapped and every plain access is `volatile` through a packed, `may_alias` struct (align 1, so an
  unaligned 16-byte access never becomes `movaps`). Ordered and atomic accesses use `__atomic` builtins on the guest address
  (D7): LDAR and STLR sequentially consistent, LDAPR acquire, DMB by option (loads-only: acquire fence, stores-only: release,
  else full), LSE through `atomicrmw`/`cmpxchg` with the A/R orderings, CASP and the 64-bit exclusive pair through 128-bit
  `cmpxchg` (x86-64-v3 includes CMPXCHG16B). LDXR/STXR use a monitor of (address, value) and a CAS against the value
  loaded (03 §3.6).
- **Floating point (D6), FPCR = 0 assumed:** FRECPE/FRSQRTE by the Arm ARM's `RecipEstimate`/`RecipSqrtEstimate` (bit-exact,
  scalar and vector, single and double, including denormals, overflow to infinity and the tiny/huge exponent cases); FRECPS and
  FRSQRTS fused with the inf × 0 special cases (FRSQRTS rounds the halved sum once, even where the sum alone would overflow);
  FMIN/FMAX/FMINNM/FMAXNM by the Arm rules (−0 < +0, signalling NaN first, a single quiet NaN ignored by the NM forms);
  saturating FP-to-integer conversions in all five rounding modes (NaN gives 0); FMA through `fma` (one instruction on FMA3) and
  no contraction anywhere else (`-ffp-contract=off`, `FPOpFusion::Strict`); half precision converted by integer arithmetic
  (round to nearest even, no double rounding from double); FCVTXN's round-to-odd; FMULX; FRECPX; FRINT32/64.
  Ordinary arithmetic NaNs follow the host in the default build; `-DNXW_SEM_ACCURATE_NAN=1` applies Arm's FPProcessNaNs and
  default-NaN rules to add/sub/mul/div/sqrt/fma/round (D6's "accurate NaN" build). FPSR exception flags and QC are not kept.
- **Crypto:** AESE/AESD/AESMC/AESIMC (S-boxes generated at compile time), SHA1C/P/M/H/SU0/SU1, SHA256H/H2/SU0/SU1, PMULL
  (8-bit and 64-bit), CRC32/CRC32C.
- **System:** hints (YIELD and WFE spin politely, the PAC hints do nothing: nothing is ever signed), barriers, DC ZVA (64-byte
  blocks, DCZID_EL0 = 4; other DC/IC/TLBI do nothing), CLREX. MRS/MSR inline for NZCV, FPCR (read), FPSR, TPIDR_EL0,
  TPIDRRO_EL0, CTR_EL0 (0x8444c004, Cortex-A78), DCZID_EL0 and CNTFRQ_EL0 (31.25 MHz); everything else, including CNTPCT_EL0 and
  FPCR writes, goes to `rt_mrs`/`rt_msr` (`nxw_sem_sysreg_inline` decides, for lifter and interpreter alike).

Both compilations use clang 23.1.4 with `-march=x86-64-v3 -ffp-contract=off -fno-math-errno` (`NXW_SEM_ARCH`); the bitcode is
`-O2` per source file, `llvm-link`ed, and embedded in nxlift with `#embed`. The same sources build on the Mac (clang, arm64) for
the oracle: nothing in them is x86-specific except the `pause` hint, which has an arm64 branch.

### 2.2 The lifter

Per guest function `void fn_<address hex>(ptr noalias nonnull align 16 dereferenceable(896) %cpu)`, `noinline`, `nounwind`
(objects also `uwtable`):

1. **Entry:** `alloca State`, `nxw_sem_enter(s, cpu)`, then the blocks of program.db's CFG, each instruction a call of its
   semantic function. Spin-loop heads (program.db) start with a `yield` hint.
2. **Control flow:**
   - **BL** sets x30 through its semantics, then `leave`, a direct `call @fn_<callee>` (program.db's binding: through a PLT
     stub, straight to the bound function), `enter`. A callee bound to host code is `call @adapter_<symbol>`, an adapter per name
     the runtime provides. When a call is bound through a PLT stub, x16 and x17 get what the stub would leave (the GOT slot and
     its content), so lifted code stays bit-exact with the guest.
   - **B to another function** (and conditional tail calls) is `leave` + `musttail call` + `ret`.
   - **RET/RETAA** is `leave` + `ret`. `ret xN` (N ≠ 30) returns only when xN equals the x30 the function was entered with;
     otherwise it is an indirect tail call.
   - **BLR** and **BR** that is not a jump table: `leave`, `nxw_sem_lookup(cpu, target)` (D4: `rt_code_table`, else `rt_miss`),
     call or `musttail` call. The lookup is itself in the semantics and inlined.
   - **Jump tables** become `switch` on the computed target address, cases from program.db's decoded table; the default calls
     `rt_trap` (every table, not only the 118 unbounded ones: it costs one compare).
   - **SVC** stores pc in `cpu->pc` and calls `rt_svc(cpu, imm)`; MRS/MSR of rare registers call `rt_mrs`/`rt_msr`; UDF, BRK,
     HLT, unallocated words, ERET, a noreturn call that returns, falling off a function, and any op without semantics call
     `rt_trap(cpu, pc, why)` (codes in `rt.h`).
3. **finish():** link the semantics bitcode in (`Linker`, only what the module uses), then inline in two phases. Phase 1 inlines
   the instruction semantics only; the lifter then reads, per function, **which State fields its instructions write** (every
   store into the State alloca, with constant offsets; anything unclear counts as everything) and replaces each `leave` by
   `nxw_sem_leave_mask` with those fields. A field nothing writes still holds what the last `enter` read, which is what cpu
   holds, so storing it would be a no-op. Phase 2 inlines enter/leave/lookup. Finally every guest memory access is tagged
   `!noalias` against a "cpu" alias scope and every cpu access `!alias.scope` (guest memory never overlaps NxwGuestCpu), so GVN
   and DSE keep registers in SSA values across volatile guest accesses; calls still clobber cpu. The module is verified.
4. **optimize():** LLVM's default O1 or O2 module pipeline. Lifted functions are `noinline`, so there is no cross-function
   inlining (03 §3.8).
5. **Objects:** COFF x86-64 for `x86_64-pc-windows-msvc`, `-mcpu x86-64-v3`, one per partition of 1,000 consecutive functions of
   a module (a function over 16 KB gets its own). The four `rt_code_*`/`rt_host_base` data symbols are `dllimport` (nxrt.exe
   exports them); functions are plain externs.

The result for sdk's `strlen` (64 instructions, a word-at-a-time loop) is 396 bytes of x86 that loads x0, x11 and x12 from cpu,
keeps everything in registers, and stores back only the six registers and NZCV it changes.

**The runtime contract** (all in `src/sem/include/nxw/rt.h`): `rt_svc`, `rt_mrs`, `rt_msr`, `rt_trap` (noreturn), `rt_miss`
(returns a NxwGuestFn; for an interpreter entry it sets `cpu->pc` and returns `nxw_interp_trampoline`), `rt_code_table`,
`rt_code_base`, `rt_code_words`, `rt_host_base` (table entries are offsets from it), plus `adapter_<symbol>` adapters, and
`memcpy`/`memset`/`__chkstk` if LLVM emits them. One requirement on the runtime: **an NxwGuestCpu must not live in memory the
guest can address** (the alias scopes above assume guest accesses never touch it). **No ABI field was added or changed**; `cpu->pc` carries an interpreter
trampoline's entry address, consistent with its "valid while the runtime holds control" role.

### 2.3 The interpreter

`nxw_interp_run(cpu, entry)` loads a State from cpu, then loops: fetch the word at pc, decode, dispatch through a table of
the same `nxw_sem_<Op>` functions (`sem_ops.inc`), and do the control transfers as the lifter does: BL/BLR store the state and
call through `nxw_sem_lookup` (translated code where it exists; a miss interprets the callee through `rt_miss`), RET returns to
the host caller, BR that hits `rt_code_table` is a tail call into translated code and otherwise continues interpreting at the
target (a jump-table label, or a tail-called function whose RET then returns to our caller: both correct), SVC/MRS/MSR/traps
through `rt_*`. It is reentrant and keeps per-thread counters (`nxw::interp::stats()`).

## 3. Tests

### 3.1 Real game functions (nxlift-tests, Pokopia 2.0.1)

The modules are mapped at their own addresses (VirtualAlloc at 0x7100000000), each test function plus every guest function it
calls is lifted into one module and JIT-compiled (ORC LLJIT, large code model), and the D4 table is filled for them. Each input
runs three ways: lifted; interpreted with the table emptied (so every callee is interpreted too); and through a host C
reference on a copy of the memory. Lifted and interpreted runs must leave **identical** registers (x0–x30, SP, NZCV, FPCR, FPSR,
q0–q31, TPIDR*, the monitor) and identical memory (256 KB of buffers); the host reference must agree on the result (exactly,
or within 1 ulp of a double-precision reference for sin/cos/exp/log, which no two libms round alike; NaN agrees with any NaN).
Every register starts with garbage, as a caller leaves it.

Inputs: mem/str functions at alignments 0–63, lengths 0–8 KB (biased to 0–16, 0–128, 0–1 KB), overlapping moves both ways,
bytes ≥ 0x80, characters passed with high garbage bits, the terminator; numbers with spaces, signs, `0x`, every base 2–36,
up to 40 digits (overflow); floats from every class: ±0, ±inf, quiet and signalling NaNs, denormals, ±FLT_MAX, 2^23
boundaries, multiples of π/2, plus uniform ranges.

| function | module+offset | insns | inputs | lifted vs interpreted | vs host | max ulp |
|---|---|---:|---:|---:|---:|---:|
| memcpy (`__memcpy_device`, a tail call through a PLT stub) | sdk+0x6d7540 | 1 | 3,000 | 0 | 0 | |
| memmove | sdk+0x6d7560 | 95 | 3,000 | 0 | 0 | |
| memset | sdk+0x6d7820 | 55 | 3,000 | 0 | 0 | |
| memcmp | sdk+0x704180 | 59 | 3,000 | 0 | 0 | |
| strlen | sdk+0x6ef880 | 64 | 3,000 | 0 | 0 | |
| strnlen | sdk+0x6efb90 | 15 | 3,000 | 0 | 0 | |
| strcmp | sdk+0x704280 | 51 | 3,000 | 0 | 0 | |
| strncmp | sdk+0x704380 | 77 | 3,000 | 0 | 0 | |
| strchr | sdk+0x6ef1c0 | 12 | 3,000 | 0 | 0 | |
| strrchr | sdk+0x6efc10 | 14 | 3,000 | 0 | 0 | |
| memchr | sdk+0x6d6ea0 | 121 | 3,000 | 0 | 0 | |
| atoi | sdk+0x6ece20 | 41 | 3,000 | 0 | 0 | |
| strtol (`__strtol_internal`, calls `__shlim` and `__intscan`) | sdk+0x6edde0 | 33 | 3,000 | 0 | 0 | |
| sqrtf, floorf, ceilf, truncf, roundf | sdk+0x6fbeb0 … | 2 each | 3,000 each | 0 | 0 | exact |
| sinf (calls a rounding helper) | sdk+0x6f8cf0 | 134 | 3,000 | 0 | 0 | 1 |
| cosf | sdk+0x6f2db0 | 132 | 3,000 | 0 | 0 | 1 |
| expf | sdk+0x6f3c30 | 61 | 3,000 | 0 | 0 | 1 |
| logf | sdk+0x6f7e70 | 54 | 3,000 | 0 | 0 | 0 |
| fmodf | sdk+0x6ffe60 | 100 | 3,000 | 0 | 0 | exact |
| floor | sdk+0x6fbd40 | 2 | 3,000 | 0 | 0 | exact |
| fmod | sdk+0x6ffcc0 | 102 | 3,000 | 0 | 0 | exact |
| main: 5 integer leaf functions (first by address, ≥ 12 instructions, registers only) | main+0x37530, +0xeca84, +0xecc90, +0xecdc0, +0xecef0 | 17–100 | 3,000 each | 0 | (no reference) | |
| **total: 30 functions, 50 lifted with callees** | | | **90,000** | **0** | **0** | |

`strtol`'s reference is a C11-conforming `strtoll` written for the test: UCRT's
own gives "no conversion" for `" +0xM"` with base 0, where the subject sequence is `+0` and the end pointer must be at the
`x`; the game's code (musl-style) gets it right.

### 3.2 Every op the game uses, alone

The same harness then samples up to 24 instruction words per op from the text of all three modules (reservoir sampling, fixed
seed), skipping only branches, SVC, traps and runtime-handled system registers (phase 1 covers those), lifts each as
`[word] + ret` and runs it on 8 random states, lifted and interpreted: random registers, half the time FP-special lanes in the
vector registers, and for memory operations a base register into a mapped window, small offsets, and a matching exclusive
monitor a third of the time. **410 ops, 6,219 words, 49,752 runs: 0 differences** (6 s).

This phase found a real lifter bug that the function tests had missed: the written-field analysis took the first `alloca` of
the entry block for the State, but inlining moves the semantics' own temporaries in front of it, so writes made through
lane-wise SIMD helpers were dropped at the exit store. The State is now the operand of `nxw_sem_enter`.

### 3.3 Known answers (interp-kat, no game data)

297 checks of values that follow from the Arm ARM or from standard check values: FRECPE/FRSQRTE (1, 2, 3, 0.5, 0.25,
±0, ±inf, NaNs, 2^-130/2^-128/2^-127 at the overflow edge, 2^126 into a denormal, the smallest denormal, the 4S and 2D vector
forms), FRECPS/FRSQRTS special cases and the once-rounded overflow case, FMIN/FMAX/FMINNM/FMAXNM with ±0 and NaN priorities,
every FP-to-integer rounding mode with saturation, half precision (ties, denormals, overflow, NaNs, no double rounding from
double), FMADD rounding once, FCMP flags, ADDS/SUBS flags, CLS/CLZ/RBIT/REV/bitfields/EXTR/CCMP/MULH/SDIV/UDIV corner cases,
bitmask immediates, CRC-32 and CRC-32C check values, SIMD saturation, permutes, TBL out of range, shifts by ≥ the lane size,
narrowing, PMULL, the FIPS-197 AES round, **SHA-256 and SHA-1 of "abc" through the hash instructions**, and loads, stores,
CAS, LDADD and LDAXR/STLXR success and failure. **297 checks, 0 failures.**

## 4. Coverage (2.0.1)

One run, `nxlift ... --jobs 4` at O2 and idle priority, every function of the three modules (`_local
xliftull-o2`):

| module | partitions | functions | instructions | lifted | failed | instructions without semantics |
|---|---:|---:|---:|---:|---:|---:|
| main | 247 | 149,688 | 10,198,713 | all | 0 | 0 |
| sdk | 55 | 47,994 | 1,983,328 | all | 0 | 0 |
| subsdk0 | 3 | 2,732 | 178,926 | all | 0 | 0 |
| **total** | **305** | **200,414** | **12,360,967** | **all** | **0** | **0** |

What the lifter emitted: 2,010,596 blocks; 521,376 direct calls to guest functions and 8,257 to host adapters; 153,997
indirect calls through the D4 lookup; 54,810 direct and 19,583 indirect tail calls; 6,957 jump tables; 57 SVCs; 48
`rt_mrs`/`rt_msr` calls (CNTPCT_EL0 reads and FPCR writes, all in sdk); 25,147 trap sites (UDF padding inside functions,
jump-table defaults, noreturn fallbacks). Every module verifies after inlining, and every partition compiled to an object.

**Instructions without semantics: 0** in all three modules, in every class (integer, branch, load/store, LSE, system, scalar
FP, AdvSIMD, crypto). By decoder section (a64.tbl):

| section | encodings | with semantics (or lifter-handled) | used by the game | used, covered | words in the game |
|---|---:|---:|---:|---:|---:|
| data processing: immediate | 17 | 17 | 17 | 17 | 2,729,577 |
| data processing: register | 56 | 53 | 49 | 49 | 2,108,912 |
| branches, exceptions, system | 47 | 36 | 19 | 19 | 2,600,219 |
| loads and stores | 141 | 139 | 85 | 85 | 4,339,201 |
| scalar floating point | 58 | 57 | 42 | 42 | 418,063 |
| cryptography | 14 | 14 | 14 | 14 | 1,500 |
| AdvSIMD scalar | 113 | 109 | 18 | 18 | 30,111 |
| AdvSIMD vector | 266 | 255 | 145 | 145 | 126,007 |
| AdvSIMD loads and stores | 52 | 52 | 33 | 33 | 6,945 |
| reserved (UDF) | 1 | 1 | 1 | 1 | 142,486 |
| **total** | **765** | **733** | **423** | **423** | |

**NEON:** all 210 AdvSIMD and crypto encodings the game uses (196 + 14) have semantics, and 430 of the 445 the decoder
knows. Not implemented (none used): SDOT/UDOT (dotprod), SQRDMLAH/SQRDMLSH (rdm), FCMLA/FCADD (complxnum). Elsewhere:
FJCVTZS, LDRAA, PACGA, RMIF, SETF, AXFLAG/XAFLAG, MSR (immediate, PSTATE), SYSL, the 128-bit system instructions and the
LLVM-parity extras: none occurs.

## 5. Timings

Measured on the i9-13900KF, 4 jobs, idle priority (`partitions.tsv`). CPU time is per job; the four jobs kept four cores busy
throughout (2,433 CPU-seconds in 612 s of wall time).

| module | lift s | optimise s | codegen s | CPU s | µs / instruction | objects MB | bytes / instruction | slowest partition s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| main | 302 | 877 | 870 | 2,049 | 201 | 342.5 | 35 | 37.5 |
| sdk | 67 | 146 | 141 | 354 | 178 | 54.2 | 29 | 22.5 |
| subsdk0 | 5 | 13 | 12 | 29 | 165 | 4.6 | 27 | 20.0 |
| **total** | **374** | **1,035** | **1,024** | **2,433** | **197** | **401.2** | **34** | **37.5** |

- **Full O2 build: 10.2 minutes wall at 4 jobs (40.5 CPU-minutes)**, within research/03's estimate of 30–60 P-core-minutes.
  Lifting (IR, semantics linking and inlining, verification) is 15 %, optimisation 43 %, code generation 42 %.
- **O1** on a 4,000-function sdk sample (254,075 instructions): 35.6 CPU-seconds against 38.5 at O2, so only about 8 % less:
  code generation dominates and O1 barely shortens it. A full O1 build would take about 9.5 minutes at 4 jobs.
- The slowest partitions are main's dense ones (1,000 functions, 150,000–175,000 instructions, 26–38 s). The 68 functions
  over 16 KB get a partition each; the slowest took 14 s (17,120 instructions, 11.5 s of it in code generation).
- The coverage run predates two renames made afterwards to match nxrt (trap codes, `adapter_` names); the IR is otherwise
  the same, and the tests above ran after them.
- Objects: 401 MB, 34 bytes per guest instruction (research/03 expected about 20 for synthetic code; the difference is
  register traffic at calls, section 8). The 30 test functions with their callees (2,360 instructions) lift, optimise and JIT
  in under a second.
- The test harness: phase 1 (30 functions × 3,000 inputs × 2 runs + references) 4 s, phase 2 (49,752 single-op runs) 6 s,
  lifting and JIT of everything (50 functions + 6,219 op words) 5 s; 15 s in all.

## 6. Results summary

| check | scope | result |
|---|---|---|
| lifted vs interpreted, real functions | 30 functions (25 sdk, 5 main), 50 with callees, 3,000 inputs each | **0 differences** in registers or memory |
| vs host reference | 25 sdk functions × 3,000 inputs | **0 mismatches** (exact; sin/cos/exp ≤ 1 ulp, log 0 ulp) |
| lifted vs interpreted, each op alone | 410 ops, 6,219 real words, 49,752 random states | **0 differences** |
| known answers | 297 checks (estimates, NaN rules, conversions, flags, SIMD, CRC, AES, SHA-1, SHA-256, atomics) | **0 failures** |
| coverage | 200,414 functions, 12,360,967 instructions | **all lifted and compiled; 0 without semantics** |
| full build | O2, 4 jobs, idle priority | **612 s wall, 401 MB of objects** |
| ctest | 15 tests of the whole tree (with `NXW_DUMP`) | all pass |

## 7. Toolchain notes

- **LLVM's official windows-msvc build uses the static CRT (/MT) and no RTTI.** Everything linked with it must be /MT, so
  `src/nxlift/CMakeLists.txt` compiles /MT twins of nxw_common, nxw_a64, nxload, nxw_sem, program.db's reader and the
  interpreter; the rest of the tree keeps /MD. The lifter does not subclass LLVM types (no RTTI needed).
- **zlib and zstd:** LLVMSupport references them but the release does not ship them, and CMake would find MSYS2's MinGW copies.
  `llvm_stubs.c` stands in (compression reports "unavailable"; CRC-32 is real); `ZLIB::ZLIB` and `zstd::libzstd_static` are
  defined before `find_package(LLVM)`.
- **The MSVC STL:** LLVM 23.1.4's libraries were compiled with a newer STL than the PC's Visual Studio 2022 Build Tools 14.38
  links: 15 vectorized-algorithm helpers (`__std_mismatch_*`, `__std_search_1`, ...) and `_Thrd_sleep_for` were missing.
  `msvc_stl_shims.cpp` implements them from the STL's own declarations. **Updating the Build Tools to 17.14 or later makes the
  file unnecessary**, and is the clean fix.
- Builds of LLVM-linked targets must be Release (`_ITERATOR_DEBUG_LEVEL` 0, like LLVM's).

## 8. What v1 needs

**Exact FP still missing:** FPSR cumulative flags and QC; FPCR modes other than 0 (round modes, FZ, DN, AH): the semantics
assume FPCR = 0, which the Mac measured in play (DESIGN §8.1), and `msr fpcr` reaches `rt_msr` only; Arm NaN rules for
ordinary arithmetic only in the accurate-NaN build (to be measured against the Mac oracle); FP16 arithmetic is not decoded.

**NEON:** nothing missing for this game. The lane-wise loops vectorise at O2 but some families (TBL with several registers,
widening multiplies, structure loads) are scalar sequences; worth measuring once the game runs.

**Control flow:** C++ exceptions (landing pads are lifted but unreachable; D5's `invoke` + `GuestNonLocal` bridge), setjmp
resume points, the four sdk context functions as host code (D5), debug-build return checks (03 §3.2), safepoints (D9).

**Runtime integration** (nxrt's `nxrt/rt_abi.h` and `nxw/rt.h` agree on names, numbering and trap codes 1–6):
- nxrt must export **`rt_host_base`** (the game DLL's base): table entries are offsets from it, and the interpreter, which
  lives in nxrt.exe, cannot use the DLL's own `__ImageBase`;
- trap codes 7–9 (unsupported op, ERET, system instruction) and the detail in bits 8 and up are new to nxrt;
- spin-loop heads, YIELD and WFE are an inline `pause` today; nxrt offers `rt_yield(cpu, kind, site)` (D9 back-off and
  safepoints), a small lifter change (store the state, call, reload, as for SVC);
- the game DLL link (lld-link, imports from nxrt.exe, the `nxw_game_functions` export list nxrt proposes) and the object cache
  keyed by content hash (03 §3.8);
- a rule for host-bound symbols reached only through data (vtables, GOT slots), CodeView line info per guest instruction
  (03 §3.9.6).

**Performance ideas** (each to be gated by nxlift-tests and the Mac swap-in tests):
- Calls: `enter` reloads every register the function later reads after each call. Trusting AAPCS64 for x19–x29, SP and d8–d15
  across guest-to-guest calls (03 §5.3) removes most of that; `preserve_none` for lifted functions.
- The leave masks could also be per exit (a register written only on some paths is stored on all of them today).
- Jump tables: switch on the table index rather than the loaded target (one lookup instead of two).
- BLR: profile-guided devirtualisation of hot vtable calls; an inline cache.
- Relax `volatile` for SP-relative accesses (thread-private, 03 §3.6).
- The interpreter decodes each word every time; a per-function pre-decoded cache would make misses cheaper.
- Compile time: optimisation and code generation are about 43 % each; the partition cache; finer partitions for main's
  densest ranges so the four jobs stay balanced at the end of a build.
