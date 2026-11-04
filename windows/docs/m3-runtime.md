# M3: the runtime, v0 (`nxrt`)

`src/nxrt/` is the Windows runtime that will host the recompiled game: it reserves the guest address space at the game's own
addresses, maps the modules nxload links, runs guest threads on nxnative's Horizon layer ported to Win32, and gives every host
replacement a generated AArch64-to-Win64 adapter and a guest-visible address. It exports what the game DLL imports. Guest code
cannot run yet: that needs the lifter's DLL. Until then, tests run host functions registered as guest functions, and they drive
the kernel only through `rt_svc`, as recompiled code will.

v0 in numbers:

- about 4,250 lines of C/C++ (about 3,100 of them in files ported from nxnative) and a 660-line generator;
- 15,100 lines of generated adapters, stubs and registry;
- 6 tests (820 lines), all passing.

On the real 2.0.1 ExeFS, `nxrt.exe` reserves the ranges, maps main, subsdk0 and sdk, and binds 573 relocation slots to host
tokens, in 0.23 s.

## 1. What was ported from where

Every ported file opens with a `// from nxnative dm/host/<file> (MIT, © seedds)` line and keeps its original order and comments, so
fixes can be carried across. nxnative's licence is in `src/nxrt/third_party/nxnative-LICENSE`.

**Core** (`src/nxrt/host/`):

| nxrt file | from nxnative | lines (orig → port) | what changed |
|---|---|---:|---|
| `dm.h` | `dm/host/dm.h` | 194 → 77 | `LOG`/`die` go to the run's log; `module_t` moved to `module.h`, cut to what a mapped image needs; guest calls go through `GUEST_CALL`; `DM_GUEST_RA()` replaces `__builtin_return_address` (the guest's x30). Dropped: key page, gates, cheats, Unreal, fps state. |
| `hle.h` | `dm/host/hle.h` | 69 → 40 | `HLE` / `HLE_FORCE` only declare the function. The generator registers the entry from the same line; Mach-O sections are gone. `HLE_FORCE_BIG` is `HLE_FORCE`, since host code always runs on the host stack. |
| `hle.c` | `dm/host/hle.c` | 132 → 124 | Host-call tokens replace the 4,096 assembled stubs; named stubs are dynamic tokens. `nxrt_host_call` is the host's entry from guest code: it counts calls and checks MXCSR (debug). The gate dispatch is gone. |
| `hle_svc.c` | `dm/host/hle_svc.c` | 701 → 486 | Fixed block at `0x72_0000_0000`; 4 KB pages, so no host-page rounding. Heap and alias are views of one pagefile-backed section. The heap commits lazily, in 1 MB chunks on first touch. `MapMemory` maps a real alias; it copies only for non-section sources. GetInfo 33 answers 4 KB. Dropped: the address-region observation gates (iPhone workarounds). |
| `hle_thread.c` | `dm/host/hle_thread.c` | 1,206 → 825 | Locks, condvar, address arbiter, events, wait-sync, handles and same-core handoffs are unchanged. pthread became SRWLOCK and CONDITION_VARIABLE; QoS became gentle Windows priorities; core masks are bookkeeping only. Pausing stops a thread in an SVC or at a safepoint (`rt_yield`), not by signal. Dropped: hitch dump, power-line CPU readings (Mach). |
| `thread.c` | `dm/host/thread.c` | 75 → 29 | TLS block (8 KB) in `cpu->tpidr_el0`; TLR (0x200 + 0x400 object, TLR+0x1f8, ID at +0x110) in `cpu->tpidrro_el0`. Gate stacks are gone. |
| `hle_os.c` | `dm/host/hle_os.c` | 170 → 155 | QueryPerformanceCounter clock; 31.25 MHz counter computed without 128-bit division; BCrypt entropy. `getenv` and `StandardAllocator::Allocate` call the SDK through `GUEST_CALL`. |
| `crash.c` | `dm/host/crash.c` | 215 → 178 | Vectored handler for fault hooks; unhandled-exception filter, `abort` and CRT-invalid-parameter handlers write the report (§4). Dropped: watchdog. |
| `loader.cpp` | `dm/host/loader.c` | 662 → 170 | nxload links. This file maps the images, applies `bind_sym`'s host side (forced first, then swhle, then named stub) as token writes, sets protections and runs `run_module_inits` as guest calls. The SDK string parser also reads the Switch 2's `nnSdk-Generic_20_5_25`. |
| `main.c` | `dm/host/main.c` | 146 → 103 | `nxrt_init` (log, banner, reservation check, handlers), `nxrt_load_program`, and the bootstrap in `nn::init::Start` order as guest calls on a guest main thread (npdm: priority 44, core 0, 1 MB stack at the top of the stack region). |

**HLE services** (`src/nxrt/hle/`):

| nxrt file | from nxnative | lines (orig → port) | what changed |
|---|---|---:|---|
| `hle_table.c` | `dm/host/hle_table.c` | 14 → 77 | Each `L(name)` became an explicit Switch signature. `long` is now `int64_t` (strtol, strtoul, lroundf). struct tm goes through musl's 56-byte layout (`sw_time.h`). mktime and localtime_r are shadowed by `hle_time.c`. |
| `hle_sdkinit.c`, `hle_nvdrv.c`, `hle_account.c`, `hle_friends.c` | same names | 15, 17, 113, 66 → +3 each | none (an origin line, the adapters include) |
| `hle_offline.c` | `dm/host/hle_offline.c` | 264 → 267 | 10 `long` / `unsigned long` → `int64_t` / `uint64_t` |
| `hle_net.c` | `dm/host/hle_net.c` | 137 → 140 | 17 `long` → `int64_t` / `uint64_t`; `__thread` → `_Thread_local` |
| `hle_time.c` | `dm/host/hle_time.c` | 156 → 166 | `localtime`, `localtime_r` and `mktime` convert musl's struct tm (`sw_tm`) to the CRT's; `tm_gmtoff` is computed with `_mkgmtime`. |
| `hle_nvn.c` | `dm/gfx/nvn/hle_nvn.c` | 2,652 → 28 | v0 keeps only the bootstrap: `nvnBootstrapLoader` and `nvnDeviceGetProcAddress` hand out the token of any of the 734 names. |

**New** (no nxnative counterpart):

| file | does |
|---|---|
| `plat/plat.h`, `plat_win32.c` | QPC clock, high-resolution sleep, `plat_read`, threads, gentle priorities, CPU and Windows info, entropy |
| `plat/posix_win.h` | The pthread subset the ported files use: mutex = SRWLOCK, cond = CONDITION_VARIABLE, once = INIT_ONCE, keys = FLS with destructors. Also `clock_gettime`, `nanosleep`, `localtime_r`, `strlcpy`. |
| `host/addrspace.c` | the reservation, placeholder carving, the guest section and its views (DESIGN D3) |
| `host/rt.c` | `rt_*` exports, the code table, the token thunks, `nxrt_guest_call`, attaching the game DLL |
| `host/log.c` | the log and its banner |
| `host/oe_defaults.c` | focus, op mode and applet stand-ins until `hle_oe.c` is ported |
| `hle/sw_time.h` | musl's `struct tm` |
| `gen/gen_hle.py` → `gen/out/` | the registry and adapters (§5) |
| `tools/nxrt_main.c` | `nxrt.exe <exefs> [--dll game.dll] [--log dir] [--quiet]` |

**`long`.** My count finds 87 non-`long long` uses of `long` in `dm/host/*.c` plus `hle_nvn.c`; research/02 counted 94 with the
headers.

- **Fixed in the ported files:** 48 (`loader.c` 15, `hle_net.c` 17, `hle_offline.c` 10, `hle_thread.c` 3, `crash.c` 1, `hle.c` 1,
  `hle_time.c` 1).
- **Left:** none in nxrt, apart from `timespec.tv_nsec`, which is `long` in the CRT.
- **Unported files:** the generator refuses `long` in a ported signature, so their uses get fixed when they are ported.

## 2. The address space actually reserved

The ranges are reserved by a TLS callback in each executable (`NXRT_EARLY_RESERVE()`). It runs before the CRT and before the
executable's own code, as `VirtualAlloc2` placeholders. All executables link with `-Xlinker /HIGHENTROPYVA:NO`. This takes effect
with lld-link: `DllCharacteristics` reads 0x8140, with no `HIGH_ENTROPY_VA`, and the test checks it. Host allocations then land
below 4 GB: `malloc` at 0x16_7D040, `VirtualAlloc` at 0x157_0000. The executable's own image stays high (0x7FF6…).

| range | base | size | contents |
|---|---|---|---|
| host-call tokens | `0x70_FFF0_0000` | 1 MB | 65,536 × 16 bytes, read-only. Each starts with `BRK #0x4E58` and holds its index (token 0 = `RETURN_TO_HOST`). |
| modules | `0x71_0000_0000` | 256 MB | 2.0.1: main `0x7100000000`–`0x7103d20000`, subsdk0 → `0x7103e60000`, sdk → `0x7104e50000` (nxload's bases). Text and ro are `PAGE_READONLY`, never executable; data and bss are read-write. |
| guest block | `0x72_0000_0000` | 11 GB (`mem_reserve`) | `+0` 256 MB unused; alias `0x7210000000` (256 MB, `mem_alias`); stack `0x7220000000` (256 MB, `mem_stack`); heap `0x7230000000` (10.25 GB, the rest). App pool 10 GB, `mem_used` 0x5600000. |

- **Below 2^39.** Everything is below 2^39, and the test checks it.
- **The guest section.** Alias, stack and heap are one pagefile-backed `SEC_RESERVE` section, with no commit charge until a page is
  committed. The alias and heap regions are views of it; the rest of the block stays placeholder, so it is inaccessible.
- **`svcMapMemory`.** It maps a second view of the source's section pages into the stack region at 4 KB granularity, which
  `MapViewOfFile3` allows for placeholders. The test writes through one address and reads through the other. Unmapping returns the
  range to a placeholder and coalesces the gap. A source outside the section (an NRO's bss) falls back to nxnative's copy.
- **Lazy heap.** The heap is committed in 1 MB chunks on first touch (a fault hook), so the ~9.9 GB heap Pokopia asks for costs commit
  charge only for what it uses. Kernel-mode writes (file reads into guest buffers) go through `dm_guest_write_prepare`, which commits
  first.
- **The code table** (research/03 §5.4): 79 MB reserved for `[tokens, end of sdk)`, 20.8 M slots, committed page by page on first
  touch.

**If a range is taken.** The log names it in red: base and size of the allocation in the way, its state and type, and the mapped
file or module that owns it. `nxrt_init` then stops with return code -2. The fallback is a launcher:

1. `CreateProcess(nxrt.exe, CREATE_SUSPENDED)`.
2. Check the ranges with `VirtualQueryEx`.
3. Reserve them in the child with `VirtualAlloc2(hChild, …, MEM_RESERVE | MEM_RESERVE_PLACEHOLDER)`.
4. Pass a flag that tells the child's TLS callback to adopt the placeholders instead of reserving them.
5. `ResumeThread`.

v0 only detects the case, but `nxrt.addrspace` exercises the mechanism the launcher needs. It creates itself suspended, takes 64 KB
of the child's token range with `VirtualAllocEx` before any child code runs, and the child reports the conflict at
`0x70fff10000`.

## 3. Threads and the Horizon layer

**One host thread per guest thread.** A guest thread is a Windows thread (16 MB host stack reservation) that owns a 64-byte-aligned
`NxwGuestCpu`:

- x0 = argument, sp = the SDK's stack top, x30 = `RETURN_TO_HOST`;
- `tpidrro_el0` = its TLR, `tpidr_el0` = its TLS block.

It enters its function through the code table, and returning from it is fatal, as in nxnative. `ExitThread` frees the TLS blocks and
ends the host thread; the object goes once the handle is also closed.

**Priorities** map gently (DESIGN D9; research/01 lesson 11):

| Horizon priority | Windows priority |
|---|---|
| ≤ 27 | above normal |
| 28–44 | normal |
| 45–59 | below normal |
| ≥ 60 | lowest |

Nothing is pinned.

**Unchanged from nxnative:** the single global lock `G` with a per-thread condition variable, kernel-order waiter selection,
same-core wake handoffs, the yield back-off, spin waits, short timed waits as polls, SystemEvent bindings, and
`SetThreadActivity` / `GetThreadContext3` on threads in SVCs. Timed waits run at millisecond resolution: the process sets
`timeBeginPeriod(1)`, and sub-20 µs waits are polls, as before.

**The clock.** The counter runs at 31.25 MHz from QPC. The test measured 31,249,998–31,250,029 Hz over 100 ms. `cntfrq_el0` reads
31,250,000.

## 4. Logging and crash reports

**The log.** Each run writes `_local\nxrt\nxrt-<date>-<time>-<pid>.log` (or `NXRT_LOG_DIR`), flushed every line. It starts with:

- the runtime and ABI version;
- `GPU: none` (the Vulkan renderer is later);
- the CPU, its x86-64-v3 check, logical processors and RAM;
- the Windows build (`26200.9711 25H2` here);
- the profile;
- each reserved range (or the conflict), the guest block's partition, and the high-entropy ASLR state.

Loading adds each module's base and build ID, the SDK version, and the game build ID with its version name.

**Crash reports.** Any crash writes `<log>.crash.txt` and copies it to the log. Covered: unhandled exceptions, `die`, `abort`, CRT
invalid parameters, `svcBreak`, `rt_trap`, `rt_miss` with no target, and `rt_nonlocal`. A report holds:

- the exception code and the fault, as module+offset;
- the host frames (`RtlVirtualUnwind`);
- the faulting thread's guest context: pc and lr symbolised (`main+0x…` or `host:<token>`), x0–x30, sp, nzcv, fpcr/fpsr,
  tpidr/tpidrro, the first words behind each register, and the guest x29 chain;
- every guest thread's state.

The test makes a guest thread write to 0x10 and checks the report.

## 5. HLE: coverage and the adapter generator

**Coverage** (registry of 1,714 entries):

| kind | ported | nxnative's constant | stubbed | total |
|---|---:|---:|---:|---:|
| forced take-overs (`HLE_FORCE`) | 381 | 128 | 409 | 918 |
| swhle (C library, `HLE`) | 62 | – | 0 | 62 |
| NVN entry points | 1 + the bootstrap | – | 733 | 734 |
| **all** | **444** | **128** | **1,142** | **1,714** |

**What Pokopia 2.0.1 references.** It references 465 forced names through relocations (nxanalyze):

- 274 ported;
- 36 with nxnative's constant;
- 155 stubbed.

Main's 332 forced imports split 211 / 22 / 99. The stubs are mostly:

| file | stubbed references | of which nxnative's constant |
|---|---:|---:|
| `hle_fs.c` | 36 | 5 |
| `hle_hid.c` | 25 | 2 |
| `hle_movie.c` | 19 | 1 |
| `hle_sdk16.c` | 14 | 8 |
| `hle_audio.c` | 14 | – |
| `hle_misc.c` | 11 | 12 |
| `hle_codec.c` | 10 | – |
| `hle_oe.c` | 9 | 7 |
| `hle_sf.c` | 8 | – |
| `hle_audren.c` | 8 | – |

**Stubs.** A stub logs `stub <name> (<file>, not ported yet)` once, with the guest call site. Its result is neutral: 0 in x0/x1 and
v0–v3, and a zeroed x8 buffer when the result goes through x8. A "constant" entry is one whose nxnative body is only `return <constant>;`
after `(void)` casts. The generator reproduces that constant (for example `RES_FAIL` for `ldn::Initialize`), so cut services fail the
way nxnative fails them, and do not succeed into retry loops (research/01 lesson 12).

**The generator** (`gen/gen_hle.py`, Python, no dependencies) reads nxnative's sources as text, as nxanalyze does:

1. **Parse.** It strips comments and `#if 0`, then finds every `HLE_FORCE`, `HLE_FORCE_BIG` and `HLE` call. It expands the file-local
   macros built on them (`PALMA_FAIL`, `SKF`, `SSF`, `SSC`, `IKB_IGNORE`), reads `L()` in `hle_table.c`, and reads `NVN` /
   `NVN_NOPROTO` in `hle_nvn.c` with `names.txt` and `nvn_protos.h`.
2. **Choose the source.** A file nxrt has ported (the same name in `host/` or `hle/`) is read from the port. Its adapters go into
   `gen/out/<file>.adapters.inc`, which the port includes at its end, so each adapter calls the static host function directly, with
   every type in scope. Entries a port leaves out stay registered as stubs.
3. **Deduplicate.** Forced wins (nxnative's binding order); 2 duplicates are shadowed (mktime, localtime_r).
4. **Classify types.** Every type is classified from a table: integers with width and sign, pointers, float, double, the `nvn.h`
   typedefs (enums, handles, function pointers), and named composites with their size and kind. An unknown type stops the
   generator, and `long` in a ported signature is refused (LLP64).
5. **Write the adapters.** `void adapter_<symbol>(NxwGuestCpu *cpu)` follows AAPCS64:
   - **Arguments.** Integers and pointers go in x0–x7, then 8-byte stack slots at `cpu->sp`; floats and doubles in v0–v7 (s/d), then
     the stack. GPR and FPR counters are kept separately. Composites up to 16 bytes go in x registers, homogeneous float aggregates
     in consecutive v registers, and larger ones by reference.
   - **Narrow values.** Narrow integers are truncated by the cast, since Switch callers leave the upper bits unextended. Variadic
     arguments are already fixed parameters in nxnative, because the Switch passes them in registers.
   - **Results.** Results go to x0 (sign- or zero-extended), x0/x1 for small composites, s0/d0 with the rest of the register zeroed,
     v0–v3 for float aggregates, or `*x8` for composites over 16 bytes.
   - **Assumed sizes.** `_Static_assert` checks every composite size the table assumes.
6. **Write the outputs.** `hle_registry.gen.c` holds the name, kind, ported/constant/stub status, file, nxnative's "why" and the
   adapter for each entry; it also writes `hle_stubs.gen.c`, `hle_adapters.h` (what the DLL imports) and `hle_inventory.tsv` (every
   entry with its signature and status). `--check` makes the `nxrt.generated` test fail when the committed output is stale.

**Host-call tokens.** At start-up, each registry entry gets the token `NXRT_TOKEN_BASE + 16 × (index + 1)`; named stubs get the next
ones when an import needs them. When the game DLL is attached, the runtime places 65,536 thunks right after the DLL's image, within
4 GB as the 32-bit RVAs need:

```
mov edx, k
jmp common          ; common: jmp [rip] → nxrt_host_call
```

It then points the code table's slot for every token at its thunk. An indirect call through a run-time-filled pointer (the 1,078
NVN and HLE procedure pointers in research/05) takes the normal fast path and reaches `nxrt_host_call(cpu, k)` with the caller's
`rcx` intact, which runs the adapter. `rt_miss` handles tokens too, so a table without thunks still works.

`nvnBootstrapLoader` returns the token of any of the 734 NVN names, and NULL for unknown names, as nxnative does. Relocation slots
bound to a host replacement receive its token (2.0.1: 573 slots, 332 of them main's GOT).

## 6. The ABI the game DLL imports (`include/nxrt/rt_abi.h`)

**Exports from `nxrt.exe`**, with import library `nxrt.lib`:

- `rt_code_table`, `rt_code_base`, `rt_code_words`, `rt_miss`, `rt_svc`, `rt_mrs`, `rt_msr`, `rt_trap`, `rt_nonlocal` (all as in
  research/03 §5.3);
- `rt_yield` and `rt_return_to_host`, added (below);
- 1,714 `adapter_<symbol>` functions.

Details:

- **`rt_mrs` / `rt_msr`** take the decoder's system-register number (`o0:op1:CRn:CRm:op2`, e.g. `cntpct_el0` = 0x5f01).
  - Counters return 31.25 MHz ticks. CTR_EL0 is 0x8444c004 (nxnative's DminLine 4); DCZID is 4.
  - `msr fpcr` other than 0 is logged once.
- **`rt_trap(cpu, pc, why)`** writes a report and exits. The kinds are UDF, BRK, HLT, unreachable, undecodable, and the
  jump-table-out-of-range default the 118 unbounded tables need.
- **`rt_nonlocal`** reports and stops (D5: no throw, longjmp or fiber ran in play).
- **`rt_yield(cpu, kind, site)`** is added beyond research/03's list. It is the one entry point for spin-loop back edges, YIELD and
  WFE: nxnative's spin-wait back-off, and a safepoint for `SetThreadActivity`.
- **`rt_return_to_host`** is the `RETURN_TO_HOST` handler.
- **Proposed DLL exports** (checked by `nxrt_attach_game_dll`, to agree with the lifter): `nxw_game_abi` (= `NXW_ABI_VERSION`),
  `nxw_game_build_id`, and `nxw_game_functions[]` / `nxw_game_function_count`, each entry `{guest_va, rva, flags}`.

**`NxwGuestCpu`** is unchanged: no new field was needed. `reserved[3]` is unused.

## 7. Tests (`ctest -R nxrt`, all passing; `NXW_DUMP` set to the 2.0.1 ExeFS)

| test | checks | time |
|---|---|---:|
| `nxrt.addrspace` | 3 ranges reserved before `main` at the real bases, below 2^39; `/HIGHENTROPYVA:NO` in effect; host allocations below the guest; a 4 KB placeholder piece committed and released; a heap range aliased into the stack region (write one side, read the other); coalescing; a range taken in a suspended child is detected and described | 0.01 s |
| `nxrt.svc` | synthetic frames on the guest main thread (§7 SVC list below); a guest-thread crash report checked in a child process | 0.18 s |
| `nxrt.threads` | 4 guest threads at most, created and started by SVC on SDK-style stacks (heap, then `MapMemory`); mutex: 80,000 contended increments; condvar queue: 20,000 items, sums equal; address-arbitration barrier: 2,000 rounds, about 5,200 real sleeps; events ×500 across threads; pause and context of a thread waiting in an SVC; cancel; joins; all thread objects freed | 0.12 s |
| `nxrt.adapters` | 12 real-signature cases against direct calls (§7 below); registry shape (918 forced, 734 NVN, every entry with a token); a stub's neutral result; a constant entry (`ldn::Initialize` → 0x2A2); a ported entry (`nnsocketGetLastErrno` → 50); `strtol` of a 64-bit value | 0.04 s |
| `nxrt.dump` | modules at 0x7100000000 / 0x7103d20000 / 0x7103e60000; MOD0 present; text and ro `PAGE_READONLY`; data read-write; `nnMain` main+0xa4630, `nnosInitialize` sdk+0x151860; 332 of main's data slots hold tokens (= nxanalyze's 332 forced imports); QueryMemory over main's text (Code RX) and sdk's data (CodeData RW); the code table's span; `SetHeapSize(pool − 80 MB)` accepted and 2 MB more refused | 0.24 s |
| `nxrt.generated` | `gen_hle.py --check` against nxnative and the ports | 0.38 s |

**`nxrt.svc`'s list:**

- GetInfo (layout, pool, used memory, program ID, 4 KB granule, free thread count);
- SetHeapSize: lazy commit, grow and shrink, `0xD001` and `0x10801`;
- QueryMemory;
- MapMemory / UnmapMemory, including every error code;
- MapPhysicalMemory, with remapped pages reading zero;
- the tick at 31.25 MHz (±0.01 %);
- events and their errors, and a 5 ms timed wait;
- thread priority, core mask and processor number;
- sleep and yield;
- OutputDebugString, a notifying Break, an unknown SVC;
- a token called end to end: code table → thunk → registry → `hle_net.c`'s adapter.

**`nxrt.adapters`'s cases:**

- int and pointer arguments, with -1 sign-extended;
- a bool with garbage above bit 7, and a narrow result;
- pointers in and out;
- float after a pointer, a float result, and floats mixed with a pointer;
- doubles;
- an 8-byte struct result in x0 (`nvnDeviceGetSeparateTextureHandle`);
- a 0x34-byte struct through x8 (`glslcGetVersion`), with the bytes after it untouched;
- a variadic argument in x2 (`socket::Fcntl`);
- a ninth argument from the guest stack (`InitializeHardwareOpusMultiStreamDecoder`);
- synthetic float-aggregate argument and result.

**Other dumps.** `nxrt.exe` on the 2.0.1 ExeFS logs `SDK nnSdk-Generic_20_5_25`, binds 573 slots and creates 0 named stubs (every
import is defined or forced, as nxanalyze found). `<another ExeFS>` (main d4e672b8…, sdk 20.5.18, a build the profile does not
know) maps and binds too: 330 token slots, 570 relocations.

**Build note.** The shared `build\Release` tree currently fails to load in ninja: `src\sem\CMakeLists.txt`'s bitcode command writes a
generator expression unevaluated (`-I$<JOIN:…`, "bad $-escape" at build.ninja:1630). That is the other agent's area, so I left it.
nxrt was built and tested in a scratch tree with the repository's flags: common, abi, nxload and nxrt; clang 23.1; lld; 4 jobs at low
priority. `scripts\build.cmd` will pick nxrt up once the shared tree configures again.

## 8. Next: what booting sdk init through nnMain needs from the lifter

**1. The DLL.** It imports `nxrt.lib`. For each translated function it exports `{guest_va, rva}`, or another list we agree on.
`nxrt_attach_game_dll` fills the table and places the thunks. `dll_base` in the fast path is the DLL's `__ImageBase`.

**2. Calls into the runtime.** Before any call into the runtime (`rt_svc`, `rt_mrs`, `rt_yield`, an adapter, a miss), store every
live guest register into the cpu, including `sp`. Set x30 to the return address on BL: `DM_GUEST_RA`, crash reports and backtraces
read it. Before `rt_svc`, also set `cpu->pc` to the SVC's address. Reload x0–x7 (and q0–q3) afterwards: SVCs write up to x7, and
adapters write x0/x1/v0–v3.

**3. Instructions with runtime entries:**

| instruction | becomes |
|---|---|
| `svc #n` | `rt_svc(cpu, n)` |
| `mrs cntpct_el0` | `rt_mrs`, or later an inline QPC × scale |
| tpidr / tpidrro | inline field accesses |
| spin-loop back edges (nxanalyze's 111), YIELD, WFE | `rt_yield` |
| UDF, BRK, unreachable, out-of-table jump indices | `rt_trap` |

**4. Host replacements.**

- PLT calls that nxanalyze binds `forced` or `swhle` call `adapter_<symbol>` directly; the names are in `gen/out/hle_inventory.tsv`.
- Data slots and run-time pointers already hold tokens and need nothing.
- M2's open question 2 is settled as nxnative does it, which is also what the Mac oracle runs: forced take-overs apply to relocations
  only. A direct BL inside sdk to a forced function's own body stays a guest call.

**5. Guest calls from the host.** The host calls these guest functions through `nxrt_guest_call`, so the function list must contain
them:

- `_init_libc0/1/2`, `nnosInitialize`, `nninitInitializeSdkModule`, `nn::os::SetHostArgc/Argv`, `nninitStartup`;
- each module's `DT_INIT`, and `nnMain`;
- `nn::os::CreateSystemEvent`, `GetWritableHandleOfSystemEvent`, `getenv`, `StandardAllocator::Allocate(m)` / `(mm)`;
- every thread entry the game passes to `CreateThread`.

They are all exported symbols or FDE starts.

**6. The four sdk context functions.** `DispatchFiberContext`, `UserExceptionHandler`, `longjmp` and libunwind's `jumpto` must
become `rt_nonlocal` or `rt_trap` until D5's bridge exists.

**7. HLE beyond nnMain.** In order of need:

1. `hle_fs.c`: RomFS, save mounts and the transactional save of D13; 36 stubbed references.
2. `hle_oe.c` and `hle_sdk16.c`: focus, operation mode, settings, time zone, shared font.
3. `hle_hid.c`.
4. A null NVN device. `nvnDeviceInitialize` and friends are stubs returning 0 today, and the game will stop at its first NVN failure.
5. Audio: `hle_audio.c`, `hle_audren.c`, `hle_codec.c`.
6. `hle_misc.c`. It plants host function pointers in guest vtables (the profanity filter), which needs a small `nxrt_host_token(fn)`
   API for run-time tokens with hand-written adapters.

**8. Interpreter.** `nxrt_set_interpreter(entry_for)` is where `src/interp` attaches for misses inside module text. Every miss is
appended to `<log>.misses.txt` for the next build (research/03 §5.4).

**9. Pausing in guest code.** `SetThreadActivity` stops a thread running guest code only at a safepoint. If a game's garbage
collector needs it, the lifter should call `rt_yield`, or a cheaper check of a per-thread flag, at loop back edges or function
entries. Pokopia's engine has not shown it.

**Smaller items found along the way:**

- **Shared tree.** The `build.ninja` breakage above.
- **nxanalyze.** It counts 65 swhle entries because its `L(` scan also matches `#define L(n)` in `hle_table.c`; there are 64.
- **SDK version.** Reading its code, nxnative's SDK-version parser does not accept the Switch 2's `Generic_` form, so for Pokopia it
  should find no version and take the newer SDKs' side, which is harmless here; nxrt's port reads it.
