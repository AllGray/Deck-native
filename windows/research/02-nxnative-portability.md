# 02 — nxnative portability audit (for a Windows x86-64 runtime with recompiled game code)

Tree audited: `<work>\installer\nxnative\` (upstream seedds/nxnative at c1df4dd + `installer\host\nxnative-switch2.patch`, 29 files, 4,059 patch lines). Paths below are relative to `nxnative\` unless they start with `installer\` (= `<work>\installer\`) or `build201\` (= `<work>\installer-test-win\build201\`). Read-only audit; nothing was built or run.

Legend used throughout:

- **Code class**: `C` portable C (or Python/C#), `PX` POSIX (pthreads, mmap/mprotect, signals), `AP` Apple-only (named), `A64` arm64 assembly, `GEN` generated, `DATA`.
- **Scope**: `GA` game-agnostic, `GS` game-specific (game named), `GE` engine-specific (Unreal, Unity…).
- **Windows fate** (recompiled guest code): `R` reuse as-is (at most mechanical edits), `R*` reuse once its calls into guest code go through the recompiler's call-in API, `P` port (POSIX/Apple calls → Win32/SDL), `W` rewrite (Metal → Vulkan, Apple input/audio → SDL3), `V` vanishes under recompilation, `D` drop (not needed for Pokopia on Windows).

---

## 1. Inventory of `dm\`

Totals of source lines (excluding `tests\`, `third_party\`, build outputs): `host` 17,562 · `gfx\nvn` 30,616 (of which 11,033 generated or third-party header) · `gfx\tex` 318 · `gfx\msl` 191 · `gfx\vk` 1,554 · `gfx\gl` 2,069 · `gfx\metal` 465 · `gfx\tools` 8,624 · `ios` 2,636 · `mac` 729 · `tools` 7,318 · `games` 2,607 (+ ~2.96 M lines of per-game data tables) · `sanitizers` 31.

### 1.1 `dm\host\` (the Horizon host)

| File | Lines | Class | Scope | Fate | What / why |
|---|---:|---|---|---|---|
| `Makefile` | 70 | AP (clang, `-framework Cocoa/QuartzCore/Metal…`) | GA | W | build; replace with CMake/clang-cl |
| `.gitignore` | 7 | – | – | – | |
| `main.c` | 146 | AP (CFBundle, `sysctl KERN_PROC`) | GA | P | bootstrap: replicates `nn::init::Start` order (`main.c:18-41`) |
| `loader.c` | 662 | AP+PX (mmap/mprotect, iOS `vm_remap`, `getsectiondata`, `mrs tpidrro_el0`) | GA | P | rtld: map, key page, gates, ELF relocation, forced take-over, NROs |
| `hle.c` | 132 | AP (`getsectiondata`, `dladdr`) | GA | P/V | import resolution, unimplemented stubs, gate dispatch (`hle.c:51`, `:112`) |
| `hle.h` | 69 | AP (Mach-O sections) + A64 (HLE_FORCE_BIG asm) | GA | P | `HLE`, `HLE_FORCE`, `HLE_FORCE_BIG` macros; ABI note at `hle.h:2-3` |
| `hle_table.c` | 14 | C (assumes Apple libc ABI) | GA | P | 64 libc passthroughs (`memcpy`…`strftime`); see risk on `long`/`struct tm` |
| `entry.S` | 173 | A64 (Mach-O) | GA | V | 4,096 unimpl stubs, 1,024 gate stubs, `_dm_gate`, `_dm_thread_enter` |
| `thread.c` | 75 | PX (pthread keys, mmap) | GA | P/V | per-thread TLS (8 KB) + ThreadLocalRegion (0x200) + thread object; gate stacks (V) |
| `hle_thread.c` | 1,206 | PX + AP (mach thread ports, `thread_suspend/get_state`, QoS, `pthread_override_qos`, SIGUSR2/ucontext) | GA | P | Horizon thread/sync SVCs, scheduling semantics |
| `hle_svc.c` | 701 | PX (mmap/mprotect/madvise) + mach `vm_read_overwrite` | GA | P | memory SVCs, guest address space, GetInfo |
| `hle_os.c` | 170 | AP (`mach_absolute_time`) | GA | P | clocks (`guest_ticks` 31.25 MHz from the patch), objmap, `getentropy` |
| `crash.c` | 215 | PX + AP (`__ss/__es` ucontext, mach, `sys_icache_invalidate`) | GA | P | signal handlers, page-fault hook dispatch, watchdog |
| `hle_bigstack.c` | 97 | A64 asm + PX | GA | V | runs take-overs on a 512 KB host stack when called on a small guest stack |
| `hle_guestthread.c` | 63 | C (calls guest functions) | GA | P | host-created guest threads via `nn::os::CreateThread` |
| `hle_fs.c` | 1,054 | PX + AP (`clonefile`, `renamex_np RENAME_SWAP`, xattr) | GA | P | nn::fs → host files; transactional saves (`hle_fs.c:37-38`, `:663`, `:673-674`) |
| `hle_audio.c` | 566 | AP (AudioToolbox AudioUnit, libdispatch) | GA | P | AudioOut queue semantics (C) + CoreAudio sink (W → SDL3) |
| `hle_audren.c` | 548 | PX; calls guest code and decodes guest arm64 (`hle_audren.c:391-410`) | GA | P | runs the game's own SDK audio renderer in-process |
| `hle_codec.c` | 107 | C (calls guest OpusDecoder) | GA | R* | hwopus → SDK software Opus |
| `hle_movie.c` | 839 | PX + libvpx + stb_vorbis | GA | P | SDK movie decoder take-over (VP8/VP9/Vorbis) |
| `hle_movie_avc.c` / `.h` | 195 / 17 | AP (VideoToolbox, CF) | GA | W/D | H.264 (only if a game uses AVC) |
| `hle_hid.c` | 1,030 | PX (pthread) | GA + GS (`look_cam`, Pokopia, profile-driven, +152 patch lines) | R | nn::hid state from the front end |
| `hle_touch.c` | 182 | C | GA | R | touch + gestures |
| `hle_haptics.c` / `.h` | 95 / 23 | PX | GA | R | vibration state |
| `hle_oe.c` | 272 | PX | GA | R | nn::oe / applet focus |
| `hle_offline.c` | 264 | C | GA | R | network/peripheral services "unavailable" (+60 S2 lines) |
| `hle_sdk16.c` | 349 | PX | GA | R | newer-SDK oe/time/pl(shared font)/vi/diag |
| `hle_misc.c` | 178 | C | GA | R | aoc/shop/error/ngc/pctl/swkbd glue |
| `hle_account.c`, `hle_friends.c`, `hle_net.c`, `hle_mii.c`, `hle_sf.c`, `hle_sdkinit.c`, `hle_nvdrv.c` | 113, 66, 137, 156, 97, 15, 17 | C | GA | R | service stand-ins |
| `hle_time.c` | 156 | PX (`localtime_r`) | GA | R/P | nn::time |
| `hle_swkbd.c` | 215 | PX | GA | P | software keyboard → front-end input box |
| `hle_ro.c` | 111 | C (calls guest DT_INIT/DT_FINI) | GA | P | nn::ro NROs (Pokopia: none listed) |
| `hle_glslc.c` | 46 | C (calls guest `glslcCompile`) | GA | R* | run-time GLSLC capture (check whether Pokopia imports it) |
| `hle_lifecycle.c` | 29 | PX (SIGTERM) + libdispatch | GA | P | graceful exit |
| `hle_cheats.c` | 2,103 | AP (`mach_vm_remap` alias `:184`, `vm_read_overwrite/vm_write` `:507-515`, libdispatch, `sys_icache_invalidate`) | GA | P (data cheats) / D (code cheats) | Atmosphère cheat VM |
| `hle_fpslock.c` | 335 | C (`mach_*` once) | GE | D | engine frame-limit entries (no Pokopia entry) |
| `hle_uecvar.c`, `hle_uemalloc.c`, `hle_oodle.c` | 44, 146, 210 | C/AP (malloc zone) | GE (Unreal, Oodle) | D | |
| `hle_count.c`, `hle_fdstat.c`, `probe.c`, `memstat.m` | 56, 38, 409, 155 | AP/PX diagnostics (`probe.c`: mach debug state) | GA | D | diagnostics |
| `save_sync.c` / `.h` | 568 / 47 | AP (iCloud folder, clonefile, renamex_np, CommonCrypto) | GA | D | Mac↔iPhone save sync |
| `front.h` | 54 | C interface | GA | R | front-end API (`front_run`, `dm_touch_set`, …) |
| `dm_input.h` | 49 | C | GA | R | `dm_input_set`, `dm_motion_set*` |
| `front_mac.m` | 1,374 | AP (AppKit, CAMetalLayer, GameController, libdispatch) | GA | W | Mac window/keyboard/pads/menus/cheat panel |
| `haptics_apple.h` / `.m` | 7 / 427 | AP (Core Haptics, GameController) | GA | W | |
| `motion_apple.h`, `motion_pro_mac.h`, `pad_apple.h`, `pad_hid_mac.h` | 40, 79, 21, 85 | AP (GCMotion, IOHID) | GA | W/D | SDL3 covers these |
| `motion_fusion.h`, `motion_pro.h`, `pad_hid.h`, `pad_slots.h` | 132, 36, 68, 37 | C | GA | R | pure logic |
| `romfs_identity.h`, `romfs_image.h` | 106, 72 | C | GA | R | RomFS image index |
| `dm.h` | 194 | PX (pthread inline helpers, `flockfile`) | GA | P | common header |
| `tests\` | 57 files, 7,390 lines | mostly Mac-only C tests | GA | – | reference for behaviour |

### 1.2 `dm\gfx\` (NVN → Metal)

| File | Lines | Class | Scope | Fate | What |
|---|---:|---|---|---|---|
| `README.md`, `nvn_metal_mapping.md` | 203, 78 | doc | | | |
| `hle_vi.c`, `hle_nv.c` | 81, 33 | C | GA | R | vi / nv glue |
| `nvn\hle_nvn.c` | 2,652 | C (Apple only for mach time, CommonCrypto SHA-256, CF, QoS, sysctl) | GA | P (logic reused) | NVN interface layer: objects, record, submit side effects, present pacing |
| `nvn\nvn_obj.h` | 261 | C | GA | R | object layouts + command record format (shared by both layers) |
| `nvn\nvn_mtl.h` | 53 | C interface | GA | R (as backend interface) | `mtl_*` calls of the Metal backend |
| `nvn\nvn_mtl.m` | 12,009 | AP (Metal, ObjC, libdispatch, CAMetalLayer, MetalFX) | GA | W | replay layer |
| `nvn\nvn_capture.inc`, `nvn_geometry.inc`, `nvn_feedback.inc`, `rt_range_check.inc` | 507, 803, 85, 62 | AP (Metal) | GA | W/D | capture, GS-as-compute, transform feedback, checker |
| `nvn\nvn_rec.h` | 335 | AP (deferred Metal encoding) | GA | D | |
| `nvn\nvn_refl.m` / `.h` | 170 / 70 | ObjC JSON / C | GA | P | shader reflection json → C arrays |
| `nvn\host_msl.m` / `.h` | 42 / 7 | AP | GA | W | host's own Metal shaders loader |
| `nvn\nvn_builtin.h` | 22 | MSL | GS (another game's 4 embedded compute shaders) | D/W | |
| `nvn\nvn_border.h`, `nvn_depth_clear.h`, `nvn_pso_ds.h`, `nvn_feedback.h` | 28, 12, 21, 11 | Metal-specific workarounds | GA | D/W | |
| `nvn\nvn_pso_skey.h` | 64 | C (Metal formats inside) | GA | P | pipeline manifest key |
| `nvn\nvn_dirty.c` / `.h` | 220 / 17 | PX (mprotect in fault path) + mach time | GA | P | write-protect dirty tracking |
| `nvn\nvn_tex.c` / `.h` | 232 / 28 | C (deko3d) | GA | R | block-linear sizes/offsets |
| `nvn\nvn_capture.c` / `.h` | 163 / 109 | C | GA | R | capture file format |
| `nvn\nvn_cover.h`, `nvn_fstep.h`, `nvn_geometry.h`, `nvn_hmap.h`, `nvn_pace.h`, `nvn_primitive.h`, `nvn_timestamp.h` | 41, 142, 95, 78, 16, 29, 7 | C | GA | R | pure helpers |
| `nvn\dmsh_abi.h` | 78 | C | GA | R | shader-library ABI (c0/c2 driver bank layout, binding kinds) |
| `nvn\dmsh_chain.h`, `nvn_count.h` | 3, 3 | GEN | | R | |
| `nvn\nvn_protos.h` | 740 | GEN (gen_protos.py; +197 S2) | GA | R | prototypes + typedefs used for signature checks |
| `nvn\names.txt` | 734 | DATA (+197 S2) | GA | R | NVN name table |
| `nvn\include\nvn.h` | 1,756 | third-party header (MIT) | GA | R | |
| `nvn\nvn_thunks.S` | 9,554 | GEN A64 (+2,561 S2) | GA | V | 734 counting thunks |
| `nvn\nvn_entry.S` | 91 | A64 | GA | V | unimpl / profiling / big-stack entries |
| `nvn\.switch2-patch`, `.windows-extras` | 1, 1 | markers | | | |
| `tex\nvn_deswizzle.c` / `.h` | 91 / 20 | C | GA | R | Tegra block-linear ↔ linear |
| `tex\nvn_deswizzle_gpu.m` / `.h` | 92 / 20 | AP (Metal compute) | GA | W | |
| `tex\nvn_fmt_mtl.c` / `.h` | 83 / 12 | C (table of MTLPixelFormat) | GA | W | NVN format → VkFormat table instead |
| `tex\README.md` | 48 | doc | | | |
| `msl\*.metal` (15 files: builtin, clear, copy, deswz, draw_tex, geo_pack, gl_util, ixc, mdx, present, rb_swap, tf, wide) | 191 | MSL | GA | W | host's own shaders |
| `vk\hle_vk.m` | 1,554 | AP (Vulkan API on Metal) | GE (Hades, SM Galaxy) | D | not used by Pokopia (NVN) |
| `gl\hle_gl.m` | 2,069 | AP (EGL/GL on Metal) | GE | D | not used by Pokopia |
| `metal\` (Makefile 23, README 58, mtl_backend.h/.m 48/198, probe\p_nocopy.m 34, test_offscreen.c 162) | 523 | AP sample | – | D | not in the host |
| `tests\` / `tex\tests\` | 132 files 15,511 / 4 files 301 | Metal tests | | – | |

`dm\gfx\tools\` (offline shader chain; all Python unless noted; all game-agnostic unless noted):

| File(s) | Lines | Windows fate |
|---|---:|---|
| `dmsh_build.py` | 1,332 | front half R (job files, xlate, caching); back half W (spirv-cross→MSL, MSL rewrites, reflection from MSL, metallib) |
| `dmsh_xlate\Program.cs` (+csproj 19) | 265 | R (C#; Ryujinx translator with `TargetLanguage.Spirv, TargetApi.Vulkan`, `Program.cs:177`); re-answer the Metal-tuned host queries (`Program.cs:125-142`) |
| `dmsh_xlate\bin\`, `obj\` (86 files), `dmsh_xlate_bin\` (64 files) | build outputs | R (.NET 10, runs on Windows) |
| `spirv_cross_bin\` (macOS binary, build.sh 29, licences) | 7 files | D for Vulkan |
| `dmsh_chain.py`, `dmsh_chain.lock`, `tool_lock.py`, `own_compilers.py`, `dmsh_facts.py`, `dmsh_abi.py`, `dmsh_dryrun.py`, `gen_protos.py`, `shader_regressions.py`, `vbf.py`, `mvgl.py` | 98, 8, 56, 77, 23, 38, 50, 63, 62, 49, 79 | R/P (chain/version plumbing; `own_compilers.py` checks the Metal compiler → P) |
| `c1_guard.py`, `cb_extent.py`, `dmsh_alpha.py`, `dmsh_logic.py`, `dmsh_vin.py`, `dmsh_pull.py` + `dmsh_pull.metal`, `dmsh_fetch.py`, `dmsh_mesh.py`, `dmsh_geometry.py`, `dmsh_atomic.py`, `dmsh_mtlcache.py`, `host_msl.py`, `pso_offline.py`, `tbdr_budget/load_dead/store_dead/pass_merge.py`, `passgpu_top.py` | 242, 101, 58, 87, 50, 58+85, 66, 138, 314, 59, 114, 57, 234, 97/46/33/26, 22 | W/D (operate on MSL, metallibs, Metal TBDR); the c1/cb interval analysis could be redone on SPIR-V |
| `shader_split.py` | 2,674 | R (Switch-1 containers; Pokopia uses the installer's own S2 extractors) |
| `gl_shaders.py`, `vk_shaders.py` | 236, 188 | D (GL/Vulkan games) |
| `cpk.py`, `frostbite.py`, `smash_arc.py`, `uepak.py`, `unityfs.py` | 127, 189, 81, 594, 427 | GE/GS archive readers, D |

### 1.3 Other `dm\` directories

| Directory / file | Lines | Class | Fate |
|---|---:|---|---|
| `ios\Sources\` (front_ios.m 354, main.m 233, DmGamepad.m 551, Launcher*.swift 362, CloudSaves 103, SaveSync.m 59, headers 17) | 1,679 | AP (UIKit, SwiftUI, GameController, CoreMotion) | D |
| `ios\tools\` (build_ipa.sh 205, pack_ipa.py 312, build_smoke.py 105, gen_* 161, ios_metal.py 14, compile_check 42, stage_resources.sh 57, xcode_projects 61) + `project.yml` 95 | 1,052 | AP build | D |
| `ios\tests\` | 9 files, 1,087 | | – |
| `mac\` (Launcher.swift 196, launcher.c 217, pack_app.py 313, min_os.mk 3) | 729 | AP | D (launcher idea → SDL/Win32) |
| `sanitizers\` (coverage.mk 17, sanitizers.mk 14, ignorelist 10, tsan.supp 8) | 49 | clang flags | P |
| `tools\patch_nso.py` | 1,670 | C (Python, Capstone 5) | V at run time; its CFG/jump-table/spin analyses are reusable references (§2.3) |
| `tools\nso.py`, `nso_asm.py`, `nso_code.py`, `nso_syms.py`, `nso_match.py`, `nso_fnmap.py`, `nso2elf.py`, `nacp.py`, `romfs.py`, `romfs_image.py`, `nro_collect.py`, `sdis.py`, `insn_census.py`, `loader_census.py`, `nvn_slots_got.py` | 125, 28, 134, 85, 293, 116, 42, 56, 324, 183, 149, 124, 49, 145, 102 | C | R (NSO/RomFS readers, symbol tables, function fingerprints, data-block finder) |
| `tools\sdk_patches.py`, `ue_cvar.py`, `ue_malloc.py`, `ue_region.py`, `il2cpp_sym.py`, `check_thread_cuts.py`, `make_ext_font.py` | 220, 105, 104, 137, 100, 177, 131 | C | sdk_patches: V (iPhone-VA cuts); UE/IL2CPP: D; font: R |
| `tools\lint_c.py` (+baseline 144), `lint_python.py` (+244), `coverage_report.py`, `docs_*.py` (3), `env_switches.py`, `gpuprof.py`, `usage.py`, `impact_map.tsv`, `ghidra\Decompile.java` | 251, 130, 135, 519, 85, 216, 289, 136, 60 | C | R (dev tooling) |
| `tools\fuzz\` (6 .py = 648 lines, corpus 33 files, known.tsv) | 648 | C | R |
| `tools\tests\` | 73 files, 11,464 | | – |
| `games\game.py`, `settings.py`, `profile_guess.py`, `settings.tsv`, `README.md` | (2,607 in all) | C/DATA | R (profile → `game_profile.h` generator) |
| `games\<title id>\` × 126 (121 `game.tsv`, 22 `patches.py`, shader_map/pairs/facts, pso manifests, READMEs) | 1,064 files, ~2.96 M lines (2.87 M shader tables) | DATA, GS | Pokopia's profile lives in `installer\profile\0400A3A01EE4C000\` (game.tsv 55, runtime_keys.tsv 158, shader_sources.tsv 7, shader_facts.seed.tsv 12) |
| `third_party\` | flip (12 files), libvpx (240, VP9 decoder, BSD), ryujinx (421, shader translator, MIT), stb_vorbis (3), vulkan-headers (17), macnativegames (licence) | | libvpx/stb_vorbis/ryujinx/vulkan-headers R; flip D |
| `.gitignore` | 13 | | |

`win\` (outside `dm\`) is the toolchain port that builds the *iPhone* app on Windows. Useful pieces: `win\arm64_emu.py` (236 lines) is a Unicorn-based mini-loader that maps and relocates NSOs and calls guest functions with a sentinel return address, used to run the game's GLSLC/Oodle at build time (`win\glslc_emu.py`, `win\oodle_emu.py`); `win\pyshim\` lets nxnative's Python tools run on Windows (the `build201` rewrite and shader chain were produced on this PC).

---

## 2. How the game's code runs today

### 2.1 Guest address space

- **Identity mapping.** Guest addresses are host addresses. Nothing is at a Horizon address; every guest pointer the game sees is a real host pointer.
- **Modules** are mapped individually wherever the host allocator puts them: Mac `mmap(NULL, …)` + memcpy of `<name>.patched.bin` (`loader.c:585-615`); iOS `vm_allocate(ANYWHERE)` + `vm_remap` of the code embedded in the executable's `__TEXT` (`loader.c:513-572`, no run-time code allowed). Text and patch area are sealed `PROT_READ|PROT_EXEC` right after the copy; ro+data+bss stay RW.
- **One guest block** for everything else, reserved `PROT_NONE` with one `mmap` (`hle_svc.c:53`) and partitioned (`hle_svc.c:61-63`): `[0, 256 MB)` unused, then **alias** (profile `mem_alias`), then **stack** region (`mem_stack`), then **heap** (the rest). `SetHeapSize` opens/closes the heap with `mprotect` (`hle_svc.c:448-477`); `Map/UnmapPhysicalMemory` commit/decommit inside alias (`hle_svc.c:593-646`, `madvise(MADV_FREE)`); `MapMemory`/`UnmapMemory` (thread stacks moved into the stack region) **copy bytes** rather than alias them, because 16 KB host pages cannot isolate 4 KB guest slices (`hle_svc.c:486-518`, `docs\development.md:134-150`). `QueryMemory` reports module layouts and alias runs from a page bitmap (`hle_svc.c:519`). GetInfo answers alias/heap/ASLR/stack bases from this block and the application pool (`hle_svc.c:662-696`).
- **Pokopia 2.0.1 profile** (`installer\profile\0400A3A01EE4C000\game.tsv`): `mem_reserve 0x2C0000000` (11 GB), `mem_alias 0x10000000` (256 MB), `mem_stack 0x10000000` (256 MB) ⇒ heap region ≈ 10.25 GB; `app_pool 0x280000000` (10 GB, the S2 game panics below ~7.9 GiB); `sys_resource 0` (no virtual address memory); `mem_used 0x5600000`. Patch adds GetInfo ids 29–33 (`hle_svc.c:689-692`).
- The reservation size limits (5–6 GB on iOS without the extended-VA entitlement, `hle_svc.c:13-18`) and all the "alias region too small" machinery (VammManager/StandardAllocator observation gates, sdk `MATCH_CUTS` shrinking the 1 GB node area) are **iPhone address-space workarounds**. On Windows (128 TB user VA) a Switch-sized alias region needs none of them.

### 2.2 NSO loading and linking

1. Offline: `dm\tools\patch_nso.py` writes per module `<name>.patched.bin` + `.fixups.tsv` (layout header) + `.gates.tsv`, `.sites.tsv`, `.cuts.tsv`, `.obs.tsv`, `.changed.tsv` (`patch_nso.py:15`, `:1598-1668`).
2. `load_modules` (`loader.c:621-651`): read every fixups header (`read_layout`, `loader.c:186`), create four pthread keys (`create_keys`, `:32`), map each image and write the **key page** (`install_key_page`, `:253`: entry k = pthread key number × 8, then `PROT_READ`), verify the Apple TSD layout (`tsd_check`, `:39-45`: slot = `tpidrro_el0 + 8 × key`), parse MOD0/dynamic (`:48`), register gates by absolute return address (`register_gates`, `:263`).
3. Relocate all modules after all are mapped (`relocate`, `loader.c:329`): DT_RELR (`:279`), `R_AARCH64_RELATIVE`, then `JUMP_SLOT/GLOB_DAT/ABS64` through `bind_sym` (`:293`): **forced take-over** table first (`HLE_FORCE`, overrides even a module's own definition, `:150-183`), then ELF global lookup in load order (a symbol defined by an earlier module wins, as Horizon's rtld), then the host `swhle` table, then a **named unimplemented stub** (`hle.c:51-79`); undefined weak with no provider → 0.
4. NROs (nn::ro) are pre-mapped and bound at `LoadModule` (`loader.c:351-469`, `hle_ro.c`). Pokopia lists none.
5. `front_run(guest_main)`; `guest_main` (`main.c:18-41`) replays `nn::init::Start`: `_init_libc0`, `nnosInitialize(main thread handle)`, `_init_libc1`, `nninitInitializeSdkModule`, `SetHostArgc/Argv`, `nninitStartup`, `_init_libc2`, each module's `DT_INIT` (`run_module_inits`, `loader.c:655`), `nnMain`.

### 2.3 What `patch_nso.py` rewrites and why (with the 2.0.1 build's numbers)

The rewriter exists because Apple's kernel and ABI differ from Horizon's for the same arm64 code (`patch_nso.py:1-15`). Each site instruction is replaced by a `B` to a stub in a **patch area** appended to the image (or placed before the base when the image is over 112 MB; split in two when the text exceeds the ±128 MB branch reach, `patch_nso.py:37-53`). Stubs reach the four per-thread slots through the key page: `mrs tpidrro_el0` (Apple TSD base) + `adrp/ldr` of `key × 8` (`emit_slot_addr`, `:189-193`, `KEYS` `:146`).

| Rewrite (site kind) | Apple reason | Stub (`patch_nso.py`) | 2.0.1 count (main / sdk / subsdk0) |
|---|---|---|---|
| `x18` read/write (`x18`) | Apple reserves x18 (platform register) and clobbers it; Horizon code uses it freely | `site_plain` `:239`, `site_cond` `:272`, `site_pcrel` `:289`, `site_br*` `:300-350`, `site_blr` `:337`, `site_sp` `:251` (x18 lives in slot K18) | 36,583 / 16,636 / 2,395 |
| `mrs tpidr_el0` / `msr tpidr_el0` (`tpidr_r/w`) | kernel owns tpidr_el0 | `site_sysreg_read` `:352`, `site_tpidr_write` `:361` → slot KTLS (guest ELF TLS block) | 145 / 2+3 / 0 |
| `mrs tpidrro_el0` (`tpidrro_r`) | Apple's TSD pointer, not Horizon's ThreadLocalRegion | slot KTLR | 0 / 430 / 0 |
| `svc #n` (`svc`) | would trap into XNU | `site_gate` `:378` → gate | 0 / 57 (distinct SVCs) / 0 |
| `mrs cntpct_el0` / `cntvct_el0` | counter rate/scale differs (S2: 31.25 MHz) | `site_gate` | 0 / 43 / 0 |
| `mrs ctr_el0` (`ctr`) | macOS traps it | in-place constant `:1196` | 0 / 2 / 0 |
| `sub sp, sp, xN` (`sp_align`) | Apple faults on misaligned sp | rounds xN up to 16 `:262` | 0 |
| Exclusive windows containing x18 (`W`) | an x18 slot load between LDXR and STXR would break the monitor | whole LL/SC window copied with x18 renamed (`site_window` `:432`, `site_retry_window` `:480`) | 0 / 0 / 0 |
| `ldxr…cbnz…stxr` locks with no barrier → `ldaxr` | Apple cores reorder loads past such a lock; Cortex-A57 did not | in place (`acquire_lock_sites` `:546-565`) | not listed per module |
| Jump tables through x18 (`J`, `T`) | patch stubs need every target | landing pads / compare chains | 12 / 17 / 1 |
| Spin-wait loops (`S`) | a guest poll burns a whole host core | loop copied, back-edges through a spin gate → host yield (`:1296-1594`) | 49 / 5 / 5 |

The four tables:

- **sites** (`<m>.sites.tsv`): every rewritten instruction (`offset kind`), plus `W` (window lo, hi), `J` (br-x18 jump table: base, count), `T` (offset table: all targets), `S` (spin head + back-edges) (`patch_nso.py:1634-1640`). main 36,789 rows, sdk 17,195, subsdk0 2,401.
- **fixups** (`<m>.fixups.tsv`): header only — `PATCH_OFF`, `KEY_PAGE_OFF/SIZE`, `TEXT_END`, `RO_OFF`, `DATA_OFF`, `KEY_*_IDX`, optional `IMAGE_SIZE/FILE_IMAGE_LEN/BACK_*`, `FPS_STATE_OFF`, `UE_*`, capabilities, `BUILD_ID` (`patch_nso.py:1618-1631`). The loader rejects any non-header row (`loader.c:186-227`). 2.0.1 main: text 0x2749480 (≈10.3 M instructions), patch area at 0x3d30000.
- **cuts** (`<m>.cuts.tsv`): registered per-build byte replacements with the original bytes asserted (`CUTS`/`MATCH_CUTS` from `dm\tools\sdk_patches.py` and `dm\games\<id>\patches.py`), plus frame-step stubs and the Unreal allocator call (`patch_nso.py:70-144`). **Pokopia 2.0.1: 0 cuts in every module**; the SDK-wide address-region cuts did not match this sdk (`build201\patched\sdk.match.tsv`).
- **gates** (`<m>.gates.tsv`): `return-point  kind  arg  original-offset` for every block that enters the host: `svc` (arg = SVC number), `cntpct` (arg = destination register), `spin`, `obs` (observation gates: one relocatable instruction replaced by "enter host callback, run the instruction, jump back", `site_obs` `:390`). 2.0.1: main 49 (all spin), sdk 110 (57 svc, 43 cntpct, 5 spin, 5 obs), subsdk0 5 (spin). Pokopia's obs gates (`sdk.obs.tsv`): VammManager `AllocateAddressRegion` entry/result and `FreeAddressRegion` (ids 22/23/24), `StandardAllocator::Initialize` ×2 (id 25) — all iPhone-VA bookkeeping.
- (`changed.tsv`: runs of text words that no longer execute as in the file, for the cheat engine; main 28,970 runs.)

### 2.4 SVCs, system registers, TLS, monitors, threads, signals, dirty tracking

- **SVC path.** Gate block pushes x29/x30, loads the thread's KGATE slot (one of 1,024 static stubs, `entry.S:39-49`), stub loads that thread's gate-stack top, `_dm_gate` (`entry.S:53-159`) saves x0–x30, q0–q31, nzcv/fpsr/fpcr and the block's sp in an 800-byte frame **on a separate per-thread gate stack** (Horizon never writes the guest stack during an SVC; a GC depended on it: `docs\reference\threads.md:51-56`), calls `gate_dispatch(ret, regs)` (`hle.c:112`), which looks the return point up in a hash (`hle.c:95-108`) and calls `svc_dispatch(num, regs, orig)` (`hle_svc.c:443`) → `svc_thread` (`hle_thread.c`, thread/sync SVCs) or the memory/info cases. Results come back in `regs[0..]`. SVCs Pokopia's sdk contains: 0x01–0x1D, 0x1F, 0x21–0x23, 0x25–0x29, 0x2C, 0x2D, 0x32–0x36, 0x40, 0x41, 0x43–0x45, 0x4B, 0x50–0x52, 0x70, 0x72, 0x80. IPC SVCs only reach the "placeholder returns success" default (`hle_svc.c:697-699`): services are replaced above IPC by name, so there is no IPC emulation at all.
- **System registers.** tpidr_el0 → slot KTLS (an 8 KB calloc'd ELF TLS block per thread), tpidrro_el0 → slot KTLR (0x200-byte ThreadLocalRegion + 0x400 thread object, TLR+0x1f8 = thread object, TLR+0x110 = thread id for SDK 22) (`thread.c:14-31`); cntpct/cntvct → `guest_ticks()` (31.25 MHz, patch to `hle_os.c` ≈ line 57; `hle.c:116-121`); ctr_el0 → constant.
- **Exclusive monitors.** Native hardware LL/SC; the host does nothing except keep x18 out of windows and strengthen unfenced lock acquires to `ldaxr`; some games also needed `dmb` cuts for lock-free queues (`docs\reference\threads.md:82-88`).
- **Threads and scheduling.** One guest thread = one pthread (1 MB stack, `hle_thread.c:888`) whose guest code runs on the guest stack the SDK gave it (`_dm_thread_enter`, `entry.S:165-173`; `thread_main`, `hle_thread.c:294-310`); the rest of the pthread stack is its gate stack. All kernel objects and waits sit under one global mutex `G` with a per-thread condvar (`hle_thread.c:95`), handle table of 4,096. Horizon priority → Apple QoS class (`prio_qos`, `:283`), core masks are bookkeeping; **same-core wake handoffs** emulate Horizon's per-core priority preemption (`:120-175`, `docs\reference\threads.md:58-80`). `dm_host_work` / native waits mark host GPU work and host-lock waits as "not occupying a guest core" (`dm.h:11-28`).
- **Pausing/GetThreadContext** (SVC 0x32/0x33, used by Boehm-style GCs): in an SVC → gate frame is the context; in guest code → `pthread_kill(SIGUSR2)`, handler saves the Apple ucontext and waits (`hle_thread.c:330-380`, `:934-965`); also `thread_suspend/thread_get_state` for dumps (`:420`, `:600`).
- **Signals and faults** (`crash.c`): SIGSEGV/SIGBUS/SIGILL/SIGTRAP/SIGABRT/SIGALRM on an alternate stack (`crash.c:189-214`); SIGSEGV/SIGBUS first go through registered **fault hooks** with the ESR WnR bit as is_write (`crash.c:99-104`, `:135-137`); SIGTERM graceful exit (`hle_lifecycle.c`); backtraces walk the guest x29 chain with module-relative PCs.
- **Write-protection dirty tracking** (`gfx\nvn\nvn_dirty.c`): memory-pool pages backing packaged textures are made read-only after upload; a guest write faults → hook marks the page's generation and unprotects (`on_fault`, `nvn_dirty.c:91`); before a texture is used its pages' generations are compared and the texture re-uploaded. Host writes into guest memory (file reads, movie frames) call `dm_guest_write_prepare` first, because a kernel write into a protected page fails with EFAULT instead of faulting (`nvn_dirty.c:1-19`, `dm.h:139-144`). The fault path uses only atomics + mprotect.

### 2.5 What exists only because the code runs natively on Apple

| Mechanism | Exists because | Under x86-64 static recompilation |
|---|---|---|
| x18 sites, K18 slot | Apple platform register | **vanishes** (x18 is `ctx->x[18]`) |
| tpidr/tpidrro sites, KTLS/KTLR slots, key page, `tsd_check`, 4 pthread keys | XNU owns those registers; Apple TSD layout | **vanishes** → two fields in the guest context; TLS/TLR blocks still allocated (`thread.c`) |
| svc/cntpct gate blocks, KGATE stubs, gate stacks, `gates.tsv` hash | SVC traps to XNU | **vanishes** → recompiled `svc #n` calls `svc_dispatch(n, ctx)` directly; host code runs on the host stack, so the "guest stack untouched" property is free if guest sp stays in guest memory |
| ctr_el0 constant, sp-alignment fix | macOS traps / Apple faults | **vanishes** |
| `ldxr→ldaxr` lock fix, `dmb` cuts for lock-free queues | Apple cores reorder more than A57 | **vanishes** for loads-vs-loads/stores-vs-stores on x86-TSO (keep a full fence for `dmb ish` before loads if the recompiler uses plain moves) |
| Exclusive windows copied whole | x18 inside LL/SC | problem vanishes; **LL/SC must be emulated** by the recompiler (CAS-based, 16-byte for LDXP/STXP) |
| Jump-table recovery for `br x18` | patch stubs | **changes**: a static recompiler needs it for every indirect branch; `patch_nso.py`'s analyses (`:627-1170`) and `nso_code.py` (data blocks in text) are a good starting point |
| Spin-wait detection | host cores vs Switch cores | **stays** (performance): insert a yield on the `S` back-edges listed in `sites.tsv` |
| Patch area placement (FAR/TWO), RX sealing, iOS embedding, no JIT | arm64 branch reach, iOS code-signing | **vanishes** (original code bytes only need to stay mapped read-only for code readers: `hle_audren.c:391`, cheats) |
| `HLE_FORCE_BIG` / big-stack entry | host C runs on small guest stacks | **vanishes** if host code runs on host stacks |
| Bool/narrow-arg truncation, variadic caveat (`hle.h:2-3`) | Apple AAPCS64 vs Horizon AAPCS64 | **changes** into the Win64 adapter problem (§3.3) |
| Obs gates for address regions/StandardAllocator, sdk node-area cuts, `DM_ALIAS_SIZE` | iPhone VA ≤ ~6 GB | **unnecessary** with a Switch-sized alias region; obs gates become generic "hook at guest address" in the recompiler |
| 16 KB pages: alias exposure bitmap, MapMemory copying | Apple page size | **simplifies**: Windows commits 4 KB pages (reservation granularity 64 KB) |
| Pause via SIGUSR2 + ucontext; backtrace via host PCs | native registers are the guest registers | **changes**: guest registers live in the context; needs safepoints (or host-RIP→guest-PC maps) |
| Write-protect dirty tracking | zero-copy GPU memory, not XNU | **stays** (VEH + VirtualProtect) |
| Same-core handoffs, Horizon sync semantics, HLE services | Horizon semantics | **stays** |

---

## 3. The HLE layer

### 3.1 By-name replacement

- `HLE_FORCE(mangled, why, ret, cname, args…)` (`hle.h:36-39`) puts `{name, fn, why}` in the `__DATA,swforce` Mach-O section; at relocation every named symbol is looked up there first and a hit **overrides the module's own definition** (`loader.c:293-310`). **848 production entries** across 25 files (largest: `hle_offline.c` 169, `hle_hid.c` 86, `hle_sdk16.c` 74, `hle_misc.c` 70, `hle_fs.c` 70, `hle_oe.c` 51, `hle_account.c` 42, `hle_audio.c` 34, `hle_friends.c` 33, `hle_net.c` 27, `hle_time.c` 26, `hle_movie.c` 24, …; `hle_nvn.c` 1 = `nvnBootstrapLoader`). Unreferenced entries are only counted (`DM_FORCE_STRICT` makes them fatal, `loader.c:177-183`). `HLE_FORCE_BIG` (7 uses) adds a big-stack trampoline (`hle.h:42-48`).
- `HLE(mangled, …)` / `swhle` section: used only for imports **no module defines** (`hle.c:51-56`): 1 `HLE` (`getentropy`, `hle_os.c:114`) + 64 libc passthroughs (`hle_table.c:8-14`) (+ the GL/Vulkan tables, unused by Pokopia).
- Anything else unresolved → one of 4,096 static named stubs that log `UNIMPL <name>` once and return 0 (`entry.S:10-32`, `hle.c:15-21`).
- Services are replaced at the `nn::` client level, so no IPC or service process exists; the SDK's own `nn::os`, libc++ and allocators run as guest code on top of the host's kernel SVCs (`hle.h:32-34`).

### 3.2 NVN entry points

`main` imports only `nvnBootstrapLoader`; its forced take-over returns `nvn_lookup(name)` (`hle_nvn.c:99-109`), and `nvnDeviceGetProcAddress` does the same (`hle_nvn.c:129-131`). `nvn_lookup` linearly maps a name to one of **734** generated thunks (`NVN_NFUNC`, `nvn_count.h`; 197 NVN2 names added by the S2 patch). Each thunk (`nvn_thunks.S`) atomically increments `nvn_slot[i].cnt` and jumps to `nvn_slot[i].fn` (16-byte slot, `hle_nvn.c:44-46`). Slots are filled from the `__DATA,swnvn` section: **407 `NVN()` + 16 `NVN_NOPROTO()` implementations** (each compile-time-checked against `nvn_protos.h`, `hle_nvn.c:64-73`), a `g_nops` list of deliberate no-ops (`hle_nvn.c:1247`), and the rest → `nvn_unimpl_entry` (red log, returns 0). `nvn_entry.S` also holds a timing wrapper and a big-stack entry.

### 3.3 The calling-convention boundary (where recompiled code plugs in)

Today guest code calls host C **directly**: GOT/JUMP_SLOT entries hold host function addresses, and `BL/BLR` lands in host code compiled for Apple arm64. The only adaptations are: Switch callers don't extend <32-bit arguments (host declares them `int/uint32_t` and masks, e.g. `B(x)` in `hle_nvn.c:112`), Switch variadics are in registers (no host C `...`), and SVC/obs/spin go through the 800-byte register frame. That frame (`entry.S:57`: x0–x30 at +0, q0–q31 at +256, nzcv/fpsr/fpcr at +768/+776/+784, guest sp at +792) is already the "guest context" every host SVC handler, observation callback, `thr_svc_enter` (pause context) and `GetThreadContext3` consumes.

For a recompiled build the boundary is the set of **call-outs** and **call-ins**:

| Direction | Today | Recompiled design |
|---|---|---|
| guest → host function (imports) | relocation writes host fn address | write a **host-call token** (an address in a reserved range or a tagged table index) into the GOT; the recompiler's indirect-call dispatcher sees the token and calls a generated adapter `adapter(ctx)` that reads x0–x7/v0–v7 (and guest-stack args), calls the Win64 C function, writes x0/x1/v0 back. ~848 `HLE_FORCE` + 65 `swhle` + 423 NVN signatures are all available as text (macro arguments, `nvn_protos.h`), so adapters can be generated |
| guest → SVC / counter / hooks | gate blocks | direct calls: `svc_dispatch(n, ctx)`, `guest_ticks()`, `obs_fn(ctx, param)` — reuse unchanged if the guest context starts with the `_dm_gate` frame layout |
| host → guest | plain C casts of `guest_sym()` results: `main.c:25-39` bootstrap, `loader.c:660` DT_INIT, `hle_ro.c:75/:89`, `hle_guestthread.c:25-50` (`malloc`, `nn::os::CreateThread/StartThread/WaitThread`), `hle_audren.c` (SDK renderer), `hle_codec.c:11` (Opus ctor), `hle_glslc.c:32/:43`, `hle_movie.c:547`, `hle_thread.c:1130` (CreateSystemEvent), `hle_os.c:144` (`getenv`), NVN command-memory callback (`hle_nvn.c:~850`), `_dm_thread_enter` (thread entry) | a `guest_call(addr, args…)` API that loads args into the context and runs the recompiled function until LR hits a sentinel (as `win\arm64_emu.py` does with `STOP`) |
| host fn pointers planted in guest data | thread entry `spawn_tramp` handed to `nn::os::CreateThread` (`hle_guestthread.c:16-28`), NVN proc addresses, vtables patched with host functions (`hle_uemalloc.c:113-121`, Unreal only) | the dispatcher must accept host tokens at any indirect call/branch, not only GOT calls |

Win64-specific pitfalls at this boundary: `long` is 32-bit (94 non-`long long` uses in `host\*.c` + `hle_nvn.c`; e.g. `loader.c:191`, `:268` parse offsets into `unsigned long`); the libc passthrough assumes the host libc has the Switch ABI — on Windows `strtol/strtoul` return 32-bit `long`, `struct tm` differs from musl's (passthroughs of `mktime/gmtime_r/localtime_r/strftime`, `hle_table.c:14`), `localtime_r` does not exist; AArch64 returns large structs through x8 (e.g. `glslcGetVersion`'s 0x34-byte struct, `hle_glslc.c:31-34`) while Win64 passes a hidden pointer in rcx; HFA float structs come back in v0–v3. Section-based registration (`__attribute__((section("__DATA,swforce")))` + `getsectiondata`) must become COFF `.swforce$m` sections (or a generated table).

---

## 4. Graphics

### 4.1 Split between NVN state tracking and Metal

- **`hle_nvn.c` (2,652 lines, C)**: NVN object semantics with host objects stored **in place in the memory the game allocated for NVN objects** (`nvn_obj.h`, static-asserted sizes); state objects packed in place; draw/state commands **recorded into the game's own command memory** in the `nvn_obj.h` record format; CPU-visible side effects (sync, events, counters, queue) done at submit; window/present pacing (`nvn_pace.h`), frame-step (`nvn_fstep.h`), Switch 2 shader keying (0xe0 control blocks, keyed lazily at first draw: patch at `hle_nvn.c:~610-655`). It has **no Metal API calls**; its Apple dependencies are `mach_absolute_time`, CommonCrypto SHA-256 (shader keys), CF, QoS/`thread_policy`, `sysctl`. It calls 86 distinct `mtl_*` functions (≈30 functional: `mtl_pool_add/del`, `mtl_addr`, `mtl_reg_tex/samp/image`, `mtl_tex_init/release`, `mtl_sub_begin/commit`, `mtl_exec`, `mtl_wait`, `mtl_drain`, `mtl_present*`, `mtl_queue_activate/forget`, `mtl_read_texels`…; the rest statistics), declared in `nvn_mtl.h` and `nvn_obj.h`.
- **`nvn_mtl.m` (12,009 lines, Objective-C)**: replays the record stream into Metal at submit (`mtl_exec`, `nvn_mtl.m:10694`): memory pools as **zero-copy `newBufferWithBytesNoCopy` buffers over guest memory, GPU address = CPU address** (`nvn_mtl.m:527-536`), textures + deswizzle + dirty re-upload, descriptor pools, shader map → metallib loading (`:3642`), pipeline cache/MTL4 archives/hybrid pulling pipelines, render-pass reconstruction with TBDR load/store elision and pass merging, indirect-draw and 8-bit-index workarounds, geometry and transform-feedback emulation (`.inc` files), MTLSharedEvent fences (`:127`, `:425`), CAMetalLayer present thread, MetalFX upscale.
- **Metal-specific fraction.** About **14,500 lines** are Metal-bound (nvn_mtl.m + 4 `.inc` 1,457 + nvn_rec.h 335 + nvn_refl.m 170 + host_msl 49 + small Metal headers 94 + `msl\` 191 + GPU deswizzle 112 + format table 95) against about **4,600 lines** of backend-neutral hand-written graphics C (hle_nvn.c, nvn_obj.h, nvn_tex, nvn_dirty, capture, pace/fstep/hmap/cover/primitive/geometry.h, pso_skey, dmsh_abi, deswizzle, vi/nv): **≈ 75 % Metal-specific**. Within `nvn_mtl.m` only ~1,500 lines name Metal types directly, but the whole file is structured around Metal (render-pass load/store actions, argument buffers, tile shading), so it is a specification to rewrite from, not code to port.
- Generated/third-party in `gfx\nvn` (not counted above): `nvn_thunks.S` 9,554, `nvn_protos.h` 740, `names.txt` 734, `nvn.h` 1,756.

### 4.2 Shader pipeline (Pokopia) and exactly where SPIR-V exists

```
game files: GLSLC 0x00220015 containers in main.rodata, LINKDATA, RDB (installer\profile\…\shader_sources.tsv)
  + run-time shaders recorded on device (DM_NVN_SHDUMP: <key>.ctl + <key>.code; ~140 keys, runtime_keys.tsv)
 → installer\tools\glslc2_extract.py       Ampere programs: <key>-stage<N>-<hdr>.program.bin
 → installer\tools\s2_ctl_scan.py / s2_dumps.py   0xe0 control blocks → host keys (sha256 of control with +0x20..+0x27 zeroed + code)
 → installer\ampere-lower(.exe) via lower_corpus.py   Ampere SASS → Maxwell words (0x50 SPH + instructions + scheduling words)   [GPL-3.0, cut from Eden-NS2 ampere-spirv without its SPIR-V stage]
 → installer\tools\s2_pkg.py               package + shader_map.tsv / shader_facts.tsv / shader_pairs.tsv in nxnative's format
 → dm\gfx\tools\own_compilers.py → dmsh_build.py (DMSH_S2_CS=1: NVN2 compute driver bank packed per program, dmsh_build.py:122, :579-590, :685-700)
     → dmsh_xlate (Ryujinx.Graphics.Shader, TargetLanguage.Spirv + TargetApi.Vulkan, Program.cs:177)
         writes <work>/<key>.spv + <key>.res.json          ← SPIR-V EXISTS HERE
     → Metal-only SPIR-V edits in place: spv_single_lane (vertex, dmsh_build.py:1006), flatten_sets / geometry variants (dmsh_geometry.py:151), atomic cross args (dmsh_atomic.py:43)
     → spirv-cross --msl (dmsh_build.py:1139) → MSL text rewrites (depth entry points main0/main0_z, vs_pad, alpha, logic ops, c1 guards)
     → reflect() from the MSL → <key>_b.json; xcrun metal/metallib → <key>_b.metallib (CAS-cached by MSL)
 → host at run time: key at nvnProgramSetShaders / first draw → b-<chain>/<key>_b.metallib + json
```

- **SPIR-V exists only transiently**, in the per-run temp directory `tempfile.mkdtemp(prefix='dmsh_')` (`dmsh_build.py:1222`), deleted by `shutil.rmtree(w)` at the end (`dmsh_build.py:1291`); the content-addressed cache stores MSL/metallib/json only (`dmsh_build.py:167-200`). Files: `<key>.spv`, `<key>.reader.spv`, `<key>_g<t>.spv` (geometry topologies), with `<key>.res.json` (storage-buffer c0 offsets, fetch1d slots).
- It is **Vulkan-flavoured Ryujinx SPIR-V** (Ryujinx's own Vulkan backend, MIT, is the reference for its descriptor-set conventions and support buffer). For a Vulkan renderer: copy each `.spv` (and `.res.json`) **before** the Metal-only edits, re-answer the host-capability queries for Vulkan (geometry passthrough, depth clip control, `Program.cs:125-142`), and replace MSL-based reflection (`reflect`, `c1_guard.py`, `cb_extent.py`) with SPIR-V-based reflection. The NVN driver-bank conventions (c0/c2 handle tables, `dmsh_abi.h`, `gfx\README.md:62-67`) and the S2 packed compute bank stay.
- Alternative source of SPIR-V: Eden-NS2's full `ampere-spirv` (GPL-3.0) translates Ampere → SPIR-V directly (`installer\ampere-lower\NOTICE.md`).
- Windows can JIT, so the ~140 run-time shaders (black title screen until `nxs2.py refresh`, `ios-port\LOG.md:1177-1183`) could be translated on the fly (ampere-lower + dmsh_xlate in a .NET sidecar or hosted runtime).

---

## 5. Verdict for Windows x86-64 with recompiled game code

### 5.1 Verdict table

| Area | Files | Today (lines) | Verdict | Windows effort (rough) |
|---|---|---:|---|---|
| Guest services (HLE) | 20+ `hle_*.c` service files, `hle_hid/touch/haptics/oe/sdk16/offline`, motion_fusion, pad_slots, romfs headers | ~5,500 | **Reuse** (+ section registration, LLP64 audit) | ~300 changed |
| Graphics state/record layer | `hle_nvn.c`, `nvn_obj.h`, `nvn_tex`, deswizzle, helpers, `dmsh_abi.h`, protos/names | ~4,600 (+3,230 gen/hdr) | **Reuse/port** (replace mach time, SHA-256, QoS; keep `mtl_*` interface renamed `gpu_*`) | ~300 changed |
| Offline tools | `dm\tools\*.py`, `games\*.py`, `shader_split.py`, dmsh_xlate (C#), installer S2 tools, ampere-lower.exe | ~12,000 | **Reuse** (already ran on this PC for build201) | small |
| Loader | `loader.c` | 662 | **Port**: VirtualAlloc mapping, keep ELF RELR/RELA/symbol/force logic; drop key page/TSD/gates; map original code read-only at a fixed base per module so the recompiler can bake addresses | ~400 kept, ~200 new |
| Kernel: memory | `hle_svc.c` | 701 | **Port**: reserve/commit/decommit with VirtualAlloc (4 KB commit simplifies; placeholders make MapMemory a real alias); Switch-sized alias region | ~250 changed |
| Kernel: threads/sync | `hle_thread.c`, `thread.c`, `dm.h` | 1,475 | **Port**: Win32 threads/SRWLOCK/CONDITION_VARIABLE (or winpthreads); QoS → thread priorities; pause via recompiler safepoints | ~400 changed |
| Clock, crash, faults, dirty tracking | `hle_os.c`, `crash.c`, `nvn_dirty.c`, `hle_lifecycle.c` | 634 | **Port**: QPC, VEH/SEH (ExceptionInformation[0] = write), VirtualProtect | ~250 changed |
| Files and saves | `hle_fs.c` | 1,054 | **Port + redesign** commit: no clonefile / RENAME_SWAP on NTFS (journal or copy+rename-with-recovery) | ~300 |
| Audio | `hle_audio.c`, `hle_audren.c` | 1,114 | **Port**: AudioOut logic kept; CoreAudio sink → SDL3 audio stream; audren via `guest_call` | ~200 |
| Movies | `hle_movie.c` (+libvpx, stb_vorbis) / `hle_movie_avc.c` | 839 / 212 | **Port** / **rewrite or drop** (VideoToolbox) | ~100 / ~200 |
| Cheats | `hle_cheats.c` | 2,103 | **Port** data cheats (VirtualQuery-guarded copies); code cheats impossible on recompiled code | ~300 |
| Bootstrap | `main.c`, `hle.c`, `hle.h`, `hle_table.c`, `hle_guestthread.c` | 424 | **Port** onto `guest_call` / host-call tokens; libc passthroughs need adapters (`strtol`, `struct tm`) | ~300 |
| Native-execution plumbing | `entry.S`, `nvn_thunks.S`, `nvn_entry.S`, `hle_bigstack.c`, key page, gate tables, `patch_nso.py` at run time, sdk VA cuts | ~11,600 (9,554 gen) | **Vanishes** | 0 |
| Recompiler glue (new) | guest context (start it with the `_dm_gate` frame layout), dispatcher incl. host tokens, `guest_call`, adapter generator (~1,336 signatures), LL/SC emulation, safepoints, guest-PC tracking for backtraces | – | **New** | ~3,000–5,000 + generator |
| GPU backend | `nvn_mtl.m` + `.inc` + `nvn_rec.h` + `nvn_refl.m` + host shaders + GPU deswizzle + format table | ~14,500 | **Rewrite → Vulkan** (implement the `mtl_*` interface; start without TBDR passes) | ~10,000–18,000 (MVP ~7,000) |
| Shader back half | `dmsh_build.py` MSL stages, `c1_guard/cb_extent/dmsh_*` MSL tools, spirv-cross, metallib cache | ~2,500 | **Rewrite**: keep `.spv`, Vulkan reflection; optional run-time translation | ~1,000–1,500 |
| Front end, input, haptics, motion | `front_mac.m`, `haptics_apple.m`, `motion_apple.h`, `pad_*_mac.h`, `motion_pro_mac.h` | ~2,400 | **Rewrite → SDL3** (window, keyboard/mouse, gamepads, rumble, gyro) against `front.h`/`dm_input.h` | ~1,500–2,000 |
| Apple-only apps/build | `ios\`, `mac\`, `gfx\metal`, `gfx\vk`, `gfx\gl`, `save_sync`, `probe.c`, `memstat.m`, Unreal/Oodle/fpslock | ~12,000 | **Drop** | 0 |

### 5.2 Riskiest parts

1. **GPU memory model.** NVN memory pools are guest memory used zero-copy by the GPU with GPU address = CPU address (`nvn_mtl.m:527-536`, `nvn_mtl.h:12-15`). Discrete Windows GPUs need either `VK_EXT_external_memory_host` imports of guest pool ranges (alignment limits, coherency, PCIe cost) or shadow buffers driven by the write-protect tracker. Everything in the replay assumes unified memory.
2. **Rewriting 12k lines of replay.** `nvn_mtl.m` carries years of per-game fixes inside Metal-shaped code (render-pass reconstruction, clears, indirect draws, 8-bit indices, geometry/XFB emulation, Switch 2 texture flags). Vulkan removes several workarounds (native GS, XFB ext, uint8 indices, custom border colours) but the semantics must be re-derived.
3. **The ABI boundary.** ~1,336 host entry points written for "Apple arm64 with Switch quirks" must become generated Win64 adapters; LLP64 `long`, `struct tm`, `strtol`, x8 struct returns, HFAs and variadics will cause silent corruption if missed.
4. **Two-way calls between recompiled and host code.** Host→guest (bootstrap, DT_INIT, audio renderer, threads, Opus, NVN memory callback) and guest calls through host pointers planted in guest data need a dispatcher that treats host tokens as first-class targets, plus guest-faithful LR/stack so the SDK's unwinder and setjmp work.
5. **Threads without native registers.** SetThreadActivity/GetThreadContext3, crash backtraces and the same-core handoff logic currently read real registers; recompiled code needs safepoints or RIP→guest-PC maps. (Pokopia's engine is Koei Tecmo's, so GC pausing may not be exercised; verify with `DM_SVC`.)
6. **Transactional saves on NTFS** (no clonefile/RENAME_SWAP); losing atomic commit risks save corruption.
7. **Shaders.** SPIR-V must be captured before Metal-only edits and reflected anew; ~140 run-time shaders must be handled (offline refresh or run-time translation); Switch 2 keys are only final at first draw.
8. **Recompiler-side semantics nxnative never needed:** LL/SC emulation, ARM FP behaviour (FPCR rounding/FZ/default-NaN, FMIN/FMAX NaN rules) on x86, and jump-table/CFG recovery for all of main (≈10.3 M instructions in 2.0.1).
