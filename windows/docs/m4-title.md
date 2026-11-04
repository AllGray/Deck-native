# M4 track A: the title screen's logic with a null renderer

Status, 2026-11-03: **the exit criterion is met, with two title-scene functions short, both explained.** Pokopia 2.0.1's recompiled code
(the M3 `game.dll`, unchanged) runs on nxrt through its boot, its loading screens and into the title screen's loop, and keeps
presenting at 59.9 frames a second for as long as it is left running. The Mac runs the same game at 59.9.

| | Windows (nxrt, null renderer) | Mac (nxnative, reference) |
|---|---|---|
| title loop reached (85 NVN functions a frame) | frame 1,200, 24 s | frame 1,200 |
| presents, 24 s to 129 s | 59.88–59.92 per s in every 300-frame window | 59.6–59.9 fps |
| main functions run before any input | 29,730 of the Mac's 29,732 | 29,732 |
| title-scene functions (first run by the Mac between 18 and 40 s) | 1,417 of 1,419 | 1,419 |
| RomFS files, in order of first open | the same 30, the same order | 30 |
| CPU at the title | 0.30 of a core (i9-13900KF) | 0.27–0.43 of a core (M4) |

Nothing is committed: everything below is in the working tree for review.

## 1. The stops and their fixes

The runs boot with the M3 command (`nxrt.exe <ExeFS> --dll _local\nxlift\m3\game.dll --romfs <RomFS.img> --save <copy> --temp <dir>`) and a
fresh copy of a save. With **no save** the game stops at its first-boot language screen (the Mac does too, `_local/mac-traces/m4title-cov-empty/title30.png`);
the title screen needs a save, so the reference runs on both machines use the run-4 save research/05 used (copied from the Mac to `_local/saves/run4`).

### 1.1 The audio abort (M3's stop)

```
[    1.049] game called nn::diag abort: Result 0, call site sdk+0x144f2c
  guest frames: main+0x11dc690 main+0x11dd2ac main+0x11dbf48 ...
```

`AcquireAudioDeviceNotificationForOutput` was a generated stub, so the SystemEvent it should bind stayed uninitialised and the audio thread's first
`TryWaitSystemEvent` aborted. **Fix:** `hle_audren.c` and `hle_audio.c` ported (origin notes kept), with the output behind an interface:

- `hle/audio_out.h`: an `audio_out_backend` (open, start, stop, close, pulls). nxnative's two consumers, its CoreAudio unit and its silent sink, are two
  backends: `audio_out_silent()` (`hle/audio_silent.c`) and `audio_out_device()`, which is NULL until SDL3 (DESIGN D14). `DM_AUDIO=1` falls back to the
  silent sink with a log line while there is no device. nxnative's watchdog, stall fallback, background suspend and statistics are kept around it.
- **The silent sink paces on the guest clock:** every 156,250 ticks of the 31.25 MHz counter (5 ms) it pulls 240 frames. A frozen guest clock (background)
  pulls nothing, and more than 100 ms behind restarts the count (nxnative's rule).
- The release events go through a notifier thread as on the Mac (dispatch semaphores became Win32 semaphores).
- `hle_audren.c`'s calls into the SDK's renderer are `GUEST_CALL`s; its render thread is a guest thread the host starts (`host/hle_guestthread.c`, below).
  Pokopia does not open a renderer (no `audren:` line on the Mac either), so this half is ported but not exercised by the game.

```
[    1.104] audio #0 OpenAudioOut (default) channels 2 silent sink (call site main+0x11dc264)
[    1.105] audio #0 Start (call site main+0x11dc45c)
```

These are the Mac's two lines, call sites included.

### 1.2 The Opus decoder (a stub that would have broken the sound streams)

```
[    1.100] stub _ZN2nn5codec47GetHardwareOpusMultiStreamDecoderWorkBufferSizeEiiiii (hle_codec.c, not ported yet): neutral result; ..., called from main+0x12b4078
```

A work buffer size of 0 for the hardware Opus decoder. The Mac relies on real behaviour here: nxnative swaps the hwopus service for the SDK's software
decoder. Its own note says that without it the KIDS loader never finishes its first sound stream and the main thread waits forever. **Fix:**
`hle_codec.c` ported, every call into the SDK's software decoder a `GUEST_CALL`.

### 1.3 `hle_misc.c`

Ported whole: add-on content, the shop and error applets (cut), nsd, pctl, and the profanity filter. The filter's constructor gives the object a host
vtable whose slots must be guest-callable. This is the runtime-token API m3-runtime §8.7 asked for:

- `nxrt_host_fn_token(name, adapter)` (`host/hle.c`) hands out a host-call token for a host function with a hand-written adapter.
- The vtable itself lives in host heap memory, below 4 GB like the guest's own pointers.

The same API gives `dm_guest_thread_spawn` its host entry point.

```
[    0.443] aoc: reporting 0 add-on contents {} (no DM_AOC_DIR)
[    1.291] ngc: ProfanityFilter host vtable of 14 slots (2 matched to the class's methods)
```

Both lines match the Mac's.

After these three fixes **no stop remains on the title path**. The only stubs the game still calls are 76 NVN commands that record nothing on a null
GPU. The next boot lines (`ToCalendarTime`, `fs mount save`, `hid supported controller styles 0x7`, `look: camera at ...`) follow in the Mac's order.

### 1.4 The frame loop (the null NVN device)

M3's null device presented, but the loop was not the Mac's.

1. **Recorded fences never signalled.** `nvnCommandBufferFenceSync` was a stub, and Pokopia fences its frame that way and waits with `nvnSyncWait`. Fences,
   event signals and counter reports in a recording now take effect when the recording is submitted. Before the submit a `SyncWait` times out; after it
   the sync is signalled. That is nxnative's replay, with a GPU that finishes at once. The effects are kept per command buffer while it records and per
   handle once it has ended. Every report of a submission carries the submit's guest time, as nxnative writes "one time for the whole submission".
2. **Texture views.** nxnative's 16-byte `view_t`: setters, getters, `GetViewOffset`.
3. **Texel reads and writes.** Block-linear swizzling is not ported (§7), so `ReadTexels` gives zeros and `WriteTexels` leaves the storage alone. Both are logged once.
4. **Presents.** M3 slept a period after each present had returned, which drifted with sleep overshoot to about 57 Hz. The device now has a 60 Hz display
   on a fixed vsync grid. A present returns at once and is shown at the first vsync after it, at least one interval after the frame before. Acquiring a
   texture waits while that texture is shown or queued, so a game that runs ahead is held back by its swap chain.
5. **Frame bookkeeping.** Each present ticks the guest frame (`dm_guest_frame`, which hid's scripted input reads; M3 never ticked it). Every 300 frames the
   device logs nxnative's frame statistics and the NVN calls of one frame.

**Frame rate: three steps.**

| null device | presents/s at the title | CPU | why |
|---|---:|---:|---|
| M3 (sleep after each present) | 57 | – | the sleep's overshoot accumulates |
| display on a vsync grid | 59.25–59.30 | 0.20 | the game paces itself, and its sleeps end late on Windows |
| + accurate guest sleeps and timed waits | **59.88–59.92** | **0.30** | as the Mac |

**The third step: guest sleeps on Windows.** `DM_THR` (which now also logs SleepThread) showed how the main thread waits out each frame: about 900
`SleepThread(1 ms)` and 120 `SleepThread(100 µs)` a second. Other threads poll with sleeps of 50–200 µs.

- **The timer is late.** Windows' high-resolution waitable timer wakes 0.36–0.50 ms late on this PC (measured; it cannot sleep less than about half a
  millisecond). nxrt's sleep still averaged 0.17 ms late on a 10 ms sleep. Condition-variable timeouts round to whole milliseconds.
- **Sleeps** (`host/hle_thread.c`) now ask the timer for the remaining time less its own lateness, learned as a moving average, and end on the deadline on average.
- **Very short waits** (≤ 150 µs), which the timer would stretch to half a millisecond, yield until the deadline.
- **Timed waits** use whole-millisecond condition-variable waits until 2.5 ms remain, then the same sleep.
- **What did not work.** Yielding through the last part of every sleep was exact but cost 1.3 cores. Returning early from short sleeps cost 1 core.

The NVN calls of a frame now match the Mac's:

| frame | Windows | Mac |
|---|---|---|
| 2 (after the first present) | 101 functions, 58,151 calls | 101 functions, 58,151 calls |
| 3 | 21 functions, 53 calls | 21 functions, 53 calls |
| 300 (loading) | 79 functions, 543 calls | 79 functions, 529 calls |
| 1,200 onward (title) | 85 functions, 2,954–3,160 calls | 85 functions, 3,008–3,140 calls |

### 1.5 What the takeover check found

The new build-time check (§4) failed on its first run:

- **The generator missed 16 take-overs.** `gen_hle.py` registered only the first `HLE_FORCE` of a file-local macro. `hle_hid.c`'s `TOUCH(N)` defines two, so
  the 16 `GetTouchScreenStates<N>` take-overs were in program.db but not in the runtime's registry. Fixed: every `HLE_FORCE` in an expansion is
  registered. The registry has 1,769 entries, 973 of them forced.
- **1.0.5's program.db was stale.** It predated M3's `dm/gfx` fix, and the check listed 52 differences (the nv:: and vi:: take-overs, and hid touch). It was
  rebuilt from the 1.0.5 ExeFS (`installer-test-win\build\extract\ExeFS`); the old one is kept as `program.m2.db`.

## 2. The exit criterion and how it was measured

**Reference runs.** `research/mac-traces/titledrive.py` (new, a copy of `tracedrive.py` without the input) boots `~/installer-test/build201-clean`
(read only) with `~/nxnative-trace`'s host. It leaves the game alone for 75–100 s and takes screenshots at 20, 30, 45 and 60 s. The run folders are on the
Mac under `~/setup/m4title/` and copied to `_local/mac-traces/m4title-*`:

| run | what | for |
|---|---|---|
| `cov-run4` | run-4 save; coverage traps (`DM_TRACE_COV`) and `DM_FS=1` | the function sets and their first-run times; files |
| `plain-run4` | run-4 save; `DM_FS=1` only, uninstrumented | files, frame rate, CPU |
| `cov-empty` | empty save (the language screen); coverage and `DM_FS` | a second scene to check against |

`title30.png` of `cov-run4` is the title screen ("PRESS A BUTTON", Ver. 2.0.1); at 20 s the screen is still black.

**Windows runs.** Untraced (`m4final-plain`, 130 s) for the frame rate and CPU; with `NXRT_TRACE=first:400000 DM_FS=1` (`m4final-trace`,
`m4final-trace2`, 100 s each) for functions and files. The trace lines now carry `ms=`, the time of each first entry. `research/mac-traces/title_compare.py`
compares a Windows log with a Mac run (functions per module, the title-scene set, files in order of first open, opens per file).

### 2.1 Steady presents: met

`m4final-plain`:

- 7,500 presents in 129 s, with no abort or hang.
- From 24 s (the title loop) to 129 s, every 300-frame window ran at 59.88–59.92 presents a second.
- The present interval was p50 16.54–16.74 ms, p95 17.37–17.46 ms, p99 17.4–17.9 ms, max 17.6–19.3 ms.
- In each window, 0–1 vsyncs showed no new frame, and acquiring a texture waited 0–10 ms in all.

The loading screens before 24 s have the Mac's kind of hitches: windows at 44 and 51 presents a second, single frames up to 0.85 s.

### 2.2 The title scene's work: 1,417 of 1,419

The set is what the Mac first ran between 18 s and the end of the run (the title screen appears between 18 and 20 s on the traced Mac; nothing new
runs after 27.5 s). Earlier functions are counted as boot and loading.

| `m4final-trace2` vs `cov-run4` | Mac | Windows | both |
|---|---:|---:|---:|
| main, boot and loading | 28,313 | – | 28,313 |
| main, title scene | 1,419 | – | **1,417** |
| main, all | 29,732 | 29,752 | 29,730 |
| sdk | 512 | 512 | 512 |
| subsdk0 | 3 | 3 | 3 |

**The two missing functions.** `main+0x1144990` and `main+0x17ab614`; the Mac first runs them at 22.0 s. The Mac's br/blr log shows `main+0x17ab614` called
from inside `main+0x1144990` every ~4.8 s. `main+0x1144990` sits next to the game's `nvnCommandBufferSetRecordingMode` call site: it is the command
buffer's out-of-memory callback. nxnative's recorder fills command memory with records and calls it when a segment runs out. The null device records
nothing (an empty recording is its 8-byte END), so the callback never fires. It comes with NVN command recording (§6).

**Windows-only functions.** Of the 22 functions Windows ran that this Mac run did not, 21 ran in research/05's `pilot` or `fullb`. The last one,
`main+0x11b54c0`, is in the sound mixer at 1.23 s, while the loader is busiest. It ran in one Windows run of three and looks like a timing-dependent path.

**The language screen (empty save) checks the same way.** Against `cov-empty` (an earlier build, run `m4r2`): main 32,971 of 32,973, and the
scene's 451 functions all ran. The two missing are the same callback. The extras are a contended `__cxa_guard_acquire` wait in sdk and one main function.

### 2.3 The same game files: met

| | files | in order of first open | opens | last open |
|---|---:|---|---:|---:|
| Mac, uninstrumented (`plain-run4`) | 30 | – | 6,947 | 20.0 s |
| Windows (`m4final-trace`, `m4final-trace2`) | 30 | **identical** | 6,915 / 6,934 | 24.8–24.9 s |
| Mac, traced (`cov-run4`) | 30 | differs: #23 and #24 swapped | 6,950 | 19.7 s |

The traced Mac run's swap is two archives two loader threads open at the same moment. `rom:/LD_C0_R.BIN` (6,754 opens) and 21 other files are opened
the same number of times on every run. Eight streaming archives are re-opened a varying number of times, on both machines:

| file | Mac traced | Mac plain | Windows | Windows | Windows |
|---|---:|---:|---:|---:|---:|
| 0x05d525ae.fdata | 36 | 33 | 30 | 31 | 34 |
| 0x38f972af.fdata | 29 | 29 | 29 | 28 | 28 |
| 0x79c5b922.fdata | 26 | 26 | 14 | 22 | 16 |
| 0x738d2a35.fdata | 16 | 15 | 11 | 14 | 11 |
| 0x57e5b452.fdata | 29 | 30 | 26 | 31 | 29 |
| MountPoint_2/0xcebd4bd4.fdata | 8 | 7 | 3 | 3 | 4 |
| MountPoint_1/0x99d71093.fdata | 2 | 2 | 1 | 1 | 1 |
| MountPoint_2/0xe8fc91c2.fdata | 5 | 6 | 2 | 5 | 6 |

Windows re-opens three of them a little less often. A null GPU takes no time, so the loader threads interleave differently.

## 3. Timings and performance (i9-13900KF; the game process at below-normal priority)

| | Windows | Mac |
|---|---:|---:|
| process start to `nnMain` | 0.43 s | |
| first frame request | 0.62 s | |
| audio open / save mount / profanity filter | 1.10 / 1.25 / 1.29 s | |
| loading done (last RomFS open) | 24.8 s | 20.0 s (uninstrumented) |
| title loop from | frame 1,200 (24.0 s) | frame 1,200 |
| frame interval at the title, p50 / p95 | 16.6 / 17.4 ms | 16.5–16.9 / 17.5–18.2 ms ("frame time") |
| CPU at the title | 0.30 of a core | 0.27–0.43 of an M4 core |
| CPU with `NXRT_TRACE` and `DM_FS` (whole run) | 0.57 of a core | |
| working set | 4.38 GB | 2.2–5.0 GB resident |

**The loading phase takes about 4–5 s longer than on the Mac.** It is CPU work in the recompiled code (decompression, asset parsing), which has had no
performance work yet (volatile guest memory accesses, no cross-function inlining: M6). The frame interval is set by the game's own pacing.

**Builds.** nxrt alone rebuilds in about a minute at 4 jobs. The game DLL was not rebuilt: 2.0.1's program.db is unchanged, and the DLL imports adapters by
name. A full tree build (`build\m4a`, 130 steps, LLVM included) completed at 4 jobs.

**Tests.** The suite is 21 tests, all passing (14 s): M3's 18, plus `nxanalyze.takeover`, `nxrt.audio` and `nxrt.save`, and `nxrt.nvn` is extended.

## 4. The build-time takeover check

`check_takeovers` (`src/nxanalyze/src/takeover.cpp`) and `nxanalyze --check-takeovers <program.db> <hle_inventory.tsv>` compare, for every symbol the
modules import or export:

- **Forced.** program.db's forced binding must agree with the runtime registry's forced entries (`src/nxrt/gen/out/hle_inventory.tsv`, `gen_hle.py`'s output).
- **swhle.** A swhle binding must agree with the registry's swhle entries, and a named stub must not be something the registry implements.

Every difference is listed, and the exit code is 1.

The test `nxanalyze.takeover` runs synthetic cases (the M3 nv:: case among them) and then every game program.db under `_local\analyze` (2.0.1 and 1.0.5).
Both pass: 2.0.1 has 1,279 forced bindings over 919 names, and 1.0.5 has 1,277. It would have failed M3 on the nv:: and vi:: take-overs.

## 5. Atomic saves (DESIGN D13)

`hle/save_txn.c` replaces the three-rename swap with a protocol a later start can finish or undo:

1. Copy the work directory to `<save>.new` and flush every file (`FlushFileBuffers`).
2. Write the commit marker `<save>.commit` (write-through, flushed). From here `.new` is complete.
3. Rename `<save>` to `<save>.old` and `.new` to `<save>` (`MoveFileEx`, write-through).
4. Delete the marker, then `.old`.

`save_txn_recover`, which runs before a save is mounted:

- **With the marker:** it finishes the commit, whatever step it stopped at.
- **Without it:** it deletes an incomplete `.new` and puts back a lone `.old`.

The layout under `<save>` is the game's own (`B00` / `G00` / `S00`), and the guest-name streams come along.

**`nxrt.save`** kills a child process at every step of a commit (after each file of the copy, after the flush, the marker, each rename, the marker's
deletion, the end), recovers, and compares the save with both versions:

```
copied:1..4, flushed    → version A (old), recovery 2
marker, renamed_old, renamed_new → version B (new), recovery 1
unmarked, done, no kill → version B (new), recovery 0
11 kill points: 5 left the old version, 6 the new one, none a mix
```

It also checks that nothing is left beside the save, that recovery is idempotent, that a recovered save takes the next commit, and that the guest-name
stream survives. `hle_fs.c` keeps nxnative's work-directory scheme; only its commit and its mount-time recovery changed. The work directory's own re-clone
still uses `renamex_np`'s three renames, which is safe because a start rebuilds the work directory.

## 6. Other tests added

- **`nxrt.audio`:**
  - the silent sink alone pulls 200 times a second (198.8 measured);
  - a frozen guest clock holds it (0 pulls in 0.3 s), with no catch-up afterwards;
  - a stereo output through hle_audio.c's queues, with four 5 ms buffers kept queued, is released at 47,997 frames/s, in order;
  - a six-channel one at 47,988.
- **`nxrt.nvn` (extended):**
  - a recorded fence, event signal and counter report take effect at the submit and not before;
  - texture-view getters and the view offset;
  - `ReadTexels` gives zeros;
  - 120 frames from an unthrottled producer through a two-texture swap chain take 1.99 s (60.2 frames/s), with the textures in turn and 120 guest frames ticked.
- **`nxrt.adapters`** now expects 973 forced entries.

## 7. Next blockers and open questions

1. **NVN command recording behind the null device** (M3 report item 7). The command-memory callback (§2.2) is the only title-scene difference left, and
   M5's Vulkan replay needs the records anyway. nxnative's recorder is most of `hle_nvn.c`, but it calls into the Metal backend for statistics, pacing and
   uploads (about 50 `mtl_*` entry points), so it is not a drop-in port.
2. **A clean-room decision is needed for texel swizzling.** nxnative's `dm/gfx/tex/nvn_deswizzle.c` (`ReadTexels` / `WriteTexels`, and M5's uploads) has a
   Switch 2 GOB order whose comment says it was taken from Eden's source code ("Eden-NS2 video_core/textures/decoders.cpp NativeGobOffset; confirmed on the
   UI sprites of Pokopia by brute force"). I did not port it. Either it is re-derived independently (for example by brute force on Pokopia's packaged
   sprites, as the comment suggests was confirmed), or you decide that nxnative's version may be used.
3. **Take-overs Pokopia references that are still stubs:** `hle_movie.c` (19), `hle_sf.c` (9) and `hle_swkbd.c` (1). None of them ran on the Mac up to
   gameplay. `hle_sf.c`'s generated stubs would return success with a zero handle if called; port it before relying on paths that connect to services.
4. **Two Switch 2 NVN entry points without a known signature** (`nvnCommandBufferSetRecordingMode`, `nvnQueueBuilderSetPrimaryQueue`) stay neutral stubs;
   539 NVN names in all are generated stubs (draws and state, no effect without a GPU).
5. **Input (M6):** pressing A at the title. hid's scripted input (`DM_KEYS`, `DM_CTL`) now has a ticking guest frame to count on.
6. **Performance:** the loading phase (§3), and the CPU cost of exact short sleeps (about 0.1 of a core).
7. **The save mount's read path** was not exercised by a commit at the title (the game saves only after the title). The kill tests cover the commit; a
   save-and-reload in M6 will cover the rest.

## 8. Reproducing

```
scripts\build.cmd                         (or a separate tree: cmake --build build\m4a -j 4, at low priority)
build\m4a\bin\nxrt.exe <ExeFS> --dll _local\nxlift\m3\game.dll --romfs <RomFS.img> --save <a copy of _local\saves\run4\save> --temp <dir> --log <dir>
    NXRT_TRACE=first:400000 DM_FS=1       function entries (with ms=) and RomFS opens, for the comparison
py -3 -I research\mac-traces\title_compare.py _local\mac-traces\m4title-cov-run4 <log> --mac-also _local\mac-traces\pilot --mac-also _local\mac-traces\fullb
build\m4a\bin\nxanalyze.exe --check-takeovers _local\analyze\2.0.1\program.db src\nxrt\gen\out\hle_inventory.tsv
Mac: python3 ~/setup/m4title/titledrive.py ~/installer-test/build201-clean <tag> --host ~/nxnative-trace/dm/host/dmhost --seconds 75 [--empty-save]
     [--env DM_TRACE=1 --env DM_TRACE_COV=$HOME/setup/traces/input] --env DM_FS=1, then research/mac-traces/analyze.py on the copied run folder
```

Windows run folders are under `_local\nxrt-run\` (`m4final-plain`, `m4final-trace`, `m4final-trace2`; the stops in `m4r1`–`m4r12`).
