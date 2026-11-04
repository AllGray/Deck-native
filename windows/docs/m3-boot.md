# M3: booting the game's own code on Windows

Status, 2026-11-03: **M3 done.** Pokopia 2.0.1's own code, statically recompiled into `game.dll`, runs on nxrt from sdk's
initialisation through `nnMain` to the game's first frame request. The null NVN device logs `WindowAcquireTexture` at
0.561 s and `QueuePresentTexture` at 0.568 s after process start, with no graphics.

Afterwards the game aborts at 1.05 s in its audio set-up, which is the first blocker for M4 (§6). Every function Windows entered
before the abort was also entered on the Mac (§3).

## 1. How far each run got

The steps the task set:

| # | step | reached | evidence (final run) |
|---|---|---|---|
| 1 | the DLL links and loads | yes | `game DLL ... base 00007FFB53AA0000, 387.5 MB, 200414 functions (main 76a489f3bd3642fa), table filled in 149.5 ms` |
| 2 | the first guest instruction runs | yes | `_init_libc0`, then `>>> nnosInitialize` at 0.369 s |
| 3 | init arrays complete | yes | `main: DT_INIT +0x20 (init_array 280 entries)`, `subsdk0 ... (1)`, `sdk: DT_INIT +0x4a0 (91)` at 0.375 s |
| 4 | `nnMain` is reached | yes | `>>> nnMain` at 0.376 s |
| 5 | threads start | yes | 32 guest threads (`KtglFsThread_Host`, `File`, `COMMON0-4`, `ZRead`, `ZWrite`, `ZLibDecoder_WorkerQueue0-4`, ...), 248 same-core handoffs |
| 6 | the romfs is read | yes | `fs RomFS image ...RomFS.img: 44 entries`; with `DM_FS=1`, 18 opens before the abort (`rom:/LI_C0_R.IDX`, `.../system.rdb`, `root.rdx`, `LD_C0_R.BIN`, `0x38f972af.fdata`, ...) |
| 7 | NVN device creation | yes | `[nvn] null device: DeviceInitialize ... at 0.409 s`, two queues, `WindowInitialize swap chain 3 textures, interval 1, first 1920x1080 format 0x38` (the Mac logs the same line) |
| 8 | the first present request | **yes** | `[nvn] first frame request: WindowAcquireTexture (texture 0) at 0.561 s, called from main+0x114b6dc` and `QueuePresentTexture (texture 0) at 0.568 s, called from main+0x114aef0` |

The runs, in order:

| run | reached | stopped at | fixed before the next run |
|---|---|---|---|
| r1 | `nnMain` at 0.392 s | 0.406 s: a null read in sdk's `nn::am::GetGraphicsSupervisor`, from `nv::InitializeGraphics` → `nn::gs::sdk::BootGraphicsSystem` → `nn::gsv::BootWithRemoteMemory` | nxnative's `dm/gfx/hle_nv.c` and `hle_vi.c` take-overs were missing from program.db and from the registry (§4.1) |
| r2 | the first frame request at 0.637 s | 1.150 s: `nn::diag` abort (§6) | none needed for M3 |
| r3 | the same, with `NXRT_TRACE=first:400000 DM_FS=1` | the same abort | used for the Mac comparison (§3) |
| final | the same, with the final binaries: 0.561 s / 0.568 s | the same abort at 1.049 s | – |

The final run's log (lines on the boot path; 82 "stub ... not ported yet" lines are left out, 80 of them NVN commands):

```
[    0.219] program loaded in 217.5 ms: game build 76a489f3bd3642fa5e18bd83d32c98bacc5f7d0e (2.0.1)
[    0.219] host replacements: 957 forced + 62 swhle + 734 NVN = 1753 in the registry (953 ported, 61 with nxnative's constant result, 739 stubbed), 0 named stubs, 587 relocation slots bound
[    0.368] game DLL ...\_local\nxlift\m3\game.dll: base 00007FFB53AA0000, 387.5 MB, 200414 functions (main 76a489f3bd3642fa), table filled in 149.5 ms
[    0.369] >>> nnosInitialize
[    0.369] >>> nninitInitializeSdkModule
[    0.369] init takeover: nndiagStartup (no-op)          ... the same seven as on the Mac
[    0.369] >>> nninitStartup
[    0.369] [mem] heap 9.92 GB (heap region 10.25 GB)
[    0.375] main: DT_INIT +0x20 (init_array 280 entries)
[    0.375] subsdk0: DT_INIT +0x20 (init_array 1 entries)
[    0.375] sdk: DT_INIT +0x4a0 (init_array 91 entries)
[    0.376] >>> nnMain
[    0.376] nv::InitializeGraphics: pool 0.00 MB, recorded (the host's graphics layer allocates its own)
[    0.376] init takeover: oe::Initialize (no-op)
[    0.376] oe::SetFocusHandlingMode 1 (focus notifications)
[    0.376] fs RomFS image ...\build201\extract\RomFS.img: 44 entries
[    0.376] fs mount rom: → ...\RomFS.img (read-only)
[    0.409] [nvn] null device: DeviceInitialize 000000734C6F20D8 flags 0 at 0.409 s
[    0.409] [nvn] null device: QueueInitialize 000000734C6EF080
[    0.555] [nvn] WindowInitialize swap chain 3 textures, interval 1, first 1920x1080 format 0x38
[    0.555] [nvn] WindowCrop 0,0 1920x1080
[    0.561] [nvn] first frame request: WindowAcquireTexture (texture 0) at 0.561 s, called from main+0x114b6dc
[    0.568] [nvn] first frame request: QueuePresentTexture (texture 0) at 0.568 s, called from main+0x114aef0
[    1.049] game called nn::diag abort: Result 0, call site sdk+0x144f2c
```

The Mac (research/05's `pilot` host log) logs the same sequence from `>>> nnosInitialize` to `WindowCrop 0,0 1920x1080`, in the same order and with the same values: heap 9.92 GB, init array sizes, focus mode 1, swap chain.

No indirect branch missed the table in any run: no `.misses.txt` was written, so the interpreter fallback never ran.

## 2. What was built

### 2.1 The game DLL (nxlift)

`nxlift <ExeFS> --db program.db --out _local\nxlift\m3 --jobs 4 --dll _local\nxlift\m3\game.dll` lifts, compiles and links in one run. It
links at idle priority, in a single lld-link process.

**The contract, settled with nxrt's proposal (`nxrt/rt_abi.h`).** `nxw_game.obj`, which nxlift writes, holds these dllexport data symbols:

- `nxw_game_abi` (= `NXW_ABI_VERSION`, 1);
- `nxw_game_build_id` (main's 40 hex digits);
- `nxw_game_function_count`;
- `nxw_game_functions[]`, `{guest_va, rva, flags}`.

The RVA is the constant `trunc(sub(ptrtoint @fn_X, ptrtoint @__ImageBase))`, which LLVM's COFF lowering turns into an image-relative
relocation. So the table needs no post-link step.

**What a freestanding DLL needs.** The same object carries `__chkstk` (a page probe), `memcpy`, `memmove` and `memset` (`rep movsb` /
`stosb`) and `_fltused`, in module assembly. Only `_fltused` was referenced.

**The link.** `lld-link /DLL /NOENTRY /NODEFAULTLIB /OPT:REF /OPT:NOICF /HIGHENTROPYVA:NO` with the 305 partition objects,
`nxw_game.obj` and `build\Release\lib\nxrt.lib`. It took 1.1 s. The DLL imports only from nxrt.exe: the data symbols `rt_code_table`,
`rt_code_base`, `rt_code_words`, `rt_host_base` and `rt_trace_on`; the `rt_*` functions; and the `adapter_*` functions.

**The object cache.** `--cache`, by default `<out>\..\objcache`, is keyed by xxh3-128 of each partition's bitcode after `finish()`, plus the
level and a cache version. A hit hard-links the cached object, which skips optimisation and code generation (85 % of the time). After the
binding fix (§4.1), 300 of the 305 partitions were hits, and the rebuild took 1 min 58 s instead of 11 min 26 s.

**Lifter changes.**

- **Spin loops.** Spin-loop heads (120 sites, counting YIELD, WFE and WFI) now call `rt_yield(cpu, kind, site)`, with the state stored and
  reloaded around the call, instead of an inline `pause`. The interpreter does the same for those hints.
- **Context functions.** sdk's four context functions (flags `sp_switch`, `lr_write`, `context_branch` and `ret_other`, exactly
  `DispatchFiberContext`, `UserExceptionHandler`, `longjmp` and libunwind's `jumpto`) are lifted as `cpu->pc = entry; rt_nonlocal(cpu)`.
  None ran, on the Mac or here.
- **Function-entry trace.** An optional hook, `if (rt_trace_on) rt_trace_entry(cpu, entry)`, is on by default in the CLI;
  `--no-trace-hooks` turns it off. The JIT tests do not use it.

**Already right, checked.**

- The state is stored before every call into the runtime.
- BL's semantics write x30 before the call.
- `cpu->pc` is set before `rt_svc`, `rt_trap`, `rt_mrs`, `rt_msr` and now `rt_yield`.
- PLT-bound host calls are `call adapter_<symbol>` (8,257 sites).
- Each `NxwGuestCpu` is host heap below 4 GB, outside every guest range.

### 2.2 nxrt

- **Exports and loading.**
  - `rt_host_base` is exported and set to the DLL's base.
  - The DLL's function list fills the code table: 200,414 entries in 150 ms.
  - A copy sorted by RVA maps host addresses back to guest functions. Crash reports print `game:sdk+0x1c1b10 <symbol> (+0xc1)` for frames
    in translated code, and nearest-symbol names for guest addresses.
- **The interpreter.** `src/interp` is linked into nxrt and attached by default (`NXRT_NO_INTERP` turns it off). `rt_miss` sets
  `cpu->pc` and returns `nxw_interp_trampoline` for a 4-aligned target inside module text, and appends it to `<log>.misses.txt`.
- **The boot sequence** is nxnative's, which the Mac runs: `_init_libc0`, `nnosInitialize(main handle, 0)`, `_init_libc1`,
  `nninitInitializeSdkModule`, `SetHostArgc/Argv`, `nninitStartup`, `_init_libc2`, each module's DT_INIT in load order (each DT_INIT runs
  its own init array), then `nnMain`. The `nxrt.boot_order` test checks this order.
- **Options.**
  - `nxrt.exe <ExeFS> --dll <game.dll> --romfs <RomFS.img> --save <dir> --temp <dir> [--log <dir>]`. `--romfs`, `--save` and `--temp`
    set DM_ROMFS, DM_SAVE and DM_TEMP for hle_fs.c.
  - The runs used a fresh `_local\nxrt-run\<tag>\save`. The installer's RomFS image was only read.

### 2.3 HLE ported (from nxnative, MIT, with origin notes)

| file | what changed for Windows |
|---|---|
| `hle_fs.c`, `romfs_image.h` | POSIX calls through the new `plat/posix_fs_win.h`: binary descriptors, 64-bit `pread`/`pwrite`/`stat` (the image is over 2 GB), FindFirstFile directories, guest names in NTFS alternate data streams instead of xattrs (NTFS is case-insensitive, as APFS is). Transactions: clonefile is a tree copy; RENAME_SWAP is three renames, not atomic yet (D13). Save sync dropped. |
| `hle_oe.c` | unchanged except DM_OE_SCRIPT's `bg` step (no audio or graphics to suspend). Replaces `oe_defaults.c`, which reported handheld; the profile default, as on the Mac, is console. |
| `hle_sdk16.c` | the shared font in VirtualAlloc memory; the vsync thread at above-normal priority; the abort's call site is the guest's x30 and x29 |
| `hle_hid.c` with `hle_touch.c`, `hle_haptics.c/.h`, `motion_fusion.h`, `dm_input.h`, `front.h` | no front end, so every state is neutral; DM_KEYS and DM_CTL scripted input work; DM_CTL `shot`, `tex*` and `pass*` say there is no renderer |
| `dm/gfx/hle_nv.c`, `dm/gfx/hle_vi.c` | `unsigned long` → `uint64_t`; otherwise unchanged |
| `hle_nvn.c` | **the null device**, 169 of the 734 names: device (Tegra X1 constants, API 55.15), memory pools (the game's storage; a virtual pool gets a reserved range), texture and sampler pools, textures (nxnative's block-linear sizes through `nvn_tex.c`, copied unchanged), buffers, samplers, programs, syncs and events (signalled at once), queues, command buffers (an empty recording is an 8-byte END in the game's command memory, accounted as nxnative accounts it), the window (its textures in turn; presents paced at 60 Hz). It logs the first acquire and the first present. The other 565 names are the generated stubs: logged once, neutral. `NXRT_EXIT_AT_PRESENT=N` ends a run at the Nth present. |

Registry: 1,753 entries, 953 ported (was 444).

### 2.4 Tracing switches

- **`NXRT_TRACE=calls:N`.** Every function entry, the first N: thread, x0–x2, sp, caller.
- **`NXRT_TRACE=first:N`.** The first entry of each function, numbered.
- Both go to `<log>.trace.txt`. The hook turns itself off when the counts run out.
- **nxnative's switches** still work: `DM_FS`, `DM_SVC`, `DM_THR`, `DM_KEYS`, `DM_CTL`.

## 3. The Mac as the oracle

`NXRT_TRACE=first:400000` (run r3) recorded the first entry of every function up to the abort. Against research/05's executed function entries
(`pilot`, `fullb` and `light`):

| module | entered on Windows | also entered on the Mac | never on the Mac |
|---|---:|---:|---:|
| main | 9,930 | 9,930 | 0 |
| sdk | 421 | 420 | 1: `nn::os::GetSystemTickFrequency`, which carried a probe instead of a coverage trap on the Mac (research/05 §5), so it ran there too |
| subsdk0 | 3 | 3 | 0 |

So Windows has not diverged from the Mac up to the abort, and the existing traces were enough: no new Mac session was needed. The comparison
script is a dozen lines (set difference of `F` lines against `executed_<module>.txt`).

## 4. Fixed along the way

### 4.1 Found by booting

1. **Missing take-overs.** nxanalyze and the HLE generator never read `dm/gfx/hle_nv.c` and `dm/gfx/hle_vi.c`, nxnative's take-overs of `nv::`
   (the GPU driver glue) and `vi::` (display, layers, vsync).
   - **Effect.** program.db bound main's calls to `nv::InitializeGraphics` to sdk's own code, which booted the real graphics system and read
     a null am object (run r1). The Mac takes them over.
   - **Fix.** Both now read `dm/gfx/*.c`. main's forced imports went from 332 to 344 and the relocation slots bound from 573 to 587. Both
     files are ported.
2. **Multi-line take-over macros.** `hle_hid.c`'s `TOUCH(N)` is a `#define` over several lines that builds names with `#N`.
   - **The generator** registered 2 truncated names instead of 32.
   - **nxanalyze** saw none of them: the touch-screen reads would have run the SDK's own code.
   - **Fix.** Both join continued lines and expand `#`/`##`; nxanalyze for macros that build names this way.
3. **The full-O2 objects of the coverage run** still used the old `nxhle_` adapter names, so everything was rebuilt once.

### 4.2 Lifter and runtime integration

- `rt_miss` did not set `cpu->pc` before handing out an interpreter entry.
- `rt_host_base` was not exported.
- The four context functions and the spin loops had no runtime entry.
- `_fltused` must be defined by the DLL.
- A `long long` printf in a hot path of hle_fs.c was corrected.

### 4.3 Build tree

`.gitignore`'s `out/` also matched `src/nxrt/gen/out/`, so nxrt's generated registry and adapters, which the build compiles, were never in
git. The new line `!src/nxrt/gen/out/` brings them back for your commit.

## 5. Timings (i9-13900KF, 4 jobs, idle priority)

| what | time |
|---|---|
| full lift, O2, 305 partitions, 200,414 functions | 685 s (lift 395 CPU-s, optimise 1,212, codegen 1,113), 11 min 26 s with loading and the link |
| the link: 432 MB of objects → 387.5 MB DLL | 1.1 s |
| rebuild after the binding fix, cache on (5 partitions recompiled) | 1 min 58 s |
| nxanalyze (program.db) | 2.8 s |
| nxrt: reserve, map, bind | 0.22 s |
| DLL load and table fill | 0.15 s |
| process start to `nnMain` | 0.376 s |
| process start to the first frame request | 0.561 s (acquire) / 0.568 s (present) |
| test suite (18 tests) | 20 s |

## 6. Next blockers

1. **Audio.**
   - `hle_audren.c` and `hle_audio.c` are not ported.
   - `nn::audio::AcquireAudioDeviceNotificationForOutput`'s stub leaves the game's SystemEvent uninitialised. Its first
     `nn::os::TryWaitSystemEvent` (from main+0x11dc690) aborts the game at 1.05 s.
   - On the Mac the next lines are `audio #0 OpenAudioOut (default) channels 2 silent sink (call site main+0x11dc264)`: a silent sink
     port is the fix, and the start of M4.
2. **`hle_misc.c`.**
   - aoc: `CountAddOnContent` returns 0, as nxnative does without DM_AOC_DIR.
   - ngc: the profanity filter's host vtable, which needs a runtime-token API for hand-written adapters (m3-runtime §8.7).
3. **NVN.** 565 names are stubs, the command recording among them. The Vulkan replay is M5.
4. **Saves.** The save mount comes after audio on the Mac, so it is not reached yet. The NTFS swap is three renames: DESIGN D13's
   commit marker is still to do.
5. **Keeping the lifter and the runtime in agreement.** A check that program.db's forced set equals the generated registry's would have
   caught §4.1 at build time. nxrt does not read program.db yet.
6. **Still open from earlier milestones.** D5's bridge (none ran), exact FP beyond FPCR = 0, and performance: not measured yet. Boot is
   not the place to measure it.

## 7. Tests added (18 in all, all passing)

| test | checks |
|---|---|
| `nxrt.gamedll` | A stand-in DLL (`tests/game_dll.c`) imports from the test executable, as the game DLL imports from nxrt.exe. The contract is accepted, `rt_host_base` is the DLL's base, the table reaches each function, an indirect call inside the DLL through the imported table data reaches a function and a host-call token, a function import resolves, and host addresses map back to guest functions. |
| `nxrt.boot_order` | On the real modules (NXW_DUMP), a recorder at each bootstrap address. The order is the Switch loader's, and the arguments are nnosInitialize's handle, `argc` 1 and `argv[0]` "game". Each step runs on the guest main thread with x30 = RETURN_TO_HOST, a 16-byte-aligned stack top in the stack region, and a TLR. |
| `nxrt.nvn` | The null device through its generated adapters: tokens and unknown names; the device and its API constant; queue memory size; pool Map and address; RGBA8 256×256 storage size and alignment; texture getters; buffer Map, address and size; a recording's handle and accounting; sync before and after a fence; window texture rotation; the two "first frame request" lines. |

Updated: `nxrt.adapters` now expects 957 forced entries (was 918; §4.1), and `nxrt.generated` covers the new ports.
