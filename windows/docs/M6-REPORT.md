# Milestone 6 report: playable

2026-11-04. M6's goal (DESIGN §6): input with direct look, audio, saves, both versions, and performance work, each optimisation gated by the differential
tests. **Done**, with one part the user has to check: physical controllers (none is attached to the build PC; the controller check is ready for the first one).
Details: [m6-input-gameplay.md](m6-input-gameplay.md) (phase A), [m6-phase-b.md](m6-phase-b.md) (phase B).

## Results

**Both versions play on Windows.** Pokopia 2.0.1 (M3's game DLL) and 1.0.5 (its own DLL, lifted this phase: 197,656 functions, 378.5 MB, 9.5 minutes at 4 jobs on
idle priority) boot to their titles, load their saves and play: walking, direct mouse look, the bag, the PC, the tools, the menus, dialogues, Save & Quit. In the
field both run at **59–60 presents/s**, with the window in front or not. Sound comes from the sound card (SDL3, WASAPI), with no underruns over 60 s.

**Against the Mac** (the real game's code under nxnative, the same saves, the clock fixed): the titles match (2.0.1 46.3 dB, 1.0.5 31.0 dB with the blinking
prompt), and along a route through the bag, the PC, the tool, the menu, a longer walk and a dialogue every step shows the same screen on both hosts; the
differences are animation phase, the drifting cloud shadows and the follow camera's lag. Saves cross: written on Windows, they load on the Mac at the same spot
(2.0.1 and 1.0.5), and the Mac's 2.0.1 saves load on Windows (phase A). The validation layer reports **no errors** over the route on either version.

**Input.** Keyboard and mouse with the Mac's layout and direct look through the profile's `look_cam` (both builds); scripted input (`DM_KEYS`, `DM_CTL`) for tests;
controllers through SDL3 with player slots, face buttons by position, radial dead zones, **rumble** (the game's two actuators onto the strong and weak motors),
**gyro and accelerometer** into the six-axis sensor, player lights, and a **controller check** (`play.ps1 -PadCheck`) for the user's first real pad.

**Renderer.** Phase A fixed the replay in gameplay (bounced storage buffers, depth comparison on colour textures, indirect quads, view usages, query resets).
Phase B made pipelines ahead in the background from a device-independent list (an empty pipeline cache: 201 pipelines made ahead in 0.7 s, 1 left on the render
thread, against 201 and a worst stall of 35 ms there), and implemented the gaps: dual-source blending, the alpha test, masked colour and stencil clears, GPU copies
read back into storage-backed textures, and depth-compare stand-ins of every view type (a validation error phase A had introduced). Each is tested on the GPU.

**Performance.** 24–27 presents/s in the field before M6, 59–60 after: `VirtualQuery` taken off the hot paths (phase A), Windows 11's power throttling turned
off for the process (40 → 60 presents/s with the window behind others), the pipeline key hashed 8 bytes a step and the last pipeline remembered (the render
thread's own share of its time from ~21 % to ~12 %). Every change kept the tests and the screenshot comparisons.

| Commit | What |
|---|---|
| `2fc98ba` | phase A: 2.0.1 playable (input, sound, saves, gameplay replay fixes, the profiler) |
| (working tree) | phase B: 1.0.5, the renderer gaps, the pipeline prewarm, rumble and motion, power throttling, the faster lookups, `play.ps1` |

43 tests pass (`NXW_DUMP` set to the 2.0.1 ExeFS).

## Found along the way

- Windows 11 throttles a process whose window is behind others (EcoQoS, and timer resolution requests ignored): the field ran at 40 presents/s whenever the game
  window was not in front. Measured and fixed in phase B; it had looked like a 1.0.5 slowdown at first.
- Phase A's depth-compare stand-ins were 2D views where shaders declare arrayed and cube shadow samplers (VUID-vkCmdDrawIndexed-viewType-07752).
- A texture resolved on another thread could record its first content into the recording thread's command buffer; uploads now belong to the recording thread.
- One 2.0.1 run-time shader was new to our runs; the on-demand path dumped and translated it, as M5 designed.

## Not done, or not verified

- **Physical controllers**: only SDL's virtual pads. The user's check is in m6-phase-b.md §4.
- The dual-source, alpha-test, masked-clear and read-back paths have not been met in gameplay yet (each logs when it first is).
- Phase A's UNDEFINED cube map at the switch into the field did not reproduce in three validation runs; the two ways it could happen that the code showed are
  closed, which is not proof they were its cause.
- Cold-start stalls with NVIDIA's own shader cache cold were not re-measured (clearing it is a system change); the prewarm covers our side.
- Descriptor sets (≈5,500 a frame) are unchanged: measured, the driver work behind them is a few percent of a core with the render thread idle 44 % of the time.

## M7: release

1. **The installer's Windows target** (DESIGN D12): the lift (both versions, by build ID), nxshader's corpus build, the game DLL and nxrt in our builder and its
   window, with a pipeline list per game build shipped beside the shader map (`NXRT_VK_PIPELINE_SEED`), so the first launch makes its pipelines ahead.
2. **The launcher**: controls (mouse sensitivity, face-button layout, dead zones, rumble strength), the save folder, fullscreen, the version.
3. **Controllers**: the user's check on Xbox, DualSense and Switch Pro pads, then per-pad calibration; Joy-Con sideways.
4. **A release build of the tools** in CI (no game data), the docs for players, and the public release as xFractals, with the user's OK.
5. Later performance work, measured as in M6: descriptor sets (push descriptors or descriptor buffers), the recompiled code's own speed (DESIGN §7: register
   traffic at call sites, `volatile` loads), upload and write-fault costs.
