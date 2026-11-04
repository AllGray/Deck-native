# 04 — Binary census of Pokopia 2.0.1 (main, sdk, subsdk0, rtld)

What the game's AArch64 code actually contains, measured over every word of every text segment, to scope a static recompiler (AArch64 → LLVM → Windows x86-64). Every number below comes from the scripts in `research/census/` (see **Method**); none is estimated.

Build: Pokopia 2.0.1 ExeFS (`installer-test-win/build201/extract/ExeFS`). Module build IDs: main `76a489f3bd3642fa…`, sdk `cc7da00d672399ef…`, subsdk0 `9646a69cb1024ba9…`, rtld `bcf5e512e7b0df65…`. SDK 20.5.25 (`NintendoSdk_nnSdk-Generic_20_5_25`).

## Key numbers

| | main | sdk | subsdk0 | rtld |
|---|---:|---:|---:|---:|
| text | 39.29 MiB | 7.72 MiB | 0.69 MiB | 9.6 KiB |
| instructions decoded | 10,298,654 | 2,023,162 | 181,206 | 2,464 |
| undecodable words | 2 | 2 | 2 | 0 |
| functions (FDEs) | 147,875 | 38,283 | 1,909 | 34 |
| text covered by FDEs | 98.41% | 96.05% | 96.93% | 96.23% |
| BLR / of which vtable-style | 136,621 / 130,489 | 15,723 / 14,050 | 1,341 / 1,001 | 10 / 1 |
| BR (jump tables / PLT / other) | 12,944 (6,346 / 886 / 5,712) | 12,644 (697 / 9,684 / 2,263) | 952 (89 / 822 / 41) | 7 |
| SVC sites | 0 | 57 | 0 | 7 |
| MRS/MSR sites | 145 | 509 | 0 | 0 |
| LSE atomics / LL-SC exclusives | 29,708 / 0 | 1,928 / 5 | 154 / 0 | 0 / 0 |

## Method

**Inputs, outputs, rules.** The four NSOs and `main.npdm` are read from the ExeFS folder and never modified. Anything derived from game code (the JSON counters, a temporary ELF wrapper for `llvm-objdump`) is written only to `windows-native/_local/census/`; the wrapper is deleted after each run. Everything runs as one Python process at a time, single-threaded. The whole census takes about 50 s (`main`'s instruction sweep is 21 s).

**Re-running.** `py -3 -I research/census/run_all.py` runs every step in order and regenerates `_local/census/tables.md`, the full tables this document condenses. The steps are:

| script | does |
|---|---|
| `nsofmt.py` | NSO0 loader (LZ4, segment SHA-256 checked against the header), MOD0, `.dynamic`, dynamic symbols, RELA/JMPREL/RELR relocations, `.eh_frame_hdr` and a full `.eh_frame` walk (CIEs, FDEs, LSDA pointers). Follows nxnative's `nso.py`, `nso_code.py` and `nso_syms.py`, kept self-contained. |
| `layout.py` | segments, MOD0, dynamic tags, symbol counts, cross-module import resolution, relocation targets, FDE statistics, `main.npdm` SVC mask; demangles with LLVM's `llvm-cxxfilt` |
| `insns.py` | linear sweep of the whole text segment with capstone 5.0.7; instruction keys; indirect-branch, jump-table, tail-call, TLS and system-register analysis |
| `diag.py` | code without FDEs, UDF placement, relocation targets that are not function starts |
| `xcheck_llvm.py` | independent decode of the same text with LLVM 23's `llvm-objdump -d -z` and comparison of per-mnemonic counts |
| `crosscheck.py` | address-by-address comparison with nxnative's rewriter tables (`build201/patched/*.tsv`) |
| `summarize.py` | classification and the markdown tables |

**Sweep.** `disasm_lite` decodes the text in 16 KB chunks. At a word it cannot decode, the sweep records the word and restarts 4 bytes later, so every word of text is counted exactly once: decoded words plus undecodable words equal text size / 4 for every module.

**Classification.** Each instruction is keyed by its mnemonic, its encoding group (bits 28:25 of the ARM ARM top-level decode, plus bit 26 for SIMD&FP loads/stores and bits 31:28 to split AdvSIMD vector, AdvSIMD scalar, scalar FP and the crypto space), and for SIMD&FP its register shape (for example `v.4s,v.s`). "Scalar FP" means the scalar floating-point encoding group, and "AdvSIMD" means the vector and AdvSIMD-scalar groups. "FP16 arithmetic" means f-mnemonics on `h` registers or `.4h`/`.8h` lanes, excluding the v8.0 conversions (`fcvt`, `fcvtn`, `fcvtl`).

**Indirect branches.** For each BR/BLR the analysis walks back through the same function's instructions (at most 48, within the FDE range) to the last writer of the target register. It follows `mov` copies and stops at an unconditional branch, a return, or (for x0–x18) a call.
- **Vtable-style** means `ldr xA, [xObj, #o]; ldr xT, [xA, #slot]; blr xT` with the object pointer not built from `adrp`. Calls through any pointer-to-table-of-function-pointers count here too, not just C++ vtables.
- **Jump table** means BR of `anchor + (entry << k)`, where `entry` is a register-indexed load.
- **PLT stub** means `adrp x16; ldr x17, [x16]; add x16; br x17`.

Jump tables are checked, not just pattern-matched. The table base and anchor are resolved from `adr`/`adrp+add`, and the bound from the `cmp #k; b.hi/b.hs` guard or an `and #2^k−1`/`lsr`/`ubfx` on the index. Every entry is then decoded, and its target must land inside the function that holds the BR.

**Cross-checks.**
1. LLVM 23 and capstone 5 decode the same words in all four modules, flag the same undecodable words, and agree on every mnemonic count except 2–4 alias choices (`orr`↔`mov`, `bfi`↔`bfc`).
2. The census finds exactly the same addresses as nxnative's rewriter for every kind of site the rewriter handles (see *Cross-check with the rewriter*).
3. The FDE count from the `.eh_frame_hdr` table equals that from walking `.eh_frame`, start for start.

**Limits.**
- Data-flow classification is local (one function, 48 instructions back). Targets defined in an earlier basic block show as "no def in window" or "xB defined earlier".
- 680 of main's 6,172 relative jump tables have a bound or base the heuristic cannot pin down. Of the 5,492 decoded, 2 have entries outside their function. Both were inspected: the index register is not the one the guard checks, so they are heuristic misses, not real out-of-function targets.
- LSDA landing pads were not enumerated.

## 1. Layout

| module | file bytes | text | rodata | data | bss | compression |
|---|---:|---:|---:|---:|---:|---:|
| main | 29,405,084 | 0x2749480 (39.29 MiB) | 0xadfdf4 (10.87 MiB) | 0x2a1ec0 (2.63 MiB) | 0x854140 (8.33 MiB) | LZ4 ×3, hashes checked |
| sdk | 7,327,611 | 0x7b7bf0 (7.72 MiB) | 0x5f0864 (5.94 MiB) | 0x5f870 (0.37 MiB) | 0x1e7790 (1.90 MiB) | LZ4 ×3 |
| subsdk0 | 750,812 | 0xb0f60 (0.69 MiB) | 0x69664 (0.41 MiB) | 0x15010 (0.08 MiB) | 0xfff0 (0.06 MiB) | LZ4 ×3 |
| rtld | 10,477 | 0x2680 | 0xa84 | 0xc008 | 0xfff8 | LZ4 ×3 |

| module | MOD0 at | .dynamic | bss (MOD0) | .eh_frame_hdr | module object | dyn tags | notable tags |
|---|---:|---:|---:|---:|---:|---:|---|
| main | 0x274a074 | 0x3481968 | 0x34cc000..0x3d14b00 | 0x2ceaba8..0x2e0b8cc | 0x34cc008 | 26 | NEEDED ×2 (`multimedia_compat_nx.nss`, `nnSdk.nss`), RELR, HASH + GNU_HASH, FLAGS_1 = PIE, DEBUG, INIT/FINI, INIT_ARRAY (0x8c0 B = 280 constructors) |
| sdk | 0x7b801c | 0xde11a8 | 0xe09000..0xfe6128 | 0xc4fbf4..0xc9a858 | 0xe09008 | 24 | SONAME `nnSdk.nss`, RELR, GNU_HASH, INIT_ARRAY (91) |
| subsdk0 | 0xb1028 | 0x11de00 | 0x130010..0x1301c8 | 0x107f6c..0x10bb20 | 0x130018 | 23 | SONAME `multimedia_compat_nx.nss`, RELACOUNT, HASH + GNU_HASH |
| rtld | 0x301c | 0x4008 | 0x10010..0x10118 | 0x351c..0x3638 | 0x10048 | 24 | RELR |

| module | dynsym entries | imports | import types | exports (global+weak) | export types | weak exports | PLT entries |
|---|---:|---:|---|---:|---|---:|---:|
| main | 1,052 | 947 | func 918, object 27, notype 2 | 104 | object 87, func 16, notype 1 | 54 | 885 |
| sdk | 27,913 | 8 | notype 8 | 27,904 | func 23,136, object 4,766, notype 2 | 4,632 | 9,683 |
| subsdk0 | 1,682 | 357 | notype 357 | 1,324 | func 1,098, object 225, notype 1 | 210 | 821 |
| rtld | 3 | 2 | func 2 | 0 | – | 0 | 1 |

| module | RELA | JMPREL | RELR (packed relative) | relative-reloc targets by segment | distinct text targets | … at an FDE start | relocs bound to imports |
|---|---|---:|---:|---|---:|---:|---|
| main | ABS64 1,732, GLOB_DAT 28 | JUMP_SLOT 885 | **285,495** | text 246,274, data 16,369, ro 15,069, bss 7,781, other 2 | 92,327 | 92,241 | ABS64 1,732, GLOB_DAT 28, JUMP_SLOT 885 |
| sdk | ABS64 9,818, GLOB_DAT 1,449 | JUMP_SLOT 9,683 | 15,662 | text 12,696, ro 1,824, data 1,063, bss 77, other 2 | 10,323 | 10,320 | GLOB_DAT 2, JUMP_SLOT 8 |
| subsdk0 | RELATIVE 689, ABS64 610, GLOB_DAT 104 | JUMP_SLOT 821 | 0 | ro 363, text 275, data 48, bss 3 | 218 | 215 | ABS64 173, GLOB_DAT 35, JUMP_SLOT 319 |
| rtld | GLOB_DAT 1 | JUMP_SLOT 1 | 39 | data 17, text 13, ro 6, other 3 | 8 | 6 | GLOB_DAT 1, JUMP_SLOT 1 |

No TLS relocation types (TPREL, DTPMOD, TLSDESC) or IRELATIVE appear in any module. sdk calls its own exports through its PLT (9,683 JUMP_SLOTs for 8 imports), because its exported symbols are preemptible.

### Imports between modules

Resolved over the load order main → subsdk0 → sdk:

| importer | imports | from main | from subsdk0 | from sdk | unresolved |
|---|---:|---:|---:|---:|---|
| main | 947 | – | 20 | 925 | 2 weak `_ZTH…` thread_local wrappers of gRPC (`grpc_core::ExecCtx::exec_ctx_`, `ApplicationCallbackExecCtx::callback_exec_ctx_`) |
| sdk | 8 | 8 (`malloc`, `calloc`, `realloc`, `aligned_alloc`, `free`, the entry points `nnMain` and `nninitStartup`, `__nnDetailNintendoSdkRuntimeObjectFile`) | – | – | – |
| subsdk0 | 357 | 40 (33 `pfnc_nvn*` proc pointers, 4 allocator functions, `nvnLoadCProcs`, `__cxa_pure_virtual`, `__nnDetailNintendoSdkRuntimeObjectFile`) | – | 317 | – |
| rtld | 2 | – | – | 2 (`nn::init::Start`, `nn::os::detail::UserExceptionHandler`) | – |

Symbols defined in two modules: main & sdk 62, sdk & subsdk0 10, main & subsdk0 6. **main supplies the allocator to the SDK** (sdk imports `malloc`/`free`/… from main).

**What main imports from sdk (925), by namespace** (demangled with `llvm-cxxfilt`):
- 587 `nn::` C++ functions in 34 namespaces
- 83 Nintendo C-API functions
- 85 libc++ symbols
- 148 libc/libm symbols
- 19 C++ ABI symbols
- 2 `nv::` and 1 NVN symbol

| namespace / group | n | example |
|---|---:|---|
| nn::os | 89 | `nn::os::LockMutex(nn::os::MutexType*)` |
| std:: (libc++) | 85 | `std::__1::__next_prime(unsigned long)` |
| nn::hid | 63 | `nn::hid::InitializeNmouse(void*, unsigned long, int, int)` |
| C: nnsocket*/nnssl*/inet | 57 | `nnsocketClose`, `nnsslConnectionRead` |
| nn::async | 53 | `nn::async::detail::FutureStateBase::FutureStateBase()` |
| C: string/memory/ctype | 43 | `memset`, `memcpy` |
| nn::socket | 40 | `nn::socket::EventFd(unsigned long, nn::socket::EventFdFlags)` |
| nn::fs | 35 | `nn::fs::CreateDirectory(char const*)` |
| C: libm | 30 | `cosf`, `acosf` |
| C: stdio/files | 30 | `snprintf`, `fopen` |
| nn::time | 26 | `nn::time::Initialize()` |
| nn::oe | 22 | `nn::oe::SetPerformanceModeChangedNotificationEnabled(bool)` |
| nn::audio | 22 | `nn::audio::OpenDefaultAudioOut(…)` |
| C: nnos* (nn::os C API) | 21 | `nnosGetSystemTick`, `nnosGetSystemTickFrequency` |
| nn::codec | 21 | `nn::codec::DecodeAdpcm(…)` |
| nn::bcat | 19 | `nn::bcat::MountDeliveryCacheStorage()` |
| nn::account | 18 | `nn::account::EnsureNetworkServiceAccountAvailable(…)` |
| nn::streamplay | 18 | `nn::streamplay::GetGuestPadStates(…)` |
| C: pthread | 18 | `pthread_mutex_init` |
| nn::ldn | 17 | `nn::ldn::Initialize()` |
| nn::friends | 16 | `nn::friends::GetProfileList(…)` |
| nn::image | 14 | `nn::image::JpegEncoder::SetPixelData(…)` |
| nn::prepo | 13 | `nn::prepo::PlayReport::PlayReport(char const*)` |
| nn::ssl / nn::err | 12 / 12 | `nn::ssl::Initialize()`, `nn::err::ApplicationErrorArg::…` |
| C: other libc | 12 | `qsort`, `getenv`, `getentropy` |
| C++ ABI / unwind | 11 | `__cxa_throw`, `__cxa_begin_catch`, `_Unwind_Resume`, `__gxx_personality_v0`, `__cxa_guard_*` |
| C: time | 11 | `clock_gettime`, `localtime_r` |
| nn::util / nn::vi / nn::swkbd | 10 / 10 / 10 | `nn::vi::GetNativeWindow(…)` |
| nn::mem / nn::nifm / nn::crypto | 8 / 8 / 8 | `nn::mem::StandardAllocator::Allocate(…)`, `nn::crypto::detail::Sha1Impl::Update(…)` |
| nn::ngc | 6 | `nn::ngc::ProfanityFilter::…` |
| operator new/delete | 5 | |
| C: nnResult*/nndetail* | 5 | |
| nn::album / nn::lmem / `__cxxabiv1` vtables | 3 / 3 / 3 | |
| nv:: / nn::aoc / nn::diag / nn::pctl / nn::nsd / **setjmp+longjmp** / nnmusl runtime | 2 each | `nv::InitializeGraphics(void*, unsigned long)` |
| nn::ro / nn::ec / nn::detail / **NVN** | 1 each | **`nvnBootstrapLoader` is the only NVN import** |

The largest second-level groups:
- `nn::async::detail` 26
- `nn::hid::VibrationPlayer` 9
- `nn::prepo::PlayReport` 9
- `nn::mem::StandardAllocator` 8
- `nn::crypto::detail` 8
- `nn::ssl::Context` 7
- `nn::image::JpegEncoder`/`JpegDecoder` 7 each

The other import edges:
- **main from subsdk0:** 20 `movie::` functions (the movie player).
- **subsdk0 from sdk:** `nn::codec` 87, `nn::movie` 79, `nn::os` 73, libc 18, `nn::mem` 11, 8 C++-mangled `nvn*` helpers, `nn::syncpt` 5, and smaller groups.

**Middleware**, from each module's API-info list:
- **main:** NintendoSDK gfx, gRPC, OpenSSL, NPLN 1.41.4, Google APIs, libcurl, libz, WebRTC
- **subsdk0:** movie
- **sdk:** nnSdk 20.5.25, NVN, libz

**main.npdm** (ACI0 and ACID agree) allows these SVCs: 0x01–0x29, 0x2C, 0x2D, 0x32–0x36 and 0x80. It lists 46 services.

## 2. Functions (.eh_frame)

| module | .eh_frame_hdr / .eh_frame | FDEs | hdr table = full walk | CIEs | FDEs per CIE augmentation | FDEs with LSDA | text covered | uncovered bytes = padding + PLT + real code |
|---|---|---:|---|---:|---|---:|---:|---|
| main | yes / yes | 147,875 | yes | 3 | zRB 79,966, zR 64,521, zPLRB 3,388 | **3,388** | 98.41% | 653,228 = 399,676 + 14,192 + **239,360** |
| sdk | yes / yes | 38,283 | yes | 3 | zRB 20,238, zR 17,471, zPLRB 574 | 574 | 96.05% | 319,428 = 159,260 + 154,972 + 5,196 |
| subsdk0 | yes / yes | 1,909 | yes | 2 | zRB 1,276, zR 633 | 0 | 96.93% | 22,288 = 9,108 + 13,180 + 0 |
| rtld | yes / yes | 34 | yes | 2 | zR 19, zRB 15 | 0 | 96.23% | 372 = 320 + 52 + 0 |

The FDE tables are clean: no FDE has zero size or starts outside text, no start is duplicated, and no ranges overlap.

The `B` in `zRB`/`zPLRB` marks frames that sign the return address with the **B key**. The `zPLRB` CIE carries the C++ personality through an indirect pointer (`__gxx_personality_v0`).

The padding between functions is `udf #0` (zero words). In main it fills 28,671 gaps of at most 64 bytes, in front of functions aligned to 16, 32 or 64 bytes.

Function starts in main by alignment:

| alignment | FDE starts |
|---|---:|
| 4 bytes only | 59,097 (40%) |
| 8 | 27,948 |
| 16 | 18,821 |
| 32 | 20,763 |
| 64 | 21,246 |

| module | functions | mean B | p10 | p25 | median | p75 | p90 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| main | 147,875 | 274 | 8 | 12 | 84 | 268 | 648 | 2,720 | 68,480 |
| sdk | 38,283 | 203 | 8 | 16 | 76 | 208 | 460 | 2,072 | 21,168 |
| subsdk0 | 1,909 | 368 | 8 | 40 | 116 | 348 | 824 | 4,808 | 11,676 |
| rtld | 34 | 279 | 8 | 12 | 124 | 356 | 472 | 3,868 | 3,868 |

| function size | main | sdk | subsdk0 |
|---|---:|---:|---:|
| ≤16 B | 43,229 (29.2%) | 11,707 (30.6%) | 365 (19.1%) |
| 17–64 B | 25,101 (17.0%) | 6,386 (16.7%) | 341 (17.9%) |
| 65–256 B | 41,321 (27.9%) | 12,296 (32.1%) | 589 (30.9%) |
| 257–1024 B | 30,292 (20.5%) | 6,765 (17.7%) | 478 (25.0%) |
| 1–4 KiB | 7,253 (4.9%) | 1,020 (2.7%) | 112 (5.9%) |
| 4–16 KiB | 621 (0.42%) | 105 (0.27%) | 24 (1.3%) |
| >16 KiB | 58 | 4 | 0 |

**Code without FDEs.**
- **main:** one region at main+0x172f438, 239,272 bytes. It holds 59,818 instructions, 1,076 returns and 622 `pacibsp` prologues, which is **0.58% of main's text**. The strings it references (HTTP headers, proxy and cookie messages) identify it as **libcurl**, built without unwind tables. 780 distinct BL targets and 86 relocation targets fall inside it.
- **sdk:** 9 small regions totalling 5,196 bytes. The largest is 2,924 bytes of small assembly helpers.
- **subsdk0 and rtld:** none.

All other uncovered bytes are padding or PLT.

| module | BL → FDE start | BL → PLT | BL → code without FDE | BL → middle of an FDE | distinct BL targets | FDE starts never BL/B-targeted | address-taken FDE starts | FDE starts with no reference found |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| main | 367,391 | 56,793 | 3,590 | **0** | 56,371 | 88,239 | 92,742 | 42 |
| sdk | 17,450 | 77,343 | 10 | 0 | 12,013 | 32,917 | 33,164 | 5 |
| subsdk0 | 3,099 | 4,269 | 0 | 0 | 1,431 | 1,227 | 1,227 | 0 |
| rtld | 84 | 1 | 0 | 0 | 24 | 10 | 8 | 2 |

"Address-taken" counts FDE starts that a relocation, an `adr`/`adrp+add` in code, or an export points at. **60% of main's functions (88,239) are never the target of a direct call or tail call.** They are reached only through pointers, mostly vtables (246,274 relocations point into text). Together, direct references and address-taken references account for every main function but 42.

## 3. Instructions

**Data in code.** The only undecodable words are the two NSO header words at text+4 and text+8 (the MOD0 offset and the next header word) of main, sdk and subsdk0. In rtld, the same two words happen to decode as `udf`.

There are **no literal pools**: zero PC-relative `ldr` literals. All jump tables live in rodata, and no other data sits in text.

| class | main | sdk | subsdk0 | rtld |
|---|---:|---:|---:|---:|
| Integer ALU | 3,884,760 (37.72%) | 867,852 (42.90%) | 85,824 (47.36%) | 995 (40.38%) |
| Load/store | 3,627,089 (35.22%) | 624,150 (30.85%) | 54,022 (29.81%) | 739 (29.99%) |
| Branch | 1,894,412 (18.39%) | 388,384 (19.20%) | 33,192 (18.32%) | 536 (21.75%) |
| Scalar FP | 395,171 (3.84%) | 22,847 (1.13%) | 45 (0.02%) | 0 |
| AdvSIMD (NEON) | 134,614 (1.31%) | 21,462 (1.06%) | 0 | 0 |
| FP16 arithmetic (v8.2) | **0** | **0** | 0 | 0 |
| Dot product / i8mm / bf16 | **0** | **0** | 0 | 0 |
| Crypto (AES/SHA/PMULL) | 0 | 1,542 (0.08%) | 0 | 0 |
| CRC32 | 0 | 48 | 0 | 0 |
| Exclusive (LDXR/STXR/CLREX) | **0** | 5 | 0 | 0 |
| LSE atomic | 29,708 (0.29%) | 1,928 (0.10%) | 154 (0.08%) | 0 |
| Load-acquire / store-release (incl. LDAPR) | 4,877 (0.05%) | 3,994 (0.20%) | 219 (0.12%) | 0 |
| Barrier | 2,468 | 312 | 0 | 0 |
| System register (MRS/MSR) | 145 | 509 | 0 | 0 |
| Cache maintenance (DC/IC) | 0 | 2 | 0 | 0 |
| Exception (SVC/BRK) | 0 | 57 | 0 | 7 |
| Hint (NOP / PAC / BTI / WFE…) | 225,142 (2.19%) | 50,131 (2.48%) | 5,465 (3.02%) | 103 (4.18%) |
| PAC data processing (PACIA/XPACI…) | 0 | 5 | 0 | 0 |
| PAC loads/branches (LDRAA, BRAA, BLRAA…) | **0** | **0** | 0 | 0 |
| MTE | 0 | 1 (`stg`) | 0 | 0 |
| SVE / SME | **0** | **0** | 0 | 0 |
| UDF (padding / trap) | 100,268 (0.97%) | 39,933 (1.97%) | 2,285 (1.26%) | 84 (3.41%) |
| **decoded total** | **10,298,654** | **2,023,162** | **181,206** | **2,464** |
| undecodable words | 2 | 2 | 2 | 0 |
| text words | 10,298,656 | 2,023,164 | 181,208 | 2,464 |

### Branches

| branch | main | sdk | subsdk0 | rtld |
|---|---:|---:|---:|---:|
| B | 268,269 | 59,228 | 5,322 | 122 |
| B.cond | 398,309 | 83,305 | 9,569 | 167 |
| CBZ/CBNZ | 384,126 | 66,878 | 5,110 | 82 |
| TBZ/TBNZ | 120,484 | 19,008 | 1,050 | 21 |
| BL | 427,774 | 94,803 | 7,368 | 85 |
| BR | 12,944 | 12,644 | 952 | 7 |
| BLR | 136,621 | 15,723 | 1,341 | 10 |
| RET | 66,666 | 14,164 | 1,143 | 30 |
| RETAB | **79,219** | 22,631 | 1,337 | 12 |
| RETAA, BRAA/BRAB(Z), BLRAA/BLRAB(Z), ERET | 0 | 0 | 0 | 0 |

### Loads and stores

| load/store | main | sdk | subsdk0 | rtld |
|---|---:|---:|---:|---:|
| GPR single load | 1,678,979 | 266,181 | 26,831 | 362 |
| GPR single store | 909,524 | 146,985 | 13,306 | 126 |
| GPR pair load (LDP/LDNP/LDPSW) | 345,289 | 87,301 | 6,648 | 99 |
| GPR pair store (STP/STNP) | 354,284 | 95,986 | 7,208 | 144 |
| SIMD&FP single load | 147,678 | 10,764 | 6 | 0 |
| SIMD&FP single store | 111,424 | 7,480 | 3 | 0 |
| SIMD&FP pair load | 44,233 | 2,912 | 0 | 4 |
| SIMD&FP pair store | 32,333 | 2,825 | 20 | 4 |
| NEON structure LD1–4/ST1–4 | 3,345 | 3,600 | 0 | 0 |
| PRFM | 0 | 116 | 0 | 0 |
| addressing: base + immediate | 3,395,796 | 565,433 | 46,807 | 643 |
| addressing: register offset (incl. extend/shift) | 97,276 | 23,407 | 4,262 | 27 |
| addressing: pre-index writeback | 77,084 | 15,923 | 1,269 | 31 |
| addressing: post-index writeback | 91,518 | 25,315 | 2,057 | 38 |
| PC-relative literal loads | 0 | 0 | 0 | 0 |

### AdvSIMD: top 40 mnemonics (all modules)

main uses 88 distinct AdvSIMD mnemonics, sdk 127, subsdk0 none. Shapes are the register signature (`s,v.s` means a scalar-by-element form).

| mnemonic | total | main | sdk | commonest shapes |
|---|---:|---:|---:|---|
| mov (INS/DUP element, ORR alias) | 37,120 | 34,633 | 2,487 | v.s 16,135; s,v.s 9,830; v.d 5,006 |
| fmul | 22,987 | 22,319 | 668 | v.4s 12,019; v.4s,v.s 6,542; s,v.s 4,023 |
| fmla | 20,191 | 19,212 | 979 | v.4s,v.s 9,279; s,v.s 8,639; v.4s 2,265 |
| movi | 20,181 | 18,810 | 1,371 | d 13,127; v.2d 5,320; v.2s 1,130 |
| fadd | 6,755 | 6,091 | 664 | v.4s 5,680; v.2s 1,048 |
| fsub | 5,687 | 5,096 | 591 | v.4s 5,489; v.2s 198 |
| scvtf | 4,975 | 3,842 | 1,133 | s 4,244; v.4s 688 |
| dup | 3,437 | 2,884 | 553 | v.2s,v.s 1,456; v.4s,v.s 831; v.4s,w 759 |
| tbl | 2,885 | 2,704 | 181 | v.8b,v.16b 2,630; v.16b 255 |
| ucvtf | 2,092 | 2,009 | 83 | s 2,007; d 85 |
| ext | 2,068 | 1,125 | 943 | v.16b 1,851; v.8b 217 |
| zip2 | 1,964 | 1,346 | 618 | v.4s 1,635 |
| zip1 | 1,591 | 1,128 | 463 | v.4s 1,310 |
| add | 1,584 | 329 | 1,255 | v.4s 1,159; v.2d 197 |
| fmls | 1,547 | 1,411 | 136 | v.4s 937; s,v.s 479 |
| bic | 1,491 | 1,443 | 48 | v.16b 1,440 |
| fcvtzs | 1,166 | 323 | 843 | v.4s 787; s 340 |
| frsqrts | 1,132 | 1,130 | 2 | v.4s |
| fmov (immediate) | 1,115 | 1,091 | 24 | v.4s 916; v.2s 196 |
| faddp | 1,067 | 1,046 | 21 | v.2s 1,065 |
| rev64 | 1,055 | 830 | 225 | v.2s 979 |
| fcmeq | 796 | 795 | 1 | v.4s |
| sub | 763 | 73 | 690 | v.4s 600; v.4h 132 |
| sqrdmulh | 590 | 0 | 590 | v.4s 386; v.4s,v.s 204 |
| frsqrte | 566 | 565 | 1 | v.4s |
| orr | 520 | 245 | 275 | v.16b 453 |
| sqadd | 477 | 0 | 477 | v.4s 330; v.4h 60 |
| frecps | 456 | 438 | 18 | v.4s 446 |
| sqxtn | 453 | 0 | 453 | v.4h,v.4s 435 |
| sshll | 427 | 16 | 411 | v.4s,v.4h 402 |
| smull | 427 | 0 | 427 | v.4s,v.4h,v.h 210 |
| uzp1 | 423 | 56 | 367 | v.4s 315 |
| sqdmulh | 409 | 0 | 409 | v.4s 141; v.4s,v.s 139; v.4h 112 |
| eor | 399 | 286 | 113 | v.16b 338 |
| cmeq | 398 | 236 | 162 | v.16b 267; v.4s 98 |
| uzp2 | 338 | 12 | 326 | v.4s 240 |
| fneg | 325 | 176 | 149 | v.4s 309 |
| and | 322 | 219 | 103 | v.16b 266 |
| fmax | 247 | 231 | 16 | v.4s 238 |
| fabd | 245 | 234 | 11 | s 243 |

main's SIMD is almost entirely 4×f32 vector maths plus lane shuffles. The fixed-point and saturating integer work (`sqrdmulh`, `sqadd`, `sqxtn`, `smull`) is in sdk, where the audio and codec code lives.

**Scalar FP, top 20** (all modules; main is overwhelmingly single precision, e.g. `fmadd s` 48,994 vs `fmadd d` 140):

`fmov` 87,983, `fmul` 64,611, `fcmp` 61,302, `fmadd` 51,651, `fadd` 34,806, `fsub` 24,943, `fdiv` 19,001, `fcsel` 18,674, `fnmul` 10,147, `scvtf` 7,917, `fcvtzs` 6,187, `fsqrt` 5,845, `fneg` 4,864, `fccmp` 4,087, `ucvtf` 4,005, `fmsub` 2,967, `fcvt` 2,038, `fabs` 1,474, `fcvtzu` 1,381, `fnmsub` 883.

### Feature-specific classes

- **FP16 arithmetic:** none. Half precision appears only as conversions: `fcvt` s↔h ×6 in sdk, vector `fcvtl`/`fcvtl2` ×26.
- **Dot product / matrix (SDOT, UDOT, USDOT, SMMLA, BFDOT…):** none.
- **Crypto:** sdk only, all inside `nn::crypto::detail` (AES CTR/XTS, SHA-1, SHA-256, GHASH). `sha1h` 160, `aese` 146, `aesimc` 138, `aesmc` 132, `sha1su0`/`sha1su1` 128 each, `aesd` 116, `sha256h`/`sha256h2` 112 each, `sha256su0`/`su1` 84 each, `sha1p` 80, `sha1c`/`sha1m` 40 each, `pmull`/`pmull2` (.1q) 23/19. main has none: its OpenSSL is built without the AArch64 crypto extensions.
- **CRC32:** sdk only. `crc32b` 27 and `crc32x` 8 (zlib `crc32_z`, nlib CRC32); `crc32cb` 12 and `crc32cx` 1 (nlib CRC32C).
- **Exclusives:** sdk only, `ldaxr` 3 and `stlxr` 2 (in `bindtextdomain`, `dcngettext` and one unexported function). No `ldxr`/`stxr`/`ldxp`/`clrex` anywhere. **main has no LL/SC at all.**
- **LSE atomics:**
  - main: `casal` 13,635, `ldaddal` 10,949, `ldadd` 3,992, `swpal` 1,023, `swpab` 64, `casl` 18, `casa` 11, `cas` 5, `casalb` 4, `casb` 3, `swp` 3, `ldaddl` 1.
  - sdk: `ldaddal` 499, `ldadd` 333, `casl` 312, `casal` 205, `swp` 160, `cas` 83, `casa` 82, `swpa` 71, `ldsetal` 66, `ldclrl` 38, `ldaddl` 31, plus 48 more across 13 other forms.
  - subsdk0: `ldaddal` 148, `ldclrl` 3, `swpalb` 2, `casa` 1.
- **Acquire/release:**
  - main: `ldaprb` 3,523, `ldapr` 1,013, `stlr` 152, `stlrb` 98, `ldarb` 59, `ldar` 32.
  - sdk: `ldaprb` 1,450, `ldapr` 1,117, `ldarb` 360, `stlrb` 332, `stlr` 314, `ldar` 236, `ldarh` 93, `stlrh` 92.
  - subsdk0: `ldaprb` 115, `stlr` 64, others 40.
  - `LDAPR` is the v8.3 RCpc load.
- **Barriers:**
  - main: `dmb ish` 2,453, `dmb ishld` 15.
  - sdk: `dmb ishld` 201, `dmb ish` 96, `dsb sy` 4, `isb` 3, `dmb ishst` 3, `dsb ish` 2, `dmb oshst` 1, `dsb st` 1, `dmb st` 1.
- **Hints:**
  - main: `nop` 109,060, `pacibsp` 83,986, `autibsp` 32,096. The NOPs come in 45,426 runs, and 95.9% of runs end on a 16-byte boundary, so most are branch-target alignment inside functions. Another 2,335 come from `adrp` relaxed by the linker to `nop; adr`, and ~290 from relaxed TLS sequences.
  - sdk: `nop` 24,522, `pacibsp` 20,847, `autibsp` 4,715, `xpaclri` 34, `yield` 8 (`nn::nlibsdk::heap::TlsHeapCentral` spin paths), `wfe` 1, `sevl` 1, `autiasp` 1, `autia1716` 1, `autib1716` 1.
  - subsdk0: `nop` 3,837, `pacibsp` 1,276, `autibsp` 352.
  - **No BTI.**
- **PAC data processing:** sdk only, `xpaci` 3 (`nn::diag::detail::Backtrace::Step`, `VAbortImpl`, `nn::ro::LookupNextSymbol`) and `pacia` 2 (unexported). No LDRAA/LDRAB.
- **MTE:** one `stg` (sdk+0x78d000, an unexported tag-store loop guarded by a compare).
- **SVE / SME / other:** none. Nothing decodes outside the classes above.
- **UDF placement:**
  - `udf #0`: main 99,916, sdk 39,825, subsdk0 2,277. Every one sits in an inter-function gap, so it is padding.
  - `udf #0x8002`: main 351, sdk 71, subsdk0 7. Always **inside** functions, after `b`, `ret`, `retab` or a `bl` to a no-return function, so it is the compiler's trap encoding (unreachable/abort).
  - sdk also has `#0x8005` ×34, `#0x8009` and `#0x800a` traps.
  - There is no BRK instruction in any module.

## 4. Indirect control flow

| BR class | main | sdk | subsdk0 | rtld |
|---|---:|---:|---:|---:|
| jump table (register-indexed load + add) | 6,172 | 696 | 89 | 3 |
| jump table / dispatch via absolute pointers (`ldr xT, [tbl, idx, lsl #3]`) | 174 | 1 | 0 | 0 |
| vtable-style tail call (`ldr vptr; ldr fn,[vptr,#slot]; br`) | 5,440 | 1,321 | 38 | 0 |
| PLT stub | 886 | 9,684 | 822 | 2 |
| global / GOT function pointer (`adrp; ldr; br`) | 54 | 872 | 0 | 1 |
| pointer from a global object (`adrp; ldr; ldr; br`) | 50 | 11 | 1 | 0 |
| ptr = [xB+off], xB from an earlier block | 79 | 19 | 2 | 0 |
| no definition in window / other (ldp, stack reload, csel, add…) | 89 | 40 | 0 | 1 |
| **total BR** | **12,944** | **12,644** | **952** | **7** |

The 174 absolute-pointer BRs in main are mostly indexed tail calls through function-pointer tables. Of the 43 whose table could be resolved, 42 have function entry points as entries (relocated by RELR) and 1 has labels inside its own function.

| BLR class | main | sdk | subsdk0 | rtld |
|---|---:|---:|---:|---:|
| vtable-style: `ldr vptr,[obj,#o]; ldr fn,[vptr,#slot]; blr` | **130,489 (95.5%)** | 14,050 (89.4%) | 1,001 | 1 |
| vtable-style, object pointer from the stack | 307 | 17 | 32 | 0 |
| ptr = [xB+off], xB from an earlier block | 1,483 | 518 | 21 | 0 |
| no definition in window (incoming argument / earlier block) | 1,259 | 151 | 1 | 3 |
| register-indexed pointer table `[xB, xI]` | 1,100 | 24 | 1 | 0 |
| pointer from `ldp` | 430 | 204 | 8 | 1 |
| pointer from a global object (`adrp; ldr; ldr`) | 419 | 80 | 70 | 2 |
| ptr = [xB+off], xB computed (add/ldp/mov/madd…) | 899 | 192 | 204 | 0 |
| global / GOT function pointer (`adrp; ldr`) | 150 | 203 | 0 | 0 |
| pointer loaded with acquire (`ldapr`) | 0 | 208 | 0 | 0 |
| other (stack reload, address built in code, csel, orr…) | 85 | 76 | 3 | 3 |
| **total BLR** | **136,621** | **15,723** | **1,341** | **10** |

The commonest vtable slots in main are:

| slot | calls | slot | calls |
|---|---:|---|---:|
| +0x30 | 15,198 | +0x60 | 6,800 |
| +0x10 | 14,810 | +0x90 | 3,910 |
| +0x38 | 13,604 | +0x80 | 3,419 |
| +0x20 | 13,373 | +0x50 | 3,008 |
| +0x18 | 10,658 | +0x28 | 2,968 |
| +0x0 | 8,098 | +0x1d8 | 2,292 |

The GPU API is reached through function pointers. main imports only `nvnBootstrapLoader`, and the `pfnc_nvn*` proc-pointer globals are defined in main and imported by subsdk0.

**Jump tables.** All are LLVM's compressed tables: 1- or 2-byte entries (some 4-byte), scaled by 4 and added to an `adr` anchor right after the BR. Every table is in rodata.

| module | jump-table BRs | entry width (ldrb / ldrh / ldrsw) | tables with bound+base decoded | entries | entries inside own function | tables with outliers | bound/base not found |
|---|---:|---|---:|---:|---:|---:|---:|
| main | 6,346 | 5,508 / 585 / 79 (+174 absolute) | 5,492 | 56,265 | 56,175 (99.84%) | 2 (heuristic misses, inspected) | 680 |
| sdk | 697 | 562 / 122 / 12 (+1) | 546 | 10,112 | 10,112 (100%) | 0 | 150 |
| subsdk0 | 89 | 78 / 10 / 1 | 81 | 1,677 | 1,677 (100%) | 0 | 8 |
| rtld | 3 | 0 / 0 / 3 | 0 | – | – | – | 3 |

Decoded tables in main average 10 entries; the largest has over 1,000.

**Tail calls and function boundaries.**

| module | B to another FDE start (tail call) | distinct tail-call targets | B to PLT (tail call to an import) | other B leaving the function | conditional branch leaving the function | B within function |
|---|---:|---:|---:|---:|---:|---:|
| main | 29,127 | 9,012 | 14,240 | **0** | **0** | 224,902 |
| sdk | 5,054 | 1,724 | 5,801 | 1 | 0 | 48,372 |
| subsdk0 | 87 | 49 | 300 | 0 | 0 | 4,935 |
| rtld | 2 | 2 | 0 | 0 | 0 | 120 |

Every direct branch in main stays inside its FDE range or goes to a function start or PLT stub. There are no conditional tail calls and no jumps into the middle of another function.

Code addresses built in code:
- **main:** `adr` to a function start 2,267, `adrp+add` to a function start 1,149, `adr` inside the same function 6,238 (the jump-table anchors).
- **sdk:** 1,563 / 23 / 696 respectively.

## 5. Floating-point control

- **main never touches FPCR or FPSR** (zero MRS/MSR to either). It imports no `fe*` function, so the game itself never changes the rounding mode or reads exception flags.
- **sdk** has 12 FPCR accesses (7 `mrs`, 5 `msr`) and 15 FPSR accesses (8 `mrs`, 7 `msr`). They are all in three places:
  - the C fenv functions `fegetround`, `__fesetround`, `fegetenv`, `fesetenv`, `fetestexcept`, `feclearexcept` and `feraiseexcept`;
  - `nn::os::detail::UserExceptionHandler`, which saves and restores both registers around the user handler (3 save/restore pairs);
  - two tiny unexported helpers just before the PLT, next to libunwind's `unw_getcontext` (1 FPCR read, 1 FPSR read, 1 FPSR write).
- **sdk** also has `msr nzcv` ×2, in `nndetailCryptoBignumAddWords` and `nndetailCryptoBignumSubWords` (restoring the carry).

Reciprocal and square-root estimates appear only as vector `.4s`/`.2s` operations, used as Newton–Raphson pairs:

| module | FRECPE | FRSQRTE | FRECPS | FRSQRTS |
|---|---:|---:|---:|---:|
| main | 219 | 565 | 438 | 1,130 |
| sdk | 9 | 1 | 18 | 2 |

There are no scalar FRECPE/FRSQRTE and no FRECPX, URECPE or URSQRTE.

### Instructions whose semantics differ from x86-64

| instruction group | main | sdk | subsdk0 |
|---|---:|---:|---:|
| SDIV/UDIV (ARM: x/0 = 0, INT_MIN/−1 = INT_MIN; x86 traps) | 4,018 | 1,465 | 189 |
| float→int FCVTZS/FCVTZU/FCVTAS/FCVTMS/FCVTPS/… (ARM saturates, NaN→0; x86 returns the "integer indefinite" value) | 7,978 | 1,761 | 3 |
| FMADD/FMSUB/FNMADD/FNMSUB (fused, one rounding) | 53,607 | 2,628 | 2 |
| FMLA/FMLS vector/element (fused) | 20,623 | 1,115 | 0 |
| FMIN/FMAX/FMINNM/FMAXNM (+P/V) (NaN and ±0 rules differ from MINSS/MAXSS) | 1,523 | 94 | 0 |
| FRINT* (explicit rounding modes) | 358 | 106 | 0 |
| FRECPE/FRSQRTE (ARM-defined estimate tables) | 784 | 10 | 0 |
| FRECPS/FRSQRTS (fused Newton steps) | 1,568 | 20 | 0 |
| FSQRT/FDIV | 23,854 | 1,177 | 3 |
| TBL (out-of-range index → 0) | 2,704 | 181 | 0 |
| RBIT | 37 | 179 | 2 |
| CLZ/CLS | 982 | 652 | 158 |
| UMULH/SMULH | 1,103 | 820 | 17 |
| ADC/SBC/ADCS/SBCS/NGC (ARM C = NOT borrow) | 411 | 217 | 0 |
| NZCV setters (CMP/CMN/TST/ADDS/SUBS/ANDS/BICS/NEGS) | 475,704 | 101,815 | 12,604 |
| CCMP/CCMN/FCCMP | 18,116 | 2,454 | 281 |
| FCMP/FCMPE | 59,735 | 1,569 | 4 |
| NZCV consumers CSEL/CSINC/CSINV/CSNEG/CSET(M)/CINC/CINV/CNEG/FCSEL | 152,976 | 22,689 | 3,435 |
| B.cond | 398,309 | 83,305 | 9,569 |

## 6. Thread-local storage

- **`TPIDR_EL0`:**
  - main reads it 145 times and never writes it.
  - sdk reads it 2 times and writes it 3 times. The writes are in `nn::os::detail::ThreadManagerImplByHorizon`'s constructor and two unexported functions. Both reads (unexported) have the shape of a TLSDESC dynamic resolver: `ldr x1, [tp, #8]` (the DTV), then a module-index load.
- **main's pattern** is TLSDESC relaxed by the linker to **local-exec**: `movz x0, #hi, lsl #16; movk x0, #lo; nop; nop; mrs x8, tpidr_el0`, then `ldr/str [x8, x0]` or `add`. The TLS offsets are link-time constants.
  - In 104 of 145 sites the sequence directly follows a `cbz; bl` (a guarded call, which fits lazy thread_local initialization).
  - First use after the read: `ldr [TP, reg]` 68, `str [TP, reg]` 9, `ldr/ldrb/stp [TP, #imm]` 10, not used within 8 instructions 58 (the TP is used later or passed on).
- **`TPIDRRO_EL0`** (the Horizon thread-local region): sdk only, 430 reads, of which the commonest first uses are:

  | first use | sites |
  |---|---:|
  | `str [TLR, #0]` / `stp [TLR, #0]` / `str` or `stp [TLR, #0x8 / #0x10]` | 155 / 21 / 111 |
  | `ldrh [TLR, #0x100]` | 38 |
  | `ldr [TLR, #0x1f8]` (current `nn::os` thread) | 37 |
  | `ldr [TLR, #0x1f0]` | 9 |
  | `ldr [TLR, #0x1e0]` | 5 |

  The stores at TLR+0x0…0x48 are consistent with Horizon's IPC message buffer at the start of the thread-local region. The 8- and 16-bit fields at +0x100…+0x104 were not identified.

## 7. System registers and SVCs

| module | op | register | sites |
|---|---|---|---:|
| main | mrs | tpidr_el0 | 145 |
| sdk | mrs | tpidrro_el0 | 430 |
| sdk | mrs | cntpct_el0 | 43 |
| sdk | mrs / msr | fpsr | 8 / 7 |
| sdk | mrs / msr | fpcr | 7 / 5 |
| sdk | msr / mrs | tpidr_el0 | 3 / 2 |
| sdk | msr | nzcv | 2 |
| sdk | mrs | ctr_el0 | 2 (`nn::os::FlushDataCache` and one unexported helper) |

There is no read of `cntfrq_el0`, `cntvct_el0`, `dczid_el0`, `midr` or `mpidr`, and no `dc zva`. The only cache maintenance is `dc civac` ×1 (`nn::os::FlushDataCache`) and `ic ivau` ×1 (an unexported helper).

**SVCs.** sdk has exactly one stub per SVC, packed at sdk+0x180bb0..0x180ecc. main and subsdk0 have none. rtld has 7 (0x02, 0x03, 0x06, 0x26, 0x27, 0x28, 0x29). The stubs are unexported, so each is named here by the exported sdk functions that call it.

| SVC | Horizon name | in npdm | callers of the stub | calling sdk exports (top) |
|---|---|---|---:|---|
| 0x01 | SetHeapSize | yes | 1 | `MemoryHeapManagerImplByHorizon::SetHeapSize` |
| 0x02 | SetMemoryPermission | yes | 5 | `nn::os::detail::SetMemoryPermissionImpl` ×3, 1 unexported |
| 0x03 | SetMemoryAttribute | yes | 4 | `nn::ro::ProtectRelro` ×2, `SetMemoryAttributeImpl` |
| 0x04 | MapMemory | yes | 2 | 2 unexported |
| 0x05 | UnmapMemory | yes | 4 | `FiberImplByHorizon::UnmapAliasStack`, 2 unexported |
| 0x06 | QueryMemory | yes | 26 | `nn::ro::detail::GetExceptionInfo` ×7, 7 unexported, … |
| 0x07 | ExitProcess | yes | 1 | 1 unexported |
| 0x08 | CreateThread | yes | 10 | `ThreadManagerImplByHorizon::CreateThread` |
| 0x09 | StartThread | yes | 1 | `ThreadManagerImplByHorizon::StartThread` |
| 0x0A | ExitThread | yes | 1 | 1 unexported |
| 0x0B | SleepThread | yes | 2 | `YieldThread`, `TimeoutHelperImplByHorizon::Sleep` |
| 0x0C/0x0D | Get/SetThreadPriority | yes | 2 / 1 | `ThreadManagerImplByHorizon` |
| 0x0E/0x0F | Get/SetThreadCoreMask | yes | 1 / 1 | `ThreadManagerImplByHorizon` |
| 0x10 | GetCurrentProcessorNumber | yes | 1 | `GetCurrentCoreNumber` |
| 0x11/0x12 | SignalEvent / ClearEvent | yes | 1 / 2 | `InterProcessEventImplByHorizon`, `InterruptEventImplByHorizon` |
| 0x13/0x14 | Map/UnmapSharedMemory | yes | 3 / 4 | `SharedMemoryImplByHorizon` |
| 0x15 | CreateTransferMemory | yes | 1 | `TransferMemoryImplByHorizon::Create` |
| 0x16 | CloseHandle | yes | 12 | `nn::os::CloseNativeHandle`, … |
| 0x17 | ResetSignal | yes | 6 | `InterruptEventImplByHorizon::Wait/TryWait/TimedWait` |
| 0x18 | WaitSynchronization | yes | 15 | `InterProcessEventImplByHorizon::TimedWait` ×3, `ThreadManagerImplByHorizon::TryWaitForExitThread` ×2, … |
| 0x19 | CancelSynchronization | yes | 1 | `MultiWaitImplByHorizon::CancelWait` |
| 0x1A/0x1B | ArbitrateLock / ArbitrateUnlock | yes | 4 / 6 | `InternalCriticalSectionImplByHorizon`, `ReaderWriterLockImplByHorizon` |
| 0x1C/0x1D | WaitProcessWideKeyAtomic / SignalProcessWideKey | yes | 2 / 2 | `InternalConditionVariableImplByHorizon` |
| 0x1F | ConnectToNamedPort | yes | 2 | `nn::sm::Initialize` |
| 0x21/0x22/0x23 | SendSyncRequest / …WithUserBuffer / SendAsyncRequestWithUserBuffer | yes | 3 / 1 / 1 | `nn::sf::hipc::SendSyncRequest`, `SendAsyncRequest` |
| 0x25 | GetThreadId | yes | 2 | `ThreadManagerImplByHorizon` |
| 0x26 | Break | yes | 7 | `nn::ro::UnloadModule` ×2, 2 unexported, … |
| 0x27 | OutputDebugString | yes | 1 | `nn::diag::detail::PrintDebugString` |
| 0x28 | ReturnFromException | yes | 0 direct B/BL callers | – |
| 0x29 | GetInfo | yes | 41 | 19 unexported, `VammManager::GetVirtualAddressMemoryResourceUsage` ×2, … |
| 0x2C/0x2D | Map/UnmapPhysicalMemory | yes | 4 / 2 | `nn::os::detail::VammManager` |
| 0x32/0x33 | SetThreadActivity / GetThreadContext3 | yes | 2 / 1 | `SuspendThreadUnsafe`, `ResumeThreadUnsafe`, `GetThreadContextUnsafe` |
| 0x34/0x35 | WaitForAddress / SignalToAddress | yes | 15 / 12 | `nn::os::Wait/SignalLightEvent`, `LightMessageQueue` |
| 0x36 | SynchronizePreemptionState | yes | 38 | 9 unexported, `nn::os::TrySendLightMessageQueue` ×2, … |
| 0x40/0x41 | CreateSession / AcceptSession | **no** | 1 / 1 | `nn::sf::hipc::CreateSession/AcceptSession` (server side) |
| 0x43/0x44 | ReplyAndReceive (+WithUserBuffer) | **no** | 4 / 3 | `nn::sf::hipc::Receive`, unexported |
| 0x45 | CreateEvent | **no** | 1 | (unexported) |
| 0x4B | CreateCodeMemory | **no** | 2 | `nn::jit::CreateJitEnvironment` |
| 0x50 | CreateSharedMemory | **no** | 1 | (unexported) |
| 0x51/0x52 | Map/UnmapTransferMemory | **no** | 4 / 1 | `TransferMemoryImplByHorizon::MapImpl` / 1 unexported |
| 0x70/0x72 | CreatePort / ConnectToPort | **no** | 1 / 1 | `nn::sf::hipc::CreatePort/ConnectToPort` |
| **0x80** | not in the Switch 1 table | yes | 6 | unexported, called only from `nn::gs::sdk::Create/DestroyNativeMemory` and `nn::gpu::sdk::Create/DestroyGpuAccessibleMemory`: a **Switch 2 GPU-memory SVC** |

npdm permits 0x1E (GetSystemTick), 0x20 (SendSyncRequestLight) and 0x24 (GetProcessId), but no code issues them; the tick comes from `mrs cntpct_el0` instead. The 11 stubs marked **no** exist in the SDK library but would fail under this game's permissions.

## 8. Cross-check with the rewriter (`build201/patched/*.tsv`)

The rewriter's `.sites.tsv` gives per-site kinds and `.gates.tsv` gives the original address of each SVC and counter gate. The census agrees **address for address**:

| module | site kind | census | rewriter | same address | census only | rewriter only |
|---|---|---:|---:|---:|---:|---:|
| main | mrs tpidr_el0 | 145 | 145 | 145 | 0 | 0 |
| main | x18 use | 36,583 | 36,583 | 36,583 | 0 | 0 |
| sdk | mrs tpidr_el0 / msr tpidr_el0 | 2 / 3 | 2 / 3 | 2 / 3 | 0 | 0 |
| sdk | mrs tpidrro_el0 | 430 | 430 | 430 | 0 | 0 |
| sdk | mrs cntpct_el0 | 43 | 43 | 43 | 0 | 0 |
| sdk | mrs ctr_el0 | 2 | 2 | 2 | 0 | 0 |
| sdk | svc | 57 | 57 | 57 | 0 | 0 |
| sdk | x18 use | 16,636 | 16,636 | 16,636 | 0 | 0 |
| subsdk0 | x18 use | 2,395 | 2,395 | 2,395 | 0 | 0 |

The SVC immediates are identical: the gate arguments are the same 57 numbers as the census's.

**Differences.** These are accesses the rewriter leaves alone because Apple's kernel permits them. A Windows recompiler must still handle them.
- sdk `mrs`/`msr fpcr` (7/5) and `fpsr` (8/7)
- sdk `msr nzcv` ×2
- sdk `dc civac` ×1 and `ic ivau` ×1

Neither tool sees `cntvct_el0`, `cntfrq_el0` or a misaligning `sub sp, sp, xN`. The rewriter's other rows (`S` spin waits: main 49, sdk 5, subsdk0 5; `J` x18 jump sites: 12, 17, 1) are rewrite decisions, not instruction kinds.

x18 use is a Horizon convention (x18 is an ordinary register there). It was costly on Apple's kernel and **does not matter to a static recompiler**, which keeps the guest's x18 in its own register file.

## Implications for the recompiler

### Must support (fast path)

1. **Base A64 integer, load/store and branches: 91% of all instructions.** This includes every addressing mode: 168 k pre/post-index writebacks and 97 k register-offset accesses in main. NZCV has to be modelled precisely. main has 476 k flag setters, 398 k `b.cond`, 153 k `csel`-family consumers and 18 k `ccmp`/`ccmn`, so the flags should map directly onto host flags with lazy materialization. The carry after SUB/SUBS is NOT borrow (411 `adc`/`sbc` in main).
2. **Scalar f32 maths with fused multiply-add.** main has 53.6 k scalar `fmadd`-family and 20.6 k vector `fmla`/`fmls`. Bit-exact results need FMA3 on the host (x86-64-v3) or a software fused fallback. Also needed:
   - fix-ups for the **7,978 saturating float→int conversions** (ARM saturates and gives 0 for NaN; x86 gives the "integer indefinite" value);
   - NaN and ±0 semantics for 1,523 `fmin`/`fmax`/`fminnm`/`fmaxnm`;
   - `sdiv`/`udiv` guards for divide-by-zero and INT_MIN/−1 (4,018).
3. **FPCR can be treated as a constant for the game's own code.** main never reads or writes FPCR or FPSR. Only sdk's fenv functions and `UserExceptionHandler` do. The initial value (FZ, DN, rounding) is not visible in the binary: read it once on hardware or in the Mac port and set MXCSR (FTZ/DAZ) to match.
4. **NEON, mostly 4×f32:** 134.6 k instructions in main, 88 mnemonics. This is lane moves (37 k), by-element `fmul`/`fmla`, `movi`, `zip`/`uzp`/`ext`/`rev64`, and `tbl` (2.9 k, which needs `pshufb` plus a zero fix-up for indices ≥ 16). SSE4.1 is enough for nearly all of it.
5. **Atomics are LSE and RCpc, not LL/SC.**
   - The mappings: `casal` → `lock cmpxchg`, `ldadd*` → `lock xadd`, `swp*` → `xchg`, `ldclr`/`ldset` → a cmpxchg loop.
   - `ldar`/`ldapr`/`stlr` become plain `mov`s on x86 (TSO).
   - `dmb ish` (2.5 k) needs a full fence, while `dmb ishld`/`ishst` are free.
   - main has **no exclusive-monitor code**, so there is no LL/SC emulation on the hot path.
6. **Indirect calls dominate dynamic dispatch.**
   - main has 136.6 k BLR, of which 95.5% are vtable-style, plus 5,440 vtable tail calls through BR.
   - **60% of main's functions are reached only indirectly.** 92,742 FDE starts are address-taken: 246 k RELR targets point into text.
   - A fast guest-address → host-function lookup is needed, for example a direct-mapped table over the ~148 k FDE starts.
   - Making every FDE start an entry point covers all relocation targets but 86, and those 86 land in libcurl.
7. **Tail calls.** main has 29,127 direct tail calls, 14,240 tail calls to imports and 5,440 indirect tail calls. These need guaranteed tail calls (LLVM `musttail`) or a dispatcher loop, otherwise deep chains will overflow the host stack.
8. **Jump tables (6,346 in main) are statically recoverable.** They use LLVM's compressed format in rodata. 89% decode with a proven bound, and 100% of their entries land inside the owning function. The remainder (≈680 + 174 absolute-pointer dispatches) need a per-function "BR to any block start" fallback.
9. **C++ exceptions are live.** main has 3,388 functions with LSDAs (sdk 574) and imports `__cxa_throw`, `_Unwind_Resume` and `__gxx_personality_v0`. `setjmp`/`longjmp` are imported too.
   - The guest unwinder (libunwind in sdk) walks guest frames with the original `.eh_frame`. The recompiled code therefore has to keep guest stack frames (x29/x30 chains) in guest memory.
   - Every LSDA landing pad must also be a re-entry point. Their count was not measured here; that is the next step.
10. **Return-address signing with the B key in ~57% of main's functions** (`pacibsp` 83,986, `retab` 79,219, `autibsp` 32,096, `zRB` CIEs). Treat PAC hints as no-ops and `retab` as `ret`. That is consistent provided signed LR values are never compared: the only consumers are `xpaci`/`xpaclri` in sdk diagnostics and libunwind, which strip signatures anyway.
11. **Thread state.**
    - `TPIDR_EL0` (local-exec TLS, constant offsets; written only in sdk thread setup) and `TPIDRRO_EL0` (thread-local region: IPC buffer at +0, `nn::os` thread pointer at +0x1F8 and +0x1F0) must be per-thread guest registers.
    - `cntpct_el0` (43 sites, sdk only) must return a monotonic tick at the guest frequency.
    - `ctr_el0` → constant; `dc civac`/`ic ivau` → no-ops.

### Rare enough for a slow path or a call-out

| item | where | count |
|---|---|---:|
| SVC (all sdk stubs + rtld; the natural HLE boundary if sdk is recompiled; or replace sdk at the 925 `nn::`/libc entry points main imports) | sdk / rtld | 57 / 7 |
| FPCR/FPSR access (fenv functions, `UserExceptionHandler`) | sdk | 27 |
| FRECPE/FRSQRTE (emulate ARM's estimate tables if bit-exact determinism matters) | main / sdk | 784 / 10 |
| FRECPS/FRSQRTS (exact fused steps) | main / sdk | 1,568 / 20 |
| Crypto AES/SHA-1/SHA-256/PMULL (`nn::crypto` only: AES-NI, SHA-NI, PCLMUL, or HLE) | sdk | 1,542 |
| CRC32 (non-C polynomial ×35 has no SSE4.2 equivalent) / CRC32C ×13 | sdk | 48 |
| `msr nzcv` (bignum carry restore) | sdk | 2 |
| LL/SC exclusives (gettext) | sdk | 5 |
| MTE `stg` (no-op) | sdk | 1 |
| PAC data ops `pacia`/`xpaci`/`aut*1716` (identity) | sdk | 7 |
| `wfe`/`sevl`/`yield` (`pause`/yield) | sdk | 10 |
| UDF with a non-zero immediate: `#0x8002` traps inside functions, `#0x8005` in sdk's FDE-less assembly, one `#1` per module in padding (raise a guest fault) | main / sdk / subsdk0 | 352 / 108 / 8 |
| `rbit` (no x86 equivalent) / `umulh`, `smulh` (MUL/IMUL high half) | main | 37 / 1,103 |
| BR/BLR whose target comes from an incoming argument or an earlier block (generic dispatcher) | main | 2,849 |

### Not present (no need to support)

- SVE or SME
- FP16 arithmetic
- dot product, i8mm and bf16
- BTI
- pointer-authenticated branches and loads (BRAA, BLRAA, RETAA, LDRAA)
- `ldxr`/`stxr` in main
- literal pools, or any data in text beyond two NSO header words per module
- BRK
- `dc zva`
- counter registers other than `cntpct_el0`
- TLS relocations or IRELATIVE
- calls into the middle of a function
- conditional branches that leave their function

### Surprising

- **The toolchain targets ARMv8.2+ with RCpc and pac-ret+b-key.** That means LSE atomics everywhere (29.7 k in main, zero LL/SC), `LDAPR` (4.5 k), and B-key return signing, which is unlike Switch 1 titles. It makes x86 atomics easier and PAC harmless.
- **main uses packed RELR relocations** (285,495 entries), so the loader must implement `DT_RELR`. main also supplies `malloc`/`free` to the SDK, which imports them from main.
- **libcurl is the only real code without unwind info.** It is 239 KB (0.58% of main), and function discovery there must use BL targets (780), relocation targets (86) and prologue scanning (622 `pacibsp`). Everywhere else, FDE ranges are exact function boundaries: no BL lands mid-function and no branch leaves its function except as a tail call.
- **NVN is invisible in the import table.** main imports only `nvnBootstrapLoader`; every GPU call goes through proc-pointer globals.
- **SVC 0x80 is new.** It is absent from Switch 1's table and allowed by the npdm, and it backs `nn::gpu::sdk::CreateGpuAccessibleMemory` and `nn::gs::sdk::CreateNativeMemory`.
- **11 SDK SVC stubs are not permitted by the game's npdm**: the server side of IPC, JIT code memory, and shared/transfer memory creation.
- **Traps are `udf #0x8002`-style, not `brk`**, and inter-function padding is `udf #0` (≈0.4 MB in main).
- **Function size is extreme at both ends.** 29% of main's functions are 16 bytes or smaller (thunks and getters), and the largest is 68 KB (~17 k instructions). main is 10.3 M instructions in 148 k functions, so the LLVM build must be sharded across many translation units.
