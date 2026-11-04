# M4: the Mac swap-in harness (D15.2)

Status, 2026-11-04: **working.** The lifter's code, compiled for arm64, runs inside the real game on the Mac under nxnative, any
chosen set of functions at a time, with everything else native.

- **The boot path** (37,539 of research/05's 37,540 executed functions; one holds an nxnative observation gate) runs lifted
  through the title screen, the save load, the menus and a minute of play, in every run. The log matches the native run's line
  for line except two lines that name a caller's return address, the screenshots show the same scenes, and 99.8 % of the main
  thread's first entries come in the native order.
- **Everything the lifter produces** runs too: all 149,688 functions of main, and then all 200,406 functions of main, sdk and
  subsdk0 (all but eight), reach gameplay with the same results.
- **Divergences: none from the lifter or the semantics** (no trap, no crash, no log line of its own). The differences left are
  the game's own timing-dependent paths, each of which also occurs in some native run (§4.3); four bugs of the harness itself
  were found and fixed on the way (§4.2).

## 1. Design

```
 the game under nxnative (a copy: ~/nxnative-swapin)            swapin.dylib (built by src/swapin/tools/build_dylib.sh)
 ┌──────────────────────────────────────────────┐             ┌──────────────────────────────────────────────────────┐
 │ main / subsdk0 / sdk, rewritten by nxnative,  │  B thunk    │ thunk (48 B, near the module) → nxw_swapin_entry      │
 │ mapped at the bases the code was lifted for  ├────────────►│   saves x0-x30, sp, q0-q31, nzcv, fpcr, fpsr, x18 and  │
 │   swapped function: entry word = B thunk     │             │   tpidr* (from nxnative's thread slots) → NxwGuestCpu  │
 │   others: unchanged, native                  │◄────────────┤   fn_<addr>(cpu) on the thread's host stack            │
 │                                              │  ret to x30 │   loads the registers back, returns to the guest's x30 │
 │ nxnative's host: SVCs, HLE, NVN, threads      │             │ fn_<addr> ──lookup──► fn_<other>   (swapped: table hit)│
 │                                              │◄────────────┤          └─rt_miss──► nxw_swapin_call_native (native) │
 └──────────────────────────────────────────────┘ native call │ rt_svc / rt_mrs / rt_yield → nxnative's own handlers   │
                                                              └──────────────────────────────────────────────────────┘
```

### 1.1 nxlift for arm64

The lifter takes its target from the semantics bitcode's triple: on the Mac, `src/sem` compiles the same sources with
`--target=arm64-apple-macos13.0 -mcpu=apple-m1` (`NXW_SEM_TARGET`, `NXW_SEM_ARCH`), and nxlift links LLVM's AArch64 back end
(`NXW_LIFT_AARCH64`) and writes Mach-O `.o` files. Every lifted function is still `void fn_<addr>(NxwGuestCpu*)`; nothing in the
ABI changed. Three options make the swap-in build:

- **`--swapin`** (`Options::swapin`): every direct call and direct tail call goes to its target *as the guest branched to it*
  (a PLT stub stays a stub) through `nxw_sem_lookup`, instead of a direct call to the bound function or an HLE adapter. Whether a
  callee runs lifted or native is then the runtime's decision, made per run from a list, so one dylib serves every subset and a
  bisection never re-lifts. It also writes `nxw_swapin_table.o` ({guest address, `fn_<addr>`, program.db flags, size} per
  function, the module bases and main's build ID).
- **`--base-of M=ADDR`**: program.db's records are module-relative, so the modules can be placed elsewhere than nxload's packed
  layout. nxnative's rewritten images are larger than the originals (its patch areas follow each image), so the packed layout's
  modules would overlap there.
- **`--fn-list FILE`**: the functions to lift, as `module offset` lines (research/05's executed lists).

On Darwin the runtime's data symbols (`rt_code_table` and the others) are hidden and DSO-local, so lifted code reaches them
without GOT loads. The rest of the shared tree builds on the Mac too (CommonCrypto for nxload's SHA-256, portable `nxlift` and
`nxload-info` front ends, Mach-O symbol names in `gen_ops.py`): there 7 of the 8 ctest tests pass with the dump, `interp.kat`
(297 known answers, the semantics compiled natively for arm64) among them; `a64-xcheck-patterns` wants the Windows
`llvm-objdump.exe`. On Windows `scripts\build.cmd` builds the whole tree and all 21 ctest tests pass (with `NXW_DUMP`).

### 1.2 The runtime (src/swapin)

| piece | file | what |
|---|---|---|
| thunk | `swapin_entry.S` | 16 bytes of code + {fn, entry, guest address}: saves x16/x17 below the guest's sp (free at a function entry), jumps to the entry |
| `nxw_swapin_entry` | `swapin_entry.S` | the native → lifted direction: every register into the thread's `NxwGuestCpu`, then `fn_<addr>` on the thread's host stack (16 MB, made at the thread's first entry), then everything back and `ret` |
| `nxw_swapin_call_native` | `swapin_entry.S` | the lifted → native direction: cpu's registers into the hardware ones, the guest's sp, x30 = a return trampoline, `br` to the target; on return everything back into cpu, x30 restored to its value at the call (what the guest sees after a return). Nested entries (native code calling back into lifted code) build their host frames below this one |
| `rt_miss` | `swapin_rt.c` | a table miss runs natively, except a PLT stub whose GOT slot holds a swapped function: that runs lifted, with x16/x17 as the stub leaves them |
| `rt_svc` | `swapin_rt.c` | nxnative's `svc_dispatch` with cpu as the 800-byte frame, inside `thr_svc_enter`/`thr_svc_leave` (the frame is a paused thread's context), as the gate does |
| `rt_mrs` / `rt_msr` | `swapin_rt.c` | cntpct/cntvct: `guest_clock_read_wait` + `guest_ticks` (the counter gate's work); FPCR writes go to the hardware register too; anything else traps |
| `rt_yield` | `swapin_rt.c` | spin-loop heads: nxnative's spin gate (`dm_spin_wait` inside `thr_svc_enter`/`leave`); YIELD/WFE: a `yield` |
| `rt_trap`, `rt_nonlocal` | `swapin_rt.c` | a report (registers, guest backtrace, the lifted function) and an abort |
| `rt_trace_entry` | `swapin_rt.c` | first entry of each lifted function into the trace module's coverage log (kind 3) |
| `nxw_swapin_init` | `swapin_rt.c` | checks the bases, reads `DM_SWAPIN_LIST`, writes the thunks and the entries' branches (through a writable alias of the code), fills the D4 table |

**nxnative's rewriting.** nxnative moved the guest's x18, tpidr_el0 and tpidrro_el0 into pthread slots (the rewriter's K18, KTLS
and KTLR; macOS owns the registers). The entry reads them from the slots into cpu; every way out of lifted code (return to
native, native call, SVC, spin gate) writes x18 and tpidr_el0 back. The thunk and the transitions never touch the hardware x18.
The lifter lifts the *original* modules; the bytes in memory are nxnative's (rewritten SVC, counter, x18, TLS and spin sites), and
the lifted code does what those sites originally did through the runtime calls above. Functions are swapped whole, so the
rewritten sites inside a swapped function never run.

**What is never swapped** (the runtime refuses, and says so): the four SP/x30 rewriters (program.db's context flags:
`DispatchFiberContext`, `UserExceptionHandler`, `longjmp`, libunwind's `jumpto`), whose lifted form is `rt_nonlocal`; and the
functions holding one of nxnative's observation gates (`<module>.obs.tsv`: sdk's `VammManager::AllocateAddressRegion` and
`FreeAddressRegion`, `StandardAllocator::Initialize` ×2), whose host callbacks only the native code reaches. Of the boot path
that is one function, sdk+0x13f634.

**Exceptions and longjmp.** None ran in normal play (research/05) and none ran here. A C++ throw or a longjmp that crossed lifted
frames would unwind native frames only: the four functions that move SP or x30 stay native, and if lifted code ever reached
`rt_nonlocal` it would stop with a report rather than corrupt the host stack.

### 1.3 The nxnative copy

`src/swapin/nxnative/apply_swapin_patch.py` turns a copy of nxnative into the swap-in host:

- `hle_swapin.c`: maps each module at its `DM_SWAPIN_BASES` address (`mach_vm_allocate`, fixed), loads `DM_SWAPIN_LIB` and hands it
  nxnative's handlers through `NxwSwapinHost` (`swapin.h`: the dylib links against nothing of nxnative's); `DM_SWAPIN_SHOTS`
  takes screenshots at exact frame numbers;
- research/05's trace module (`hle_trace.c`, unchanged hooks) with two additions: `trace_cov_note` (lifted first entries) and
  `DM_TRACE_COV_ENTRIES=1` (function starts only);
- loader.c, main.c, hle_os.c, crash.c, hle.c and hle_fs.c: one hook each (hle_fs.c's `DM_SWAPIN_FS_DELAY_US` slows every
  ReadFile, a control for timing experiments).

### 1.4 Where things live in memory

On macOS 27 every process starts with `[0xfc0000000, 0x7000000000)` reserved and two 12 GB reservations at a random place in
`[0x7180000000, 0x8000000000)` (200 samples: starts from 0x7184… to 0x79fc…). `[0x7000000000, 0x7180000000)` is the one window
below 2^39 that is always free, and nxnative's 11 GB guest block goes to the first fit from 0x7000000000. So:

| what | where |
|---|---|
| main / subsdk0 / sdk (rewritten images, with their patch areas) | 0x7000000000 / 0x7005000000 / 0x7006000000 |
| thunks | in the gaps between the modules (never past sdk's end) |
| nxnative's guest block (heap, alias, stacks) | first fit after sdk, as before (0x70070b8000 when the random reservations leave room) |
| the D4 table, the first-entry bitmap, host stacks and thread states | from 0x8000000000 up (the host's own; the guest never sees them) |

The last two rows matter: the runtime's own allocations first went next to the modules or into malloc, which moved the guest
block (and, through nxnative's malloc'd TLS blocks and thread objects, host memory the guest sees) against the native run (§4.2).
The guest block itself still lands elsewhere when the random reservations take its first fit (0x7802000000 and 0x79775e8000
in two of the native runs), as it does under nxnative itself.

## 2. How to run it

On the PC (the source of truth; nothing is committed by these steps):

```
tar cf - CMakeLists.txt src research/mac-traces | ssh mac 'mkdir -p ~/swapin/windows-native && cd ~/swapin/windows-native && tar xf -'
```

On the Mac (`~/swapin` holds everything of ours; `~/toolchains/llvm-23.1.4` the LLVM release):

```
# the tools, natively (cmake and ninja from pip in ~/swapin/venv, LLVM 23.1.4's clang)
cmake -S ~/swapin/windows-native -B ~/swapin/windows-native/build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_COMPILER=~/toolchains/llvm-23.1.4/bin/clang -DCMAKE_CXX_COMPILER=~/toolchains/llvm-23.1.4/bin/clang++ \
      -DNXW_LLVM_DIR=~/toolchains/llvm-23.1.4 -DCMAKE_OSX_SYSROOT=$(xcrun --show-sdk-path) -DPython3_EXECUTABLE=~/swapin/venv/bin/python3
cmake --build ~/swapin/windows-native/build -j 10
# the host
rsync -a --exclude '*.o' ~/nxnative/ ~/nxnative-swapin/
python3 ~/swapin/windows-native/src/swapin/nxnative/apply_swapin_patch.py ~/nxnative-swapin ~/swapin/windows-native
cd ~/nxnative-swapin && DM_GAMES_DIR=~/installer-test/build201-clean/profile make -C dm/host GAME=0400A3A01EE4C000 dmhost -j10
# the dylibs (program.db copied from the PC's _local/analyze/2.0.1; the boot list from research/05's executed lists)
build_dylib.sh ~/swapin/work/boot --fn-list ~/swapin/lists/boot.list     # 37,540 functions, 74 s
build_dylib.sh ~/swapin/work/all                                          # 200,414 functions, 317 s
# runs: native (nothing swapped), a set swapped in, a comparison, a bisection
swapin_drive.py run nat --trace
swapin_drive.py run boot --trace --list lists/boot.list --lib work/boot/swapin.dylib
swapin_drive.py compare boot nat
swapin_bisect.py bis --list lists/boot.list --lib work/boot/swapin.dylib --fail entry:main+ec8200 --until loaded
```

A run boots a fresh copy of `~/setup/save_run4` on build201-clean (read-only), plays research/05's route a (title, eight A
presses, the save, the menus, a minute of play) and takes screenshots at the four checkpoints; `--det` plays the keys and takes
the screenshots at fixed frame numbers instead.

## 3. Results

### 3.1 The lifts

| dylib | functions | instructions | lift + optimise + codegen (10 jobs, M4) | Mach-O objects | dylib |
|---|---:|---:|---:|---:|---:|
| `work/boot` (research/05's executed lists, pilot ∪ fullb) | 37,540 | 2,616,656 | 74 s | 70.5 MB | 61.8 MB |
| `work/all` (every function of main, sdk and subsdk0) | 200,414 | 12,360,627 | 317 s | 353.6 MB | 305 MB |

0 instructions without semantics. In swap-in mode the whole program has 683,621 calls and 74,391 tail calls through the lookup,
6,957 jump tables, 57 SVCs, 45 runtime MRS/MSR, 120 spin-loop yields and the 4 context functions.

### 3.2 The runs

Every run: a fresh copy of the run-4 save, research/05's route a (40 s of boot, eight A presses through the title screen and
the save load, the menus, a minute of play), 156 s, screenshots at the four checkpoints, coverage traps on every function start
plus the lifted functions' own first entries. "Native" runs use the same host, bases and dylib with nothing swapped in.

| run | swapped in | checkpoints | traps / crashes | frames in 156 s | frame 900 at |
|---|---:|---|---|---:|---:|
| native ×4 (nat1, nat2, nat3, nat5) | 0 | title, loaded, menus, play | none | 8,700 | 17.8 s |
| boot path ×4 (boot2, boot3, boot4, boot9) | 37,539 | title, loaded, menus, play | none | 8,400–8,700 | 18.8–22.0 s |
| boot path (boot8, to the save only) | 37,539 | title, loaded | none | – | 19.6 s |
| whole main (main1) | 149,688 | title, loaded, menus, play | none | 8,100 | 20.1 s |
| everything (all1) | 200,406 | title, loaded, menus, play | none | 7,800 | 19.1 s |

The lifted functions really ran: in boot2, 36,685 of the 37,056 first entries were the lifted code's own (kind 3); 371 were
native functions outside the list. Swapped-in code costs time: the boot's loading screen runs 1–2 s late (frame 900 at
18.8–19.6 s against 17.8 s, after the PLT shortcut of §4.2); in play the frame time stays at the 16.7 ms vsync with the boot
set, and rises to a median of 18.4–19.7 ms with all of main and 17–23 ms with everything. Lifted code makes about 400,000
native calls a second (32.0 M in boot8's 81 s, almost all into nxnative's HLE and NVN host functions), each a full transition
(§1.2).

### 3.3 Against the native runs

`swapin_drive.py compare` (the log with host statistics, addresses and times masked: 124 lines a run; the screenshots' mean
absolute difference at 480×270, 0–255; first entries up to the shorter run's end):

| run vs reference | log lines differing | title | loaded | menus | play | main thread's first entries in native order |
|---|---|---:|---:|---:|---:|---:|
| nat2 vs nat1 | 0 | 0.08 | 1.14 | 0.84 | 5.22 | 20,748 / 20,749 |
| nat3 vs nat1 | 0 | 0.08 | 1.85 | 0.69 | 5.87 | 20,736 / 20,749 |
| nat5 vs nat1 | 0 | 0.04 | 0.61 | 0.91 | 16.23 | 20,748 / 20,749 |
| boot4 vs nat5 | 2 | 1.57 | 2.63 | 2.48 | 8.12 | 20,704 / 20,749 |
| main1 vs nat5 | 2 | 1.17 | 1.40 | 3.67 | 14.41 | 20,705 / 20,749 |
| all1 vs nat5 | 2 | 1.59 | 2.47 | 3.81 | 13.38 | 20,703 / 20,747 |

- **The log** matches line for line (SDK start-up, the forced take-overs, module inits, the RomFS and save mounts, the NVN
  device, swap chain and pools, the audio device, hid, the time zone): only `audio #0 OpenAudioOut … (call site host)` and
  `audio #0 Start (call site host)` differ, where the native run prints `call site main+0x…` (§4.1).
- **The screenshots** show the same scenes at every checkpoint (the same dialogue after the save load, the same spot and camera
  after the play route); what differs is animation phase, which also differs between native runs (play: 5–16 natively).
  The swap-in values are higher at title and loaded because the loading screen and its animation run 1–2 s late.
- **First entries.** The swap-in runs enter 21–22 function starts no native run entered and miss 24–27 that all four native runs
  entered; all of them are the branches of §4.3. (main1 and all1 also enter 228 and 478 functions that are not `.eh_frame`
  starts, program.db's extra function starts, which the native trace cannot see: the coverage traps sit on FDE starts only.)

## 4. Divergences found, and their causes

No divergence came from the lifter or the semantics: nothing trapped, nothing crashed, and every difference that is left traces
to the game's own timing-dependent paths or to the harness, as follows.

### 4.1 The caller's return address (by construction; kept)

A native function called from lifted code sees x30 = the runtime's return trampoline, not the guest's `bl` site, because the
native code must come back to the runtime. nxnative's audio take-overs log their caller from x30, so `OpenAudioOut` and
`Start` print `call site host` instead of `call site main+0x11dc264` / `+0x11dc45c`. Those are the only two log lines that differ.
On Windows the same calls go through generated adapters, so this is a swap-in artifact, not a port issue.

### 4.2 The harness's own bugs (fixed)

| found by | cause | fix |
|---|---|---|
| the module mapping failed (`KERN_NO_SPACE`) in some runs | macOS 27 reserves `[0xfc0000000, 0x7000000000)` and two randomly placed 12 GB regions above 0x7180000000 in every process; a base inside them works only when the dice allow | the modules at 0x7000000000 / 0x7005000000 / 0x7006000000, the one window below 2^39 that is always free (§1.4) |
| the swap-in runs' guest block sat 32 KB higher than the native runs' (the `guest AS` line) | the sdk thunk pages, placed right after sdk, took the first fit where nxnative puts its guest block, moving every guest heap address (the game keys hash tables by pointer, so this alone can change its behaviour) | thunks in the gaps between the modules; the runtime's tables, host stacks and thread states from 0x8000000000 up, out of both the guest block's way and the host's malloc heap (which holds nxnative's TLS blocks and thread objects); native runs load the same dylib with nothing swapped |
| the boot was seconds late; `sample` put half the busy time in the transitions | every call through a PLT stub (main → sdk, sdk → main) went native and came back into lifted code: two full transitions per cross-module call | `rt_miss` decodes a PLT stub and, when its GOT slot holds a swapped function, runs it lifted with x16/x17 as the stub would leave them (the boot's longest frame went from 3.2 s to 1.3 s) |
| a lighter transition for calls into host code (only x0–x8/q0–q7 moved) crashed in the NVN layer (`cmd_alloc_keep`, a null command buffer) | a host function that calls back into swapped code re-enters through the thunk, which fills the thread's one `NxwGuestCpu`; the light path then returned with the callback's x9–x30 in cpu | withdrawn: every native call takes the full transition, which saves every register on the way back |

### 4.3 The game's own timing-dependent paths (not lifting bugs)

**The main thread's order at 0.95 s.** The main thread's first entries split at index 9,284, right after main+0x2674b20 (a
behaviour-tree style evaluator): one variant enters main+0x1ff6c60 … +0x21070c0 and then +0xca7a9c, the other +0xca7a9c and
+0x2674db8 first. Both are native: nat1, nat2 and nat5 take the first, nat3 and a short native run (bis3-b001) the second, and
every swap-in run the second. After the split 99.8 % of the main thread's first entries keep the native order (natives among
themselves: 99.94–99.995 %). A bisection on the order (`--fail before:main+ca7a9c:main+1ff6c60`, 5-second runs) stopped at
its first check: the native run already showed it.

**A random choice at the title screen (48 s).** At about 48 s a selector (main+0xec7620) is called with `mode = rng % 3` from the
game's global random state (one u32 behind a GOT slot, updated without a lock by whatever thread calls it). The mode orders three
pickers (main+0xec7820, +0xec7c00, +0xec8200) that draw a 16-bit id from a table with the same generator. Native runs took mode 0
(+0xec7820, then +0xec8800 …); swap-in runs mostly took a mode that runs +0xec8200 first (19 functions on threads 4161/4097
that no native run of ours entered, 11 that every one did). Why this is the game and not the lifter:

- research/05's `fullb` run, the native game under heavy instrumentation (coverage traps on every word, br/blr emulation, a
  1 kHz sampler), entered +0xec8200 at 48.4 s: the "swap-in" side, natively. That is why +0xec8200 is in the boot list at all.
- It is not deterministic under swap-in either: boot8 took the native side.
- It is not one function: the bisection (`swapin_bisect.py --fail entry:main+ec8200`, runs bis1 and bis2) found each half of
  the boot set (18,770 functions) on the native side, twice, and one half plus a quarter (28,155) on the swap-in side: the
  signature of an effect that grows with the amount of swapped-in (slower) code, not of a culprit. It was stopped there.
- Native timing changes of other kinds did not move it (A presses ±3 s, eight CPU hogs, 0.3–0.8 ms per file read). Most likely
  the random state at that moment depends on how many draws the job threads make while the loading screen runs, which a slower
  CPU side (lifted code, or research/05's traps) changes and slower I/O does not.

**What follows from it (111 s).** 12 functions (main+0x1a59cf4…0x1aa7544, +0x71b960) that every native run enters in the play
phase at 111 s were never entered by a swap-in run; research/05's `pilot` (native side at 48 s) has all 12, `fullb` (swap-in
side, another route) none. The play screenshots show the same place and camera in all runs.

One more first entry, main+0xe8754 at 121 s in main1 and all1, is one the native game makes too: research/05's `pilot` entered
it at 158 s on the same route.

**Contention paths.** `nn::nlibsdk::heap::TlsHeapCentral::LockMutexSlow_` (sdk+0x137860, all1 at 1.2 s) and
`nn::os::SdkConditionVariableType::Wait` (sdk+0x14780c, 52 s in every swap-in run) are the slow paths of a heap lock and a
condition wait: they run when another thread holds the lock or the condition is not ready yet, which slower code makes more
likely.

### 4.4 What a real divergence would have looked like

A trap (`rt_trap` prints the lifted function, the registers and the guest backtrace), a crash in a `fn_<addr>` frame, a log
line that no native run prints, a checkpoint not reached, or a first entry no native run makes that persists in every swap-in
run and bisects to one function. `swapin_bisect.py` takes any of these as its criterion (`crash`, `log:`, `nolog:`, `entry:`,
`noentry:`, `before:`, `shot:`), checks first that the native run passes and the full set fails, keeps one half swapped in
while it searches the other when neither half fails alone, and logs every step in `runs/<tag>.bisect.tsv`. Each step is one run
(about 95 s to the save, 5 s for something in the first seconds of the boot with `--until title --boot 4`), so a boot-set
bisection to one function takes 16–30 runs.

## 5. Limits

- **What the host is.** nxnative on an M4: the Mac's arm64 semantics (NaN propagation, FMA, the estimates) are the hardware's,
  so the swap-in checks control flow, integer and memory semantics, the lifter's model (calls, returns, tail calls, jump tables,
  the state at exits), the runtime contract and the semantics library as compiled for arm64. x86-specific risk (LLVM's x86 back
  end, the x86 lowering of FP corner cases, the default NaN) is D15.1's job, not this one's.
- **Only what runs is checked.** The boot route executes about 37,500 functions; the other 160,000 are swapped in but never
  entered (§3.2). A function that diverges on an input the route never gives it is not found.
- **The game is not deterministic on the Mac.** Thread interleaving, I/O timing and the wall-clock input script make two native
  runs differ (§3.3), and nxnative's `DM_DETCLOCK` cannot pin Pokopia's clock (its boot and its frame limiter wait on the clock
  between presents, which a per-present clock never lets go on). The comparison is therefore against the spread of native runs,
  not bit for bit, and a divergence smaller than that spread is not seen.
- **Swapped-in code is slower** (each transition between native and lifted code copies the whole register file, both ways), so
  timing-sensitive behaviour shifts: the boot path's loading runs about 1–2 s late with every boot function swapped in.
- **Visible differences by construction**, all ABI-legal: a native function called from lifted code sees x30 = the runtime's
  return trampoline (nxnative's own log prints such call sites as "host"), x17 = its own address, and the 32 bytes below the
  guest's sp at a swapped function's entry hold the thunk's scratch registers. Code that reads its caller's return address for
  anything but returning, or reads stack it never wrote, can tell.
- **Not swapped**: the four SP/x30 rewriters (rt_nonlocal), the functions with nxnative observation gates (sdk+0x13f634 among the
  boot functions); PLT stubs are guest code but no functions.
- **Fibers, exceptions, longjmp** would cross lifted frames on the host stack incorrectly; none ran.
- **Memory digests** at sync points were not added: with the game's own nondeterminism, the heap differs between native runs.

## 6. Files

| file | what |
|---|---|
| `src/swapin/swapin.h` | the dylib ↔ nxnative contract (`NxwSwapinHost`, the table types) |
| `src/swapin/swapin_entry.S`, `swapin_rt.c` | the runtime (§1.2) |
| `src/swapin/CMakeLists.txt` | `nxw_swapin_rt` (Mac only; the top CMakeLists adds it on APPLE, and nxrt on WIN32 only) |
| `src/swapin/nxnative/hle_swapin.c`, `apply_swapin_patch.py` | the nxnative copy's side (§1.3) |
| `src/swapin/tools/build_dylib.sh` | lift + link a swap-in dylib |
| `src/swapin/tools/swapin_drive.py` | runs and comparisons |
| `src/swapin/tools/swapin_bisect.py` | bisection to one function |
| shared changes | `src/nxlift` (arm64 target, `--swapin`, `--base-of`, `--fn-list`, the table object), `src/sem/CMakeLists.txt` and `gen_ops.py` (the Mac target), `src/nxload` (SHA-256 on the Mac), `src/a64/tools/a64_xcheck.cpp` and `src/nxload/tools/nxload_info.cpp` (portable entry points), the top `CMakeLists.txt` |

Data, never committed: the Mac's `~/swapin/` (`data/` program.db and the executed lists, `work/` the dylibs, `runs/` every run,
`lists/`), `~/nxnative-swapin/`, `~/toolchains/llvm-23.1.4/`.
