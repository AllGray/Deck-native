# Milestone 4 report: complete CPU and system

2026-11-03. M4's goal (DESIGN §6): the game runs its title-screen logic with a null renderer, and the recompiled code is
checked function by function against the real game. **Done**, apart from the deliberately deferred exception, longjmp and fiber
bridges (none of them runs in normal play). Details: [m4-title.md](m4-title.md), [m4-swapin.md](m4-swapin.md), [s2tex.md](s2tex.md).

## Results

**Windows (track A):** Pokopia 2.0.1's recompiled code boots past audio and loading into its title loop and keeps running.
Reproduced on the build PC: 100 s without a stop, **59.90–59.92 presents/s** (median frame 16.6 ms, p99 under 17.9 ms). Against the
Mac: 1,417 of the title scene's 1,419 functions run (the other two fire only when a real recorder fills command memory), and the same
30 RomFS files open in the same order. CPU at the title is about 0.3 of a core.

**Mac swap-in (track B, D15.2):** the lifted code compiled for arm64 ran **inside the real game** in place of the originals: the
boot path (37,539 functions), then all of main (149,688), then main + sdk + subsdk0 (200,406). Every run reached gameplay through
the title and the save load with no trap or crash; logs and screenshots match native runs. **No divergence traced back to the lifter
or the semantics.** The few differences are timing-dependent paths of the game that native runs show too.

**Switch 2 textures (s2tex):** a clean-room MIT implementation of the Switch 2 block-linear layout, reproducing 591 real game
textures bit for bit in C and Metal. It replaces Eden-derived code in pokopia-native's host patch (that swap is in progress) and is
M5's texture decoder.

| Commit | What |
|---|---|
| `23befab` | s2tex |
| `d911809` | both M4 tracks: audio, codec, misc HLE; the null device's frame loop; timer compensation; the takeover check; atomic saves; the swap-in harness |

24 tests pass on Windows.

## Found and fixed along the way

- 16 touch-screen take-overs the adapter generator had missed (found by the new takeover check on its first run).
- Windows wakes timed sleeps 0.4–0.5 ms late, which held the title at 59.3 Hz; sleeps now compensate.
- Harness-side issues on macOS 27 (random reservations below 2^39, the runtime moving the guest heap); all fixed in the harness.
- A licence problem in pokopia-native's host patch (GPL code labelled MIT): relabelled and pushed, with the clean replacement on the way.

## Deferred, by design

- C++ exceptions, longjmp and fibers (D5): none run in normal play; the four sdk context functions trap cleanly.
- `hle_movie.c`, `hle_sf.c`, `hle_swkbd.c`: none ran on the Mac up to gameplay.
- Loading is about 5 s slower than on the Mac (to look at with real rendering in place).

## M5: the Vulkan renderer (next)

Goal: the title screen drawn, then gameplay. This is the largest milestone (research/02 estimates 10–18k lines, a minimal first
version about 7k).

1. **NVN command recording:** port nxnative's `hle_nvn.c` recording layer (plain C, no Metal) behind the device, so command streams
   exist to replay.
2. **Shaders:** keep the SPIR-V our pipeline already produces before the Metal edits (`dmsh_build.py`), reflect from it, translate
   run-time shaders on demand with a versioned cache, and initialise unwritten vertex outputs to (0,0,0,1).
3. **Vulkan bring-up:** SDL3 window, swapchain, present; then memory pools as device-local copies driven by the dirty tracking (no
   giant host-memory import: research/01 lesson 10).
4. **Resources:** textures through s2tex, ASTC transcoded to BC7 with a cache, format fallbacks applied to every view, samplers,
   texture sharing with the icon fix's rule from day one.
5. **Pipelines and draws:** render passes, pipelines from SPIR-V with an incremental pipeline cache, descriptors, draws, compute,
   copies, queries; resolution-independent from day one.
6. **Exit:** the title screen drawn and matching the Mac's screenshot; then gameplay.
