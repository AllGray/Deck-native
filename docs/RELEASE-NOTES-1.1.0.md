# pokopia-native 1.1.0 (draft, not published)

*Draft for the release page. Fill in the co-op section and the checksums on release day (§ "Release day" at the end); the
rest is written against the release candidate.*

---

**Pokopia now runs natively on Windows too**, Link Play works between our builds, and the builder works when your build
folder is on another drive. One release, one download per computer:

## Which download do I need?

| I have… | and want to… | Download |
|---|---|---|
| a Windows PC | play on Windows | [Pokopia-Native-for-Windows.zip](https://github.com/xFractals/pokopia-native/releases/latest/download/Pokopia-Native-for-Windows.zip) |
| a Windows PC | build the iPhone app | [Pokopia-Native-for-Windows.zip](https://github.com/xFractals/pokopia-native/releases/latest/download/Pokopia-Native-for-Windows.zip) |
| a Mac | play on the Mac | [Pokopia-Native-for-Mac.zip](https://github.com/xFractals/pokopia-native/releases/latest/download/Pokopia-Native-for-Mac.zip) |
| a Mac | build the iPhone app | [Pokopia-Native-for-Mac.zip](https://github.com/xFractals/pokopia-native/releases/latest/download/Pokopia-Native-for-Mac.zip) |

- **Co-op needs the same pokopia-native version on every device.** Update every device to 1.1.0 before playing together.
- **Each player needs their own save.** The game refuses a second copy of the same save.

As always: **your own copy, your own dump.** Neither download contains anything of the game; everything is made on your
computer from your dump. Please do not share build folders, apps, IPAs, `game.dll`, shader files or saves.

## New

### The Windows game

The "for Windows PCs" download builds **Pokopia for Windows** from your dump: the game's arm64 code is recompiled for your
PC (our own recompiler, through LLVM) and runs on our own runtime with a Vulkan renderer. No emulator.

- Both game versions, **1.0.5 and 2.0.1**.
- **60 fps** in the field on the test PC; mouse look written straight into the game's camera, as on the Mac;
  controllers (Xbox, PlayStation, Switch Pro) as Switch controllers, with rumble and motion; SDL3 sound.
- **Your save**: `%APPDATA%\Pokopia Native\<version>\save`; **Import save…** (or `nxs2.py import-save`) brings yours from
  Eden, the iPhone or the Mac. Windows saves load on the Mac and the iPhone.
- **Run-time shaders** are recorded and translated on the spot the first time the game makes them; no refresh step.
- **No installer, no administrator rights, no compiler**: the download brings its own tools in `windows\bin` (their
  notices in `windows\bin\THIRD-PARTY-NOTICES.txt`). It needs Windows 10/11 64-bit, an AVX2 CPU, a Vulkan 1.3 GPU,
  Python 3.12, the .NET 10 runtime and git, and about 12 GB per game version. The build took 20 minutes on the test PC (4 workers; longer on smaller PCs), at idle
  priority. Details: [docs/WINDOWS.md](docs/WINDOWS.md).
- The source is in `windows/` (MIT).

### Link Play between our builds

*To be written when co-op lands (status on 2026-11-04: Mac and iPhone apps in the patch, Windows hosts and joins; local
network play in progress). Cover: how to host and join on the local network, which devices were tested together, the
firewall / local-network prompts, the limits (no Nintendo servers, no internet play through anyone's relay).*

### Builder

- **The build folder on another drive** than the tool now works (nxnative's `DM_GAMES_DIR` is split on the system's
  path separator, not on `:`).
- **Windows without Apple's tools**: `nxs2.py setup --no-ios` (or Setup with "iPhone IPA" unticked) sets up the
  Windows game only.
- The Windows game's recordings of the run-time shaders also finish the iPhone app of the same build folder
  (`nxs2.py refresh` picks them up).
- `nxs2.py --version`; the window's title shows the version.

## Known issues

- The Windows game was developed and tested on one PC (Windows 11, NVIDIA). AMD and Intel GPUs are untested; please
  report how it runs.
- Physical controllers on Windows were tested with SDL's virtual pads only; `-PadCheck` logs everything for a report.
- The first start on Windows draws some effects late while the run-time shaders are translated (about 150, once).
- *(co-op: to fill)*

## Upgrading

- **Mac / iPhone:** unzip the new download, run Setup once in it, build again with your dump: only what changed is
  redone. Your saves are untouched.
- **Windows (iPhone builder from 1.0.x):** the same. The Windows game is new: tick "Windows: play on this PC".

## Release day (remove this section from the published notes)

1. Co-op has landed in `main` and the release candidate passed the user's end-to-end test (Mac app, iPhone app from a Mac
   and from Windows, Windows game, Link Play between them).
2. `windows/` refreshed: `python tools/snapshot_windows.py <windows-native checkout>` (the audit's fixes run again; it
   stops on anything that must not be published).
3. The Windows tools built from that source with the static C runtime, the tests passed; then
   `python tools/package_release.py` → `dist/1.1.0/`. Check `SHA256SUMS.txt` and `CONTENTS.txt`.
4. Commit, tag `v1.1.0` and push (the user, as xFractals), create the release with both zips (asset names exactly
   `Pokopia-Native-for-Windows.zip` and `Pokopia-Native-for-Mac.zip`, so the README's links work), paste these notes with
   the checksums.
