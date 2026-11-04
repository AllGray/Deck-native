# 01: What goes wrong for pokopia-recomp (and nxrecomp)

Snapshot taken 2026-11-03, about 18:40 UTC, roughly 25 hours after pokopia-recomp's first public release.

Clean-room scope. Read: issues and their comments, pull request titles and descriptions (no diffs), release notes, Markdown docs with every fenced code block stripped before reading, the repository file listings (names only, to find the docs), and the `pkp.log` files users attached to issues. Not opened: any source file of pokopia-recomp, nxrecomp, MetroidPrimeRemasteredRecomp or any emulator. Some issue and PR texts name source files or quote a line of code. That text is part of the allowed material, but none of it is copied here.

Also read, because they hold most of the fixes for upstream issues: the issues and PR descriptions of the fork `featureJosh/pokopia-recomp`, and the issue tracker of `drakolordx7/MetroidPrimeRemasteredRecomp`, which nxrecomp names as the place for game bugs.

---

## 1. Snapshot: drakolordx7/pokopia-recomp

| Item | Value |
|---|---|
| Description | Pokopia (Switch 2) recompiled to run natively on Windows |
| Created / last push | 2026-11-02 03:10 UTC / 2026-11-02 17:25 UTC (the v0.1.1 release; nothing pushed since) |
| Licence | GPL-3.0 (built on drakolordx7/nxrecomp, also GPL-3.0) |
| Stars / forks / watchers | 228 / 20 / 3 |
| Issues | 10, all open, none closed, all labelled `bug` |
| Pull requests | 9: none merged, 5 open (#6, #9, #14, #15, #19), 4 closed unmerged (#5 accidental, #8, #11, #12) |
| Maintainer activity | drakolordx7 has not commented on any issue or PR and has merged nothing since the release |
| Most active fork | featureJosh/pokopia-recomp: 7 issues and 19 merged PRs in one day; it carries most fixes for the upstream issues |

### Releases

| Tag | Date (UTC) | Asset | Downloads | Notes |
|---|---|---|---|---|
| v0.1.0 (pre-release) | 2026-11-02 14:20 | PokopiaRecomp-0.1.0-win64.zip, 137 MB | 24 | First test build. Compile on the player's PC "about 7 minutes on 12 threads", then the shader cache. Tested through the opening and the first area only. |
| v0.1.1 | 2026-11-02 17:25 | PokopiaRecomp-0.1.1-win64.zip, 170 MB | 3,500 | Set up has to be rerun (shader cache changed). Set up takes 1 to 2 hours, mostly shaders, and can be paused. Fixes: shader sine and cosine were off by a factor of 2π (fog, haze, wind, water); mouse in the title, settings and dialog menus; mouse look no longer drops out about once a second; GPU results copied back only when the game reads them. Adds DLSS, DLAA and FSR 3 (FSR 3 bundled; the DLSS file is downloaded from NVIDIA through a launcher button). |

### Stated requirements

- **Dump:** the player's own extracted game, **version 2.0.1a** (their shader doc ties this to update 655360; their NVN2 doc gives a main build ID starting `76a489f3`, the same main that pokopia-native calls 2.0.1). Update 1.0.5 (v327680) is not supported.
- **Dump layout:** a folder with `base/{exefs,romfs}` and/or `updates/<version>/{exefs,romfs}`. The newest complete update is used, otherwise the base. The ExeFS is verified by hash before the build.
- **NUP:** not supported in v0.1.1 (issue #13). Upstream PR #15 unpacks a .nup to a folder; the fork can also read a stored (uncompressed) .nup directly.
- **OS / GPU / RAM:** Windows 10 or 11, 64-bit; a GPU with Vulkan 1.3; 16 GB of RAM. DLSS needs an RTX card and the NVIDIA file.
- **Disk:** not stated in the README. The launcher README gives about 10 GB of temporary shader corpus (deleted after setup) and about 26 GB of SPIR-V for the 99,012 shader blocks, on top of the built game and a BC7 texture cache.
- From a user's log: the runtime reserves a 12 GiB heap section by default, and its memory guard limit is 16 GB.

### Setup flow (README, launcher README, release notes)

1. Unzip and run `PokopiaLauncher.exe` (a Tauri 2 app). It writes `pkp.ini` next to the game exe; every path comes from that ini.
2. **Game files:** pick the dump folder.
3. **Set up** ("release model b": the zip contains no game code; the game is built on the player's PC):
   - verify the ExeFS by hash; lift the AArch64 modules to C++ (Python); compile with a bundled Zig/clang (284 objects); link against the runtime DLL. About 7 minutes on 12 threads.
   - shader extraction: every shader in the romfs and in `main`, 99,012 distinct code blocks (about 10 GB, temporary).
   - shader translation: a .NET tool (`spirv_harness`, the Ryujinx shader translator behind their own Ampere SASS decoder) in chunks of 1,000 blocks, about 26 GB of `.spv`. **1 to 2 hours**; about an hour on an 8-core/16-thread Ryzen (issue #1), 15 minutes with the .NET server GC.
   - Pause and Continue work at every step; the tools run at below-normal priority.
4. **Play.** F1 opens the settings; middle mouse toggles Joy-Con 2 mouse mode.

Not covered by setup: about 212 shaders the game builds while it runs (issue #2).

### Their architecture, as far as the docs say

- **CPU:** nxrecomp lifts each NSO (main, sdk, subsdk0, rtld) to C++ that clang-cl or Zig compiles for x64. It is checked against Unicorn instruction by instruction (565,061 differential tests). Translation coverage is 100% on Pokopia's main, sdk, subsdk0 and rtld. Documented gaps: FPCR is stored but not honoured (always round-to-nearest, no flush-to-zero, no default-NaN); FPSR sticky flags are not tracked; the exclusive monitor is a compare-and-swap (ABA is not detected); plain loads and stores are not ordered between barriers; PAC, BTI and MTE are no-ops; SVE is not lifted.
- **Runtime (nxrt):** guest memory at its guest address; the heap is one pagefile-backed section with alias views; one Win32 thread per guest thread, pinned to three "console cores" in strict priority bands; Horizon services are emulated at a high level; NVN2 runs on Vulkan, recorded on game threads and replayed on one render thread; memory pools are imported into Vulkan as host memory; ASTC textures are transcoded to BC7 and cached; SDL3 for the platform layer, XAudio2 for sound, libvpx for VP9 video.
- **Game hooks** live in a sites file for `main`. A new hook means lifting and compiling again on every player's PC.

---

## 2. Every issue

### 2a. Upstream issues (all 10)

| # | Title | State | Subsystem | Symptom | Hardware / GPU | Root cause or fix stated in the thread |
|---|---|---|---|---|---|---|
| 1 | Shader setup: spirv_harness runs ~4x faster with server GC | open | launcher/setup (performance) | Shader translation keeps only about 2 of 15 workers busy; the machine sits at about 10% CPU; setup takes about an hour | Ryzen 7 9700X, RTX 4070, Fedora 44 + Wine 11 | The .NET workstation GC stops every worker on each collection. `DOTNET_gcServer=1` cuts 600 blocks from 20.4 s to 5.3 s with byte-identical output, and the whole setup to about 15 minutes. Merged on the fork. |
| 2 | 212 shaders missing from the cache at boot (Ditto renders black) | open | shaders | After a complete setup, 212 shader blocks are missing at the first boot, all requested 8 to 10 s in; Ditto is black; a 10.6 s start freeze trips the watchdog | RTX 4070 under Wine; confirmed by others on Windows, including an RX 7900 XTX | The game builds these shaders at run time, so the romfs/main corpus never contains them. Workaround: turn on the runtime's missing-shader dump, boot past the title, translate the dumped blocks, merge them into the cache. Afterwards: 0 missing, Ditto correct, start freeze 2.2 s. Upstream PR #9 automates it with a 60 s headless warm-up run before the first Play. Several users confirm. |
| 3 | HADD2/HFMA2/HMUL2 `.H0_NH1` on the second operand is read as no swizzle (probably the missing sun and cloud shadows) | open | shaders | Expected: no object shadows, no cloud shadows, caves lit like outdoors (found by reading their code, not by running it) | none (filed by xFractals) | The decoder recognises the selector but the emitter falls through to "no swizzle", so the two half lanes cross in the sun-shadow mask. Fix (high lane = B.H0, low lane = −B.H1) merged on the fork. In #17, ENDU1997 confirms it restores the sun shadows, but only after the runtime-built shaders are retranslated too: about 45 of the 212 use `.H0_NH1`. |
| 4 | Compiling error in every file | open | launcher/setup (build toolchain) | During setup every C++ unit fails: the Zig/clang front end crashes with exception code 0x20474343 | RTX 3050 Laptop (4 GB), Windows 11 25H2 | No upstream reply. Fork PR #5: 0x20474343 is an uncaught C++ exception in clang (`std::bad_alloc`). One compile job per core runs a laptop out of memory, and then every unit fails. Fix: cap jobs at free RAM / 2 GiB; retry a failed unit alone. |
| 7 | Textures in black and white | open | GPU renderer (depth format), shaders | Textures broken or greyscale; log: texture 381 "view not possible" (the view format is incompatible with the image's) and the dummy view is bound | RX 6750 XT, Ryzen 5 5600X, 16 GB | AMD has no `D24_UNORM_S8_UINT`, so D24S8 images are created as `D32_SFLOAT_S8_UINT`, but sampled views still ask for D24S8, are refused, and get a dummy depth of 1.0 (PR #8, closed unmerged). vzeol (RX 7900 XTX) needed both PR #8 and the #2 workflow. chainblade131 (RX 7800 XT): afterwards the floor plane shows through everything and the sky is black; the screenshot is 2559x1428, so probably 1440p and the #17 viewport bug (my inference). |
| 10 | Textures in black and white | open | shaders | Greyscale or colourless world | RTX 3060 12 GB (driver 621.01); also reported on RX 9070 16 GB, RTX 5060 Ti 8 GB, RX 6650 XT | No reply from the maintainer. Both attached logs (RTX 3060 and RX 9070) contain 212 "no usable shader" lines, and the RX 9070 log also has a refused depth view. So the cause is #2 (plus #7 on AMD). vzeol's #2 workflow fixes it. |
| 13 | not accepting NUp format | open | dump formats | The launcher does not recognise a .nup package | Windows 11 | Upstream PR #15 adds a .nup button that unpacks to a folder (needs the space of the whole unpacked game; per-file hashes not checked). Fork PR #17 lets the runtime read a stored .nup directly and refuses zstd-compressed ones. |
| 16 | crashes after compiling | open | crashes (dump/version) | The base folder has no romfs, only the update does; setup eventually compiles; the game then dies | RTX 3080 (plus an AMD iGPU) | Not diagnosed in the thread. The attached log stops mid-line 2.7 s into boot, during audio output setup, with no crash report. The update folder is named `327680`, which is 1.0.5's version number, while only 2.0.1a is supported (my observation; whether the dump really is 1.0.5 is not stated, and the hash check should have refused a 1.0.5 main). |
| 17 | Shadows render wrong (mottled acne on surfaces): 12 ray-tracing NVN procs are logging stubs | open | GPU renderer (resolution) | A grey, mottled film fixed to world geometry; wrong shadow edges; at 2560x1440 | RTX 3060 Ti 8 GB, driver 32.0.16.2101, Windows 11 22H2, Ryzen 7 5700X, 32 GB | The reporter suspected the ray-tracing NVN stubs. ENDU1997 has the same stubs and correct shadows: rendering at exactly 1920x1080 removes the mottling, and the #3 fix (with the runtime-built shaders) restores the sun shadows. Root cause (fork PR #25): game code records many pass viewports as 1920x1080 and derives screen constants from that record, so above 1080p the AO composite covers 1.333x the target. Fix: a hook that rescales the recorded rects. It needs a re-lift. |
| 18 | Left stick never counts as a full push on a DualSense | open | input | With the stick fully pushed the character moves much slower than with the keyboard; changing the dead zone does not help | RTX 2070 Super, DualSense, Windows 11 | The pad tops out at about 0.96 and the runtime has an inner dead zone only, so the game never sees a full push. Workaround: DS4Windows with a 90% outer zone. Fix: `[pad] stick_max` outer zone (upstream PR #19, merged on the fork). |

### 2b. Bugs that show up only in upstream PRs

| PR | Title | State | Subsystem | What it reveals |
|---|---|---|---|---|
| #6 | Write the archive response files as UTF-8 | open | launcher/setup | Response files are written in the ANSI code page, so a non-ASCII user folder (for example an accented name) breaks the archive step after all 284 objects have compiled. The launcher only showed "exit code 120, Traceback", because printing the error itself also failed on a cp1252 console. |
| #8 | NVN: sample depth buffers made in their fallback format (AMD) | closed, not merged | GPU renderer | The root cause of #7 (above). Tested on an RX 9060 XT: Ditto pure white before, textured after. |
| #9 | Launcher: collect and translate the shaders the game builds at run time | open | shaders/launcher | Automates #2: dump missing blocks every run, translate them before the next Play, and do one hidden 60 s headless warm-up run before the first. 212 blocks rebuilt within 3 s of a headless run; 62 s for the first warm-up. |
| #11, #12 | Software keyboard packed config; menu mouse | closed upstream, merged on the fork | services; input | See the fork table (F6, F7). |
| #14 | Add read-only ExeFS compatibility verification | open | dump formats | Users with the wrong build had no way to see why: there was no check without the bundled compiler and a build folder. Adds a verify-only mode that reports the build ID and per-module SHA-256. |
| #15 | Launcher: use a .nup package instead of a dump folder | open | dump formats | Fixes #13 by unpacking to a folder. |
| #19 | Add `[pad] stick_max` | open | input | Fixes #18. |

### 2c. The fork's issues (featureJosh/pokopia-recomp)

| # | Title | State | Subsystem | Symptom | Hardware | Root cause / fix |
|---|---|---|---|---|---|---|
| F6 | Name entry keeps only the first letter | closed | other (HLE services) | The name entered at the naming scene comes back as its first letter ("J"); later the prompt loops forever, or shows the game's rejection in CJK mojibake | RX 9060 XT, Windows 11 | Three faults. (1) Pokopia's keyboard config is a packed 0x530-byte layout (library 0x140010) in which the UTF-8 flag and other fields have moved. (2) The game's name check calls the `ngc:u` profanity filter, which was missing; the generic stub error made the game refuse every name. (3) The text check has to use the config's encoding. One intermediate fix was wrong and had to be reverted. |
| F7 | Menus: the selection jumps around when the mouse is used | open | input | Hovering moves the selection erratically | RX 9060 XT | The pointer steered the game's selection with d-pad presses from a guessed start entry. Fix: the pointer no longer moves the selection; click = A. |
| F8 | The v0.1.1 release's spirv_harness does not match the repository source (too-bright shaders) | closed | shaders (build reproducibility) | With the shipped translator everything is too bright: the Pokédex is orange instead of red, Ditto pale pink | RX 9060 XT | Retranslating all 99,012 blocks with a build from source changed 34,364 `.spv` files (real code differences). The fork now builds the translator from source in CI. |
| F11 | SteamOS/Proton: black screen (no memory pool reaches the GPU) | closed | GPU renderer (host memory import) | Black window; audio, input and game logic work | Legion Go S, Proton 11, RADV | Wine backs the pagefile-backed heap section with a shared file, and RADV can only import anonymous memory as a host pointer. Fix: private heap memory under Wine with no alias views. It still needs the pool mirror and write-watch turned off: the write-watch `mprotect` on imported pools makes the imports fault again and hangs fences. |
| F13 | SteamOS/RADV: black background and white halos | closed | shaders | Black sky and background, white bloom halos | Legion Go S, RADV | The translated vertex shaders write only some components of their outputs. Vulkan leaves the rest undefined: AMD's Windows driver happens to give 0, RADV gives NaN, the tonemap turns NaN black, and bloom spreads it. The console and Ryujinx start every vertex-stage output at (0,0,0,1); their offline translator never knows the next stage. Fix: a load-time SPIR-V pass that initialises the outputs. It is a runtime pass because players' caches are never rechecked against a translator version. |
| F19 | Legion Go S: about 25 fps (an emulator does 60) | closed | performance | 22 to 27 fps | Legion Go S, Proton | 1080p render into a 720p window; about 11 queue submissions per frame, each of which re-walks about 4.7 GB of imported pools in the kernel driver (amdgpu userptr). Fix: one kernel submission per frame under Wine, giving 55 to 60 fps. |
| F23 | Legion Go S: 60 to 90 fps stable | closed | performance | GPU-bound at about 16 ms a frame | Legion Go S | Config lookups call `getenv` on every call (about 7% of the render thread); a guest thread spins in SleepThread; the 3.5 GB pool is imported whole. The plan is device profiles with upscaling on by default. |

---

## 3. Patterns

### Where the problems fall (primary subsystem; "touched" counts secondary involvement)

| Subsystem | Upstream issues | Upstream PR-only | Fork issues | Touched |
|---|---|---|---|---|
| Shaders | #2, #3, #10 | #9 | F8, F13 | **8** (#1, #2, #3, #7, #10, #17, F8, F13) |
| GPU renderer | #7, #17 | #8 | F11 | 4 |
| Launcher / setup / build toolchain | #1, #4 | #6 | – | 4 (with #16) |
| Input | #18 | #19 | F7 | 2 |
| Dump formats / versions | #13 | #14, #15 | – | 3 (with #16) |
| Performance | – | – | F19, F23 | 3 (with #1) |
| Crashes | #16 | – | – | 1 |
| Other (HLE services) | – | – | F6 | 1 |
| CPU / recompiler | 0 | 0 | 0 | 0 |
| Textures | 0 | 0 | 0 | 0 (both "Textures in black and white" issues were shaders and depth views) |
| Audio, saves / filesystem | 0 | 0 | 0 | 0 |

Observations:

- **No recompiler bug has been reported.** Their lifter's per-instruction differential testing appears to work. What breaks is everything around it: the shader pipeline, the build on the player's PC, and renderer assumptions.
- **The shader pipeline is the main source of failure,** in four different ways: incomplete (runtime-built shaders), wrong semantics (`.H0_NH1`, unwritten outputs), irreproducible (a shipped binary that does not match its source), and slow (1 to 2 hours, 36 GB).
- 10 issues in the first 20 hours after a release downloaded 3,500 times. The two most-confirmed issues (#2/#10, about 8 users) are the same root cause.
- The maintainer has not answered anything, so fixes collect in a fork. Some fixes (new hook sites, translator changes) cannot reach existing installs without a forced rebuild or retranslation (fork PR #26, F13).

### Systemic (architectural weaknesses)

1. **Offline, whole-corpus shader translation.** A one-time translation of the shaders found in the game files misses the ones the game builds at run time (#2, #10, #7; nxrecomp's MPR saw the same thing in S40). It translates each stage alone, so it cannot do what depends on the next stage (F13). The cache carries no translator version, so a translator fix silently does not apply (F13, F8, and #3 needing the runtime blocks retranslated). And it costs 1 to 2 hours and about 36 GB up front (#1).
2. **Building the game on the player's PC with a bundled toolchain.** It fails on low-RAM machines (#4), on non-ASCII paths (PR #6), hides the real error (PR #6), and leaves stale executables after an update that changes hook sites (fork PR #26). #16 may belong here too.
3. **Fixing game behaviour through hook sites inside the lifted code.** Resolution bugs like #17 need a new hook, and every new hook means re-lifting and recompiling on every install.
4. **Host-memory import of a pagefile-backed heap section.** Fragile across drivers and OSes: no import under Wine/RADV (F11), a userptr re-walk on every submission (F19), write-watch faults hanging fences (F11), and 30 to 100 ms per pool on Windows (nxrecomp note 401).
5. **Format and feature fallbacks applied in one place but not another.** The image uses the fallback depth format but the view does not (#7). Vendor-specific undefined behaviour (AMD gives 0, RADV gives NaN) hides bugs on the developer's GPU (F13).
6. **One exact game version.** Only 2.0.1a, verified by hash, and the user is not told clearly why a dump fails (#13, #16, PR #14, and the author of #3 could not run it).
7. **Thread model** (from nxrecomp, already patched in the logs they ship): pinning guest threads to three host cores in strict priority bands starves loaders on a busy PC; WFE and YIELD compiled to nothing made spin locks starve their holders. Each needed a guard (notes 180, 221, 360, 420, 422).

### One-off (local bugs)

- `.H0_NH1` half-precision swizzle (#3); sine and cosine off by 2π (fixed in v0.1.1); release translator binary built from different source (F8).
- DualSense outer dead zone (#18); menu mouse steering (F7); packed software-keyboard layout and a missing `ngc:u` (F6).
- .NET workstation GC throttling (#1); UTF-8 response files (PR #6).

---

## 4. Lessons for our design

Each lesson gives the requirement or pitfall, then the issues that motivate it.

1. **Translate shaders on demand at run time, with a persistent cache. Never depend on a one-time offline corpus.** Pre-translation can be an accelerator, not the source of truth. Ship the list of shader keys the game builds at run time for each supported version (pokopia-native's 154 on 2.0.1), so even the first launch knows what to prepare. A missing shader must never silently skip draws without a loud log line. *Motivated by #2, #7, #10, #17 (comment), nxrecomp S40.*
2. **Version the shader cache.** Key each cache entry by guest code hash plus translator version and the options of every pass, and invalidate automatically when the translator changes. Build the shipped translator from source in CI and record its hash. *#3 (the fix needed the runtime blocks retranslated), F8 (34,364 of 99,012 outputs differed), F13 (fix forced into a runtime pass because caches are never rechecked).*
3. **Match hardware semantics where Vulkan leaves things undefined, and test on more than one vendor.** Initialise every user output of vertex, tessellation and geometry stages to (0,0,0,1). Run the shader regression set on RADV or another NaN-producing driver as well as NVIDIA and AMD Windows. *F13: black sky and halos on RADV only; possibly the black sky in #7's last comment.*
4. **Build a per-instruction oracle for the shader decoder and emitter, with special attention to half-precision selectors and negations.** Their decoder recognised `.H0_NH1` but the emitter treated it as no swizzle. About a dozen romfs shaders and about 45 of the 212 runtime shaders use it, including the sun-shadow mask. *#3, #17.*
5. **Keep first-run setup short and bounded.** Avoid a multi-hour, roughly 36 GB step before the first frame. If a managed tool is used, use server GC; better, use a native translator. Show the disk needs up front. *#1, README (1 to 2 h), launcher README (10 GB + 26 GB).*
6. **If the game is compiled on the player's PC, make the build robust.** Size the jobs by free RAM (their fix: free RAM / 2 GiB per job), retry a failed unit alone, keep translation units at about 1 GB peak, make every path and response file Unicode-safe, show the compiler's real error text, and stamp the build with the hash of the recompiler, hook sites, runtime library and flags so that an update forces a rebuild. Prefer an in-process LLVM over a subprocess toolchain whose failures are opaque. *#4, PR #6, fork PR #26, #16.*
7. **Identify the game by build ID, not by folder name, and support several versions.** Accept base plus update, folders, NSP/XCI and .nup (stored and compressed). Offer a "check my dump" mode with plain messages: wrong version, missing romfs, unsupported format. pokopia-native already supports 1.0.5 and 2.0.1; keep per-version tables. *#13, #16, PR #14, PR #15, #3's author.*
8. **Handle the vendor format gaps consistently.** When an image gets a fallback format (D24S8 becomes D32S8 on AMD), every sampled, storage and attachment view of it must use the same fallback. A dummy-view binding is a bug report, not a fallback. Run CI on AMD hardware. *#7, PR #8, #10 (RX 9070 log).*
9. **Make rendering resolution-independent at the renderer level.** Test at 1080p, 1440p, 4K and ultrawide from day one. Where the game bakes 1920x1080 into recorded viewports or screen constants, fix it in one renderer-side rule or one targeted patch, not by re-lifting game code. *#17, fork PR #25, chainblade131's 1440p screenshot in #7.*
10. **Do not import a giant pagefile-backed heap as Vulkan host memory.** Use device-local copies, small private views per pool, or a staging scheme. Batch submissions so a frame is not 11 kernel submits, and make it work under Wine/Proton (Steam Deck class devices are likely users). *F11, F19, nxrecomp notes 047, 223, 401.*
11. **Thread model: no strict pinning without a starvation guard.** Compile WFE and YIELD as host yields, replace SDK spin mutexes with host locks that yield, and assume a loaded PC (other programs, shader compiles). Raise the priority class only for play mode. *nxrecomp notes 180, 221, 360, 420, 421, 422.*
12. **Stubs must not cause retry loops.** Any failure the game retries (a failed shader link, a refused name check) becomes a performance collapse or an infinite prompt. Answer services explicitly (`ngc:u`) and accept an unlinked program instead of returning failure. *F6, nxrecomp note 400 (frame rate decayed to 2 to 3 fps).*
13. **Input: outer dead zone and per-pad calibration; pointer menus that click instead of steer.** Keep direct-look camera writes for mouse and stick (pokopia-native's approach). *#18, F7, v0.1.1 note on mouse look dropping out.*
14. **HLE details are version-specific.** Pokopia's software keyboard uses the packed 0x530 config (library 0x140010) with moved fields and UTF-8 text checks. Pick the layout by size or version and test the naming scene in CI. *F6, PR #11.*
15. **Logs that triage themselves; reports that cannot be lost.** Log at start the GPU, driver, game build ID, number of missing shaders, number of dummy bindings and render resolution. Flush the log and write a crash report on any exit (#16's log stops mid-line with no report). Do not print alarming "542 logging stubs" banners: #17's reporter chased the ray-tracing stubs. *#10 logs (made the diagnosis possible), #16, #17.*
16. **Persist pipeline caches incrementally and prewarm from recorded keys.** Their cache was never written because the process ends with TerminateProcess. Use graphics pipeline libraries or async pipeline builds to avoid first-run hitches. *nxrecomp notes 222, S24, S29.*
17. **Texture aliasing needs explicit rules.** One image per storage and shape fixed MPR's colour noise (note 440). It then needed feedback-loop copies (notes 441, 443), and it is the configuration that produced pokopia-native's stale icons when the game sets the IMAGE flag on BC1/BC7 textures. Key sharing by storage, shape, format and usage, and test icon updates. *nxrecomp notes 440, 441, 443; our icon bug.*
18. **Desktop GPUs cannot sample ASTC.** Pokopia ships ASTC, which Apple GPUs sample natively. Windows needs a transcoder (they use ASTC to BC7 with a fast first pass and a background high-quality re-encode, cached by source hash and quality). Plan the cache, the CPU cost and the watchdog interaction (nxrecomp note 421: a cold BC7 cache on the render thread looked like a freeze). *nxrecomp tools/textures, notes 024, 421.*

---

## 5. What pokopia-native already solved, judged from their descriptions

| Their problem | pokopia-native's fix | Match | Notes |
|---|---|---|---|
| #3 and the shadow half of #17: no sun, cloud or object shadows | The translator's `.H0_NH1` swizzle on the second operand of HADD2/HFMA2/HMUL2 is fixed | **likely** | #3 was written from our fix, and ENDU1997 confirms it restores their sun shadows. Our port must carry the same fix into its Windows translator, and into any runtime-built shader path. |
| #2, #10 and the shader half of #7: Ditto black, greyscale world, startup freeze | The roughly 154 shaders Pokopia creates at run time are recorded and added, so the first launch is no longer black | **likely** | Same class of bug. Their count (212 code blocks in the opening, keyed by a SHA-1 prefix of the code area) and ours (140 keys shared with 1.0.5, plus 14 only in 2.0.1) use different schemes and are not directly comparable. Their fix needs a warm-up run per install; ours ships the knowledge with the build. |
| Version lock (#16 possibly, #3's author, PR #14) | pokopia-native supports 1.0.5 (v327680) and 2.0.1 (v655360) | **likely** (for the version part) | They support only 2.0.1a. |
| Stale item, Pokédex and habitat icons | Do not share one GPU texture between texture objects over the same storage when the game sets the IMAGE flag on BC1/BC7 icons | **possible** (risk, not reported) | No issue reports stale icons yet, and the first public build reached only the opening and first area. But nxrecomp's note 440 says their back end shares one image per storage and shape, the arrangement that produced our bug. Expect it to appear when players reach the menus with many icons. |
| Camera look: mouse look dropping out about once a second (v0.1.1 release notes), general camera feel | Direct look writes the camera's pitch and yaw at player 1's input read | **possible** | No open issue is about look latency. Their log shows they also place the camera every frame from the mouse. #18 (stick outer zone) is a different problem and not solved by direct look. |
| #7 depth view refused on AMD (D24S8 fallback) | nxnative on Metal must already sample D24S8 in a fallback format (Apple GPUs lack D24S8) | **possible, unconfirmed** | Not among the known fixes. Check that our Vulkan back end applies the fallback to views too. |
| F13 unwritten vertex outputs read as NaN on RADV | unknown | to check | Metal may hide it the way AMD's Windows driver does. Add the (0,0,0,1) initialisation to our translator. |
| F6 name entry (packed 0x530 keyboard config, `ngc:u`) | unknown | to check | Test a new save's naming scene on our runtime. |
| #17 viewports above 1080p | unknown | to check | Test at 1440p and 4K on Windows; iPhone and Mac may never have rendered above 1080p. |
| #4, PR #6 build-on-PC failures | pokopia-native's Windows builder works (its black first run was the runtime shaders, not the toolchain) | partial | Our Windows x86-64 build will compile on players' PCs and must still meet lesson 6. |
| ASTC on desktop GPUs | not applicable on Apple (native ASTC) | not solved | New work for Windows (lesson 18). |

---

## 6. drakolordx7/nxrecomp

### Snapshot

| Item | Value |
|---|---|
| Description | Static recompiler, runtime and tools for running Switch (AArch64) games natively on Windows x64 |
| Created / last push | 2026-10-21 / 2026-11-02 |
| Licence | GPL-3.0 |
| Stars / forks | 4 / 0 |
| Issues / PRs / releases | **0 / 0 / 0** |
| First game | drakolordx7/MetroidPrimeRemasteredRecomp (14 stars; 6 releases v0.1.0 to v0.1.5, 2026-10-24 to 2026-11-02, 35 to 153 downloads each; 1 issue: a language-selection request, answered and shipped in v0.1.5) |

Docs: README (status "early": all of MPR lifts and passes its checks, and the game boots to its first level), CONTRIBUTING (issue routing, no game data, the log/crash/freeze files and trace switches), the recompiler docs (README, GAPS, S1, S1b, 9 debug notes), about 50 runtime development logs (S3 to S50) and about 90 runtime debug notes, plus READMEs for the tools. Pokopia-specific docs (NVN2 ABI, Ampere shaders, Joy-Con 2 mouse, IPC answers) exist only in pokopia-recomp.

### What their own logs say went wrong (MPR bring-up), and what it means for us

| Note | Problem | Lesson |
|---|---|---|
| recomp 007, 008 | Indirect tail calls were lifted as a dispatch over every instruction of the function (4,907 functions); hand-written SDK stubs with no FDE or symbol were never lifted | Classify `br` targets and run a gap scan for code outside every discovered function; "100% coverage" counted instruction words, not functions |
| recomp GAPS | FPCR not honoured; FPSR flags not tracked; exclusive monitor is a CAS; no memory ordering between plain accesses | Decide each of these on purpose for x86; Pokopia's sdk writes FPCR in 3 places |
| 400 | The frame rate decays to 2 to 3 fps when shader blocks are missing: a failed program link is retried on every bind | Lesson 12 |
| 222, S24, S29 | The pipeline cache was never written (the process ends by TerminateProcess); first-run pipeline hitches | Lesson 16 |
| 047, 223, 401 | Importing pools as host memory costs 10 to 100 ms each, because the driver walks the whole view | Lesson 10 |
| 440, 441, 443 | Texture objects over one storage need one image, then feedback-loop copies when a pass samples its own target or another mip of it | Lesson 17 |
| 442, 140 | Indexed vertex-attribute arrays, unwritten varyings, integer formats through float aliases, missing IMAGE flags, dual-source blend, partial stencil clears | A long tail of NVN-on-Vulkan mismatches; budget for validation-driven bring-up |
| 180, 221, 360, 420, 421, 422 | Pinned threads with strict priorities starve on a loaded PC; WFE and YIELD as no-ops let spin locks starve their holders; "deadlocks" that were starvation | Lesson 11 |
| 072, 073 | SetHeapSize allowed more than TotalMemorySize; CNTPCT_EL0 read as 0 | Model memory limits and counters exactly like the kernel |
| 121, nvn2_abi | NVN object sizes differ from NVN1 (texture 0x80 to 0xC0, queue 0x2000 to 0x3000, and more) | Use per-game, per-SDK size tables taken from the binary |
| S40 | Shader blocks missed because an extraction tool stopped on a `?` in an asset name, and failures were hidden among 340 empty FAIL lines | Read the game archives directly; make failures loud |
| 160 | The game presents from a different thread than the one running Update | Keep swapchain work on the presenting thread |

---

## Appendix: method

- GitHub REST, unauthenticated: about 25 requests (repository metadata, `/issues?state=all`, `/issues/comments`, `/pulls?state=all`, `/releases` and recursive tree listings for pokopia-recomp, nxrecomp, MetroidPrimeRemasteredRecomp and the featureJosh fork).
- Docs fetched from raw.githubusercontent.com, with fenced code blocks removed by a script before reading.
- Four attached `pkp.log` files (#10 twice, #16, #18) were read for the GPU, missing-shader and dummy-view counts. No personal data from them is reproduced here.
