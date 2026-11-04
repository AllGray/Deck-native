# M6 phase B: 1.0.5, the renderer's gaps, wider gameplay, controllers, performance

Status, 2026-11-04: **Pokopia 1.0.5 plays on Windows too**, from its own game DLL: title, field, bag, PC, tool, menu, walking, Save & Quit, at 59.2–59.6 presents/s,
and a save it writes loads on the Mac's 1.0.5 build at the same spot. The renderer gaps of M5-C / phase A are implemented and tested on the GPU (dual-source
blending, the alpha test, masked colour and stencil clears, GPU copies read back into storage-backed textures), pipelines are made ahead in the background from a
device-independent list, and the validation layer reports no error over the longer route on either version. Controllers get rumble, gyro and accelerometer,
player lights and a controller check for the user's first real pad. Gameplay is back at 60 presents/s when the window is not in front (Windows 11's EcoQoS had
cut it to 40), and the render thread's own share dropped by about a third. In the working tree for review (nothing committed).

| | |
|---|---|
| New | `src/nxrt/vk/shaders/clear.vert`, `clear_f.frag`, `clear_u.frag`, `clear_i.frag` (masked clears), `test_dual.frag`, `test_tri.vert` (tests); `scripts/test_play.ps1` |
| Changed | `vk/vk_replay.c` (variants at draw time, masked clears, GPU→storage readback, depth-compare twins of every view type), `vk/vk_shader.c/.h` (the dual-source and alpha-test SPIR-V edits, fragment variants, the shader table locked, lookup by key), `vk/vk_pipe.c/.h` (push constants for the alpha test, the pipeline list and the prewarm, the maps locked, the last pipeline remembered), `vk/vk_hmap.h` (hashing 8 bytes a step), `vk/vk_submit.c` (uploads recorded only by the thread recording the submission), `vk/vk_tex.c` (a missed first content recorded at a later use), `vk/vk_window.c` (the front end's tick), `front/front_sdl.c/.h`, `front/front_map.h` (rumble, motion, player lights, the controller check), `gfx/nxs_rt.c/.h` (`nxs_peek`), `plat/plat_win32.c` (no power throttling), `scripts/play.ps1`, `src/nxrt/CMakeLists.txt`, `vk/CMakeLists.txt` |
| Tests | `nxrt.vk_replay` 52 → 60 checks (masked colour and stencil clears, GPU→storage readback, dual-source, alpha test GREATER / LESS, both together); `nxrt.pad_sdl` extended (rumble through the game's vibration calls, motion through the six-axis read, the check's F5); new `scripts.play_defaults`. 43 tests, all pass with `NXW_DUMP` (§8) |
| Game DLLs | `_local/nxlift/v105/game.dll` (1.0.5, new); `_local/nxlift/m3/game.dll` (2.0.1, unchanged) |
| Contract | unchanged (`gfx_backend.h`, `nvn_obj.h`, `dmsh_abi.h`) |

## 1. Pokopia 1.0.5

**The lift.** `nxlift <1.0.5 ExeFS> --db _local\analyze\1.0.5\program.db --out _local\nxlift\v105 --jobs 4 --dll _local\nxlift\v105\game.dll` (idle priority by
default): 303 partitions, **197,656 functions, 12,117,636 instructions, 0 ops without semantics**, 378.5 MB, 563.5 s wall (9 min 25 s). The object cache found
nothing to reuse: sdk and subsdk0 are the same modules as 2.0.1's (same build IDs), but their partitions' IR differs (the bindings into main). 1.0.5's
program.db is M4's rebuild (`installer-test-win\build\extract\ExeFS`, main `816c5358…`); the takeover check against today's registry: 1,277 forced bindings, 0
differences.

**Shaders.** `nxshader.py build ..\installer-test-win\build --jobs 2` into the shared cache: 3,953 shaders (cs 107, gs 2, ps 3,574, vs 270), **3,897 already there
from 2.0.1, 56 translated, all 3,953 pass `spirv-val`**; the profile's 140 run-time keys of 1.0.5 are all in it (131 s).

**Per-build profile entries.** `look_cam`'s 1.0.5 entry (`816c5358e8178d80 34513d0 …`) finds the camera ("look: direct (written into the camera…)"); mouse look
and the scripted `look` turns work as on 2.0.1. The display version is the game's own ("Ver. 1.0.4" on the title on both hosts: the profile names no 1.0.5
string).

**Against the Mac's 1.0.5** (`~/installer-test/build`, read only; the phone's 1.0.5 save backup, copied):

| | Windows | Mac |
|---|---|---|
| title (`DM_FIXED_TIME`, `TZ=UTC`) | 31.0 dB against the Mac (the blinking "PRESS A BUTTON" and the bokeh) | |
| the route (§3) | same screens at every step | |
| Save & Quit | "save commit #1 … (clone + swap 32 ms)"; G00/CMNDAT.BIN and G00/STGDAT01.BIN changed, the rest byte for byte the backup's (as 2.0.1 in phase A) | |
| that save reloaded | 24.2 / 24.9 dB against the Mac's reload of it (same spot, the same 60° look turn; an NPC animates) | loads it |
| validation (`NXRT_VK_VALIDATION=1`, the route) | **0 errors** | |
| presents/s in the field | 59.2–59.6 | |

**Running 1.0.5.** `play.cmd` / `play.ps1` take 1.0.5's files as they take 2.0.1's: `-ExeFS` / `-RomFS` of a 1.0.5 extract and `-Dll` its game DLL. On this PC
`_local\play-local.ps1 -V105` plays on a copy of the phone's 1.0.5 backup (`_local\play\save105`, its own work folder `_local\play\work105`). A save that 2.0.1 has
upgraded may not load in 1.0.5: keep the versions' saves apart.

## 2. The renderer's gaps

| Gap | Now | Evidence |
|---|---|---|
| Pipelines made at first use (cold-cache stalls) | **made ahead in the background** from a list (below) | empty pipeline cache, route to the field: without the list 179 + 22 pipelines on the render thread (627 ms, worst 35 ms); with it **201 made ahead in 0.7 s by two background threads, 1 on the render thread (5 ms)** |
| First use of a never-initialised image (VUID-vkCmdDraw-None-09600, phase A) | two ways an image's first layout could be lost are closed (below) | not reproduced in 3 validation runs (one before these changes); 0 errors over the route on both versions |
| A 2D view where a shader declares an arrayed or cube depth image (VUID-vkCmdDrawIndexed-viewType-07752: **found this phase**, from phase A's depth-compare stand-ins) | the stand-in has six cube-compatible layers and a view of each type | 10 reports (the layer's limit per error) at the switch into the field before, 0 after |
| Dual-source blending | the shader's dual-source variant, the SRC1 factors passed through | `nxrt.vk_replay`: ONE + SRC1_COLOR over white gives output 0 + output 1 |
| The alpha test | the shader's alpha-test variant, function and reference as push constants | GREATER 0.5 keeps alpha 0.6, discards 0.4; LESS the reverse, on an early return too; with dual-source |
| Masked colour and stencil clears | a clear draw through the mask | R and B cleared in the scissor, G / A and the rest kept; stencil 0x0f then the top four bits to 0xa5 gives 0xaf |
| A GPU copy into a storage-backed texture | read back into its storage when the submission completes | `nxrt.vk_replay`: the storage holds the copied texels |

**The pipeline list and the prewarm** (`vk_pipe.c`). Every graphics and compute pipeline the replay makes is appended to `pipelines.tsv` beside the pipeline
cache: its shaders by key with the variants that made them (vertex input retyping `/v<mask>:<types>`, fragment variants `/f<flags>`) and the rest of its key in
hex. Nothing in it is device-specific, so a list made on one PC serves another. At start two threads at below-normal priority make every listed pipeline whose
shaders the cache has (`nxs_peek`: a quiet lookup), from this folder's list and from `NXRT_VK_PIPELINE_SEED` (a list shipped with a build), while the game boots;
the replay's lookups then find them. The pipeline and shader maps are locked for this (SRW locks; a pipeline two threads make at once is kept once). The replay's
own misses are still made on the spot. A list of the 201 pipelines of the route is `_local\m6b\seed-201.tsv` (22 KB). `NXRT_VK_PREWARM=0`, `NXRT_VK_MANIFEST=0`
turn the two parts off. *Caveat:* NVIDIA's own shader disk cache was warm in both measurements; phase A's ~2 s stalls came with it cold, and clearing it is a
system-level change this work did not make.

**First use.** Reading where an image's first transition is recorded found two ways for it to go wrong: (1) a texture resolved by another thread while a
submission was being recorded put its initial clear into *that* submission's upload command buffer (a race on the command buffer, and lost when that one had
already ended); uploads and staging now belong only to the recording thread (`vks_upload_cb` / `vks_owner` check the thread), another thread uses a one-shot of
its own; (2) when nothing could record the first content at creation, the image never got one: a later use now records it (logged). The error did not reproduce
before or after, so these are not proven to have been its cause.

**Dual-source blending and the alpha test** follow nxnative's Metal replay: target 0 blending with a SRC1 factor takes the shader's dual-source variant when it
writes output 1 and the device has `dualSrcBlend` (`pso_reads_src1`, `fs_dual`; output 1 becomes Location 0 Index 1, as nxshader's `DualSource` pass does); an
alpha function other than ALWAYS takes the alpha-test variant, which compares colour 0's alpha with the reference before each return of the entry point and
discards on failure, the function and reference being data (push constants), not pipeline state, so one pipeline serves every value (`at_fn`, `at_bind`); no
test when colour 0 goes to an integer target (`atest_key`). Both are SPIR-V edits made at run time (`vkspv_dual_source`, `vkspv_alpha_test`), cached per shader,
and named in the pipeline list. Other targets blending with SRC1 still count it as zero (Vulkan allows one dual-source attachment), logged once.

**Masked clears** follow `clear_color_tri` / `clear_ds_tri`: a triangle over the target level within the scissor, the value converted for integer targets as the
Metal path does, one pass per layer; a full mask still clears with the attachment's load operation. **GPU→storage copies** follow `mirror_readback` /
`mirror_rb_land`: the region is copied into the submission's arena after the copy and written into the texture's storage at completion.

**Not hit by gameplay yet.** None of the dual-source, alpha-test, masked-clear or read-back paths fired in this phase's runs (title to field, the route, the menus,
Save & Quit, on both versions): each logs once when it first does. Still open from M5-C: the indirect-quad and indirect-count arguments read from guest memory at
record time; the 2D default texture where a shader declares 3D (it reads 0; Metal cannot do better); transform feedback.

## 3. Wider gameplay against the Mac

`_local/m6b/m6bdrive.py --plan route` drives both hosts the same way (screens, not time): the title, the field, then the bag (X), the PC ("A Check": Challenges,
Shop, Link Play, Get Items), the Water Gun on the ground (ZR), the menu (+: the Pokédex, 11/4 12:00 PM), a longer walk (down, a run to the right, up) and a
dialogue. Both saves start beside the PC. Windows ran the isolated build at 59–60 presents/s, the Mac about 41 frames/s (its M1 shared, 2.0.1). PSNR per step (`_local/m6b/cmp-*`):

| Step | 2.0.1 | 1.0.5 | |
|---|---:|---:|---|
| title | 46.3 | 31.0 | 1.0.5: "PRESS A BUTTON" mid-blink |
| standing (r00) | 27.9 | 24.0 | NPC animation, cloud shadows (phase A §2) |
| bag (r01) | 30.3 | 26.3 | the PC screen animates |
| PC menu (r04, r05) | 33.4, 33.7 | 30.2, 30.8 | 1.0.5's PC has fewer entries, on both hosts |
| at the PC, the tool, walking (r03, r06–r08, r10–r17) | 15.8–18.9 | 15.1–18.6 | same places and screens; the follow camera lags the player by a different amount on each host |
| menu (r09) | 21.2 | 19.3 | same panel and clock |
| a dialogue (route 1, r10–r21, 2.0.1) | 17.9–19.6 | | the same NPC's lines in the same order on both hosts |

No difference was found that is the renderer's: every pair shows the same scene; what differs is animation phase, the clouds' shadows and the camera's lag (the
camera follows the player with smoothing, which depends on the exact frame times of each host). One 2.0.1 run-time shader new to our runs (`bf7bd31acd899c50`,
walking in circles) was missing from the cache: logged, dumped and translated on demand, as M5 designed.

## 4. Controllers

**Rumble** (`front_sdl.c`, `front_map.h`). The game's vibration (`hle_hid.c` → `hle_haptics.c`) reaches `SDL_RumbleGamepad` on the window thread: the haptics
sink keeps each player's levels and wakes the window thread, which sends them and renews a running effect every 400 ms (1 s effects: a process that dies leaves no
motor running). The mix is nxnative's DualShock 4 route, "as the emulators feed SDL": the strong motor follows the low band, 0.85 from the left actuator and 0.15
from the right, the weak motor the high band the other way round. `DM_RUMBLE=0` off, `DM_RUMBLE_GAIN` (0–3), `DM_RUMBLE_MIN` (0–0.9). A player has vibration
devices only while one of its controllers can rumble; out of focus rumble is suspended (as front_mac.m).

**Gyro and accelerometer.** A controller with both sensors has them switched on; each gyro sample goes, with the latest accelerometer sample, to
`dm_motion_set_hid` in the Switch's frame: SDL's x right, y out of the face, z toward the player, m/s² as specific force, becomes the Switch's x right, y away,
z out of the face, G as gravity direction (`front_motion_frame`). `DM_MOTION=0` off. **Player lights:** `SDL_SetGamepadPlayerIndex` (Switch Pro, DualSense).

**Mappings.** Xbox, DualSense and Switch Pro all go through SDL3's gamepad layer, which names buttons by position: face buttons by position as on a Switch (right
A, bottom B, top X, left Y), so a Switch Pro maps label for label and an Xbox or PlayStation pad by place (`DM_PAD_FACE=labels` maps an Xbox pad's letters
instead); shoulders L / R, triggers ZL / ZR past a quarter, Start + / Back −, sticks with the radial dead zones (0.08 / 0.92), D-pad. Guide, Share / Capture,
the touchpad and paddles have no function (as on the Mac).

**Tested with SDL virtual pads** (`nxrt.pad_sdl`): the game's `GetVibrationDeviceHandles` / `SendVibrationValue` on No2 reach a virtual pad's motors as 0.85 /
0.85 (55,705) and stop; a pad lying flat and turning left reads (0, 0, −1) G and one turn a second about z on player 2's six-axis sensor; the check's F5 runs the
strong motor for a second. **Not tested with a physical controller** (none is attached here).

### The user's controller check (a few minutes)

1. Plug in the controller (USB or Bluetooth). Close Steam first, or turn off Steam Input for it: Steam can hide a pad behind a virtual Xbox one.
2. Start the game with the check on: `powershell -ExecutionPolicy Bypass -File _local\play-local.ps1 -PadCheck` (or `play.cmd … -PadCheck`). Load into the field.
3. The window title now shows every controller live: its player, the Switch buttons held, both sticks, and the tilt. The log (`_local\play\work\logs\nxrt-*.log`,
   lines starting `pad check:`) has the controller's type, USB id, connection, battery, and whether it has rumble, gyro, accelerometer and player lights.
4. **Buttons:** press each once. Each press is logged as `-> Switch <button> (held on the controller: <place and printed label>)`. Expect right = A, bottom = B,
   top = X, left = Y; bumpers L / R; triggers ZL / ZR; Start + and Back −; stick clicks; the D-pad.
5. **Sticks:** turn both sticks slowly round their edges. Once a second the log gives the largest deflection so far: it should reach 1.00 on both sticks; a stick
   at rest should read 0.00.
6. **Rumble:** press F5 (strong motor, 1 s), F6 (weak motor, 1 s), F7 (a vibration through the game's own path). Then play: the game's own rumble is logged as
   `rumble:` lines.
7. **Gyro:** lay the controller flat: `acc` should read about 0 0 −1; tilt it forward, back and sideways and turn it: `acc` and `gyro` follow (once a second).
8. **Report back:** the controller's name and type, the first `pad check:` lines (type, USB id, capabilities, mapping), any button that played the wrong Switch
   button, the largest stick deflections, whether F5 / F6 / F7 were felt, and the flat `acc` reading. Paste the `pad check:`, `rumble:` and `[hid] motion` lines.

## 5. Performance

Measured with `prof` (`_local/m6/m6drive.py --plan perf`: 8 s standing, 8 s walking in circles, 6 s in the menu; nxrt with `-DNXRT_PDB=ON`):

| Change | Before | After |
|---|---|---|
| **Power throttling off** (`plat_win32.c`: `SetProcessInformation(ProcessPowerThrottling)`, execution speed and timer resolution): Windows 11 runs a process whose window is behind others at EcoQoS (efficiency cores, lower clocks) and ignores its timer resolution | field 39–41 presents/s with the window not in front (both versions); render thread 84–87 % of a core, uploads 4.4 ms a frame | **59.9–60.0 presents/s**; render thread 72 %, uploads 2.2–2.4 ms a frame |
| **Hashing 8 bytes a step** (`vk_hmap.h`) and the last pipeline remembered (`vkp_graphics`): the ~550-byte pipeline key was hashed byte by byte at every draw (6.3 % of the render thread) | render thread 69 / 61 / 76 % of a core (standing / walking / menu), nxrt's share of it 21.4 / 19.5 / 23.4 % | **61 / 54 / 59 %**, nxrt's share 12.0 / 11.3 / 12.7 %; `vkp_graphics` gone from the top of the profile |

Both kept the screenshots (the route's pairs above were taken with them) and the tests. **Descriptor sets were not changed**: at 60 Hz the render thread waits in
44 % of its samples; the driver (descriptor writes and draws together) is 11–15 % of it, so push descriptors or descriptor
buffers would save a few percent of a core at the cost of two layouts per shader (one set per stage, and only one push set per layout). Left for M7 with the
numbers above. Write faults and uploads per frame are as in phase A (≈450, 6.7 MB).

## 6. play.ps1

`-Exe` and `-Work` defaults are resolved in the script body, not in `param()`, where some hosts leave `$PSScriptRoot` empty (a script block, `-Command`):
nxrt.exe is found beside the script, else under the current folder; `%LOCALAPPDATA%` falls back to the known folder; a relative `-Work` becomes absolute.
`-DryRun` prints the command line; `-PadCheck` turns the controller check on (§4). `scripts.play_defaults` checks it started three ways (`-File` from another
folder, `play.cmd`, a script block).

## 7. Left for M7

- A real controller: the user's check (§4), then per-pad calibration and settings in the launcher.
- The installer: build the 1.0.5 DLL as it builds 2.0.1's; ship a pipeline list per game build (`NXRT_VK_PIPELINE_SEED`) next to the shader map.
- Descriptor sets (§5), the recompiled code's own speed (DESIGN §7).
- The renderer paths not yet met in gameplay (§2), and the indirect arguments read at record time.

## 8. Reproducing

```
scripts\build.cmd                                              (this phase built in an isolated copy, see the note below)
ctest --test-dir build\Release                                 (NXW_DUMP=<2.0.1 ExeFS>; CMake 4 from msys64)
nxlift <1.0.5 ExeFS> --db _local\analyze\1.0.5\program.db --out _local\nxlift\v105 --jobs 4 --dll _local\nxlift\v105\game.dll
py -3 -I src\nxshader\nxshader.py build ..\installer-test-win\build --jobs 2 --report _local\nxshader\report-build105.txt
_local\m6b\drive.ps1 -Name <run> -Version 105|201 -Plan route|walk|save|load|perf -Drive _local\m6b\m6bdrive.py [-Env @{ NXRT_VK_VALIDATION = "1" }] [-Cache <dir>] [-Save <dir>]
Mac: cd ~/m6b && VER=105 ./m6brun.sh <tag> 330 &  python3 m6bdrive.py --ctl ~/m6b/runs/<tag>/ctl.txt --out ~/m6b/runs/<tag>/shots --ref ~/m6b/ref --plan route
py -3 -I _local\m6\cmp.py <mac shots> <windows shots> <out>
```

*Note on the build.* While this phase ran, another session's co-op work in the shared tree (`src/coop`, `hle_offline.c`, `tests/test_coop.c`) did not compile, so
this phase's changes were built and tested in an isolated copy, `<work>\m6b-isolated` (HEAD plus exactly this phase's files, `_local` joined to the real one),
by `_local\m6b\sync-iso.sh`.

Sources consulted are logged in `docs/PROVENANCE.md` (2026-11-04, M6 phase B).
