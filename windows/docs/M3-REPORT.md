# Milestone 3 report: the CPU core

2026-11-03. M3's goal (DESIGN §6): the game's own code runs on Windows from sdk's initialisation to its first frame request,
with no graphics. **Done.** The run log is [m3-boot.md](m3-boot.md); the components are in [m3-lifter.md](m3-lifter.md) and
[m3-runtime.md](m3-runtime.md).

## The result

Pokopia 2.0.1's own code, statically recompiled from AArch64 to x86-64 into `game.dll` (387.5 MB at O2), runs on `nxrt`:

| After start | Milestone |
|---:|---|
| ~0.4–0.5 s | sdk init and every module's DT_INIT done; **`nnMain`** |
| | 32 guest threads started; the RomFS mounted and read |
| ~0.57 s | the NVN device created (a null device for now) |
| ~0.72 s | **first frame request:** `WindowAcquireTexture`, then `QueuePresentTexture` |
| ~1.2 s | the game aborts in its audio set-up (M4's first blocker) |

Every function Windows entered before the abort also ran on the real game on the Mac (9,930 of 9,930 in main), and the log
from `nnosInitialize` to the window set-up (`WindowInitialize 3 textures 1920x1080`) matches the Mac's. Reproduced independently
before committing.

## What M3 built

| Commit | Piece |
|---|---|
| `6c456a8` | the guest CPU ABI: nxnative's 800-byte register frame first, so its SVC handlers take it unchanged |
| `490f797` | `nxrt` v0: the game's address space at its fixed addresses, the Horizon thread, sync and SVC layer ported from nxnative, 1,714 generated host-call adapters |
| `4ead2d1` | semantics (721 ops), lifter and interpreter: all 410 ops the game uses checked alone, 30 real functions × 3,000 inputs, 0 mismatches |
| `1faa9a6` | integration: the game DLL, the loader and code table, the boot path's HLE ported (fs, oe, sdk16, hid, nv, vi), a null NVN device |

18 tests, all passing. A full O2 rebuild of the game is about 11 minutes at 4 jobs on idle priority, or about 2 minutes with
the object cache.

## Bugs that booting found (all fixed)

- `nv::` and `vi::` host take-overs were missed by nxanalyze and the adapter generator (`dm/gfx/hle_nv.c`, `hle_vi.c` weren't
  read), so the SDK's real graphics boot ran and crashed. main's host-bound imports went from 332 to 344.
- `hle_hid.c`'s multi-line `TOUCH(N)` macro was misread (2 bogus names, 32 real ones missing).
- `rt_miss` didn't set `cpu->pc` before handing over to the interpreter.
- `.gitignore`'s `out/` hid the generated adapters from git, so `490f797` alone wouldn't build from a fresh checkout. Fixed in `1faa9a6`.

## Notes

- **The ABI header didn't change.** The runtime contract grew: `rt_yield`, `rt_nonlocal`, `rt_trace_on`/`rt_trace_entry` and
  `rt_host_base`.
- **Toolchain:** LLVM 23.1.4's libraries expect a newer MSVC STL than Build Tools 14.38; `msvc_stl_shims.cpp` covers 16 helpers
  until Build Tools are updated to 17.14 or later.

## M4: complete CPU and system (next)

Goal: the game runs its title-screen logic with the null renderer, and every function is verified against the Mac.

1. **Audio:** port `hle_audren.c` and `hle_audio.c` with a silent sink, as the Mac can, then real output through SDL3 later.
   This is the current abort.
2. **The rest of the boot-path HLE:** `hle_misc.c` (add-on content, the profanity filter) and whatever the title path needs
   next. Each is found by running, compared against the Mac.
3. **A build-time check** that program.db's host-bound set equals the runtime registry's; it would have caught the `nv::`
   miss at build time.
4. **Saves:** the NTFS commit made atomic (D13), with kill tests.
5. **The Mac swap-in harness** (D15): the lifted code compiled for arm64 and swapped into nxnative function by function, so
   every function is checked against the real one, not just the ones the boot reaches.
6. **Exceptions, longjmp, fibers** (D5): still off the critical path (none run in normal play), but needed for completeness.
7. **NVN command recording** behind the null device, ready for M5's Vulkan replay.
