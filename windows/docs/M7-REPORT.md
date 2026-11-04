# Milestone 7 report: release, prepared

2026-11-04. M7's goal (DESIGN §6): the Windows target in our builder and its window, docs, a CI build of the tools, a public release. Everything is
**prepared locally**: the release itself (pokopia-native **1.1.0**, together with co-op, which lands separately) is the user's to make. Nothing was
pushed, tagged, uploaded or published; nothing is committed (the main session reviews and commits both worktrees).

## Results

**Both versions build from the dump with the new builder and play through the launcher it makes**, from the packaged "for Windows PCs" download
unzipped into a fresh folder (`m7-test`), set up there with `nxs2.py setup --no-ios` (no Apple tools), on copies of the saves, with the clock fixed
(`DM_FIXED_TIME=1793793600`, `TZ=UTC`) and the M6 drive (`m7drive.py`: the title, A, the field, then the plan's inputs and screenshots).

| | 2.0.1 | 1.0.5 |
|---|---|---|
| dump | ExeFS `update_exefs\exefs`, RomFS the folder `installer-test-win\build201` was made from (the dump folder holds only 1.0.5's update) | the dump folder itself (`build <dump folder>`: found `updates\327680`) |
| build, 4 workers | 19 min: RomFS image, shader corpus / lowering / package 2 min, analysis 3 s, **nxshader 3,813 shaders in 372 s, all pass `spirv-val`**, **nxlift 200,414 functions in 652 s** (387.5 MB) | 19 min 55 s: same stages; nxshader 3,813 in 372 s, all pass; **nxlift 197,656 functions in 647 s** |
| takeover check | 30,644 symbols, 1,279 forced, 0 differences | 30,640 symbols, 1,277 forced, 0 differences |
| **against M6's tested binaries** | `game.dll` **identical to M3's `_local\nxlift\m3\game.dll` but for the 3-byte link time stamp** (twice: before and after the path-map rebuild) | `game.dll` identical to M6's `v105\game.dll` but for the time stamp; **`program.db` byte-identical** to M4's |
| save | imported with `import-save --eden` from a stand-in Eden NAND folder (`EDEN_DIR`) holding a copy of the Level 2 backup | imported from a copy of the phone's 1.0.5 backup (a folder holding `save\`), into a save folder whose path has a space; a second import kept the first as `save.before-<time>` |
| first start (empty run-time cache) | title on screen at 40.7 s, the field at 69.4 s; 95 run-time shaders recorded, 67 translated by the end (one at a time then, 0.6–1.4 s each), 0 failed | title at 26.6 s, the field at 51.4 s; **93 recorded and translated, 2 at a time, 0 failed**: the field complete at the first look |
| second start | title at 29.1 s, the field at 53.0 s, the remaining 28 translated (2 at a time), 0 failed; the M6 route (bag, PC, Shop, the tool, menu, a walk, a dialogue) | – |
| speed in the field | 59.1–59.4 presents/s | 58.8 presents/s |
| memory | peak working set 5.2 GB, private 1.9–2.1 GB | 4.8 GB, 1.9 GB |

**Screenshots** (`<work>\m7-test\runs\`): `e2e201-walk\shots\title.png`, `g0.png`–`g6.png` (the first start: the terrain still white
while its run-time shaders were translated, as `docs/WINDOWS.md` warns); `e2e201-route\shots\title.png`, `r00.png`–`r17.png` (the second start: the
field, the bag, the PC, the Shop, the menu, a walk, a dialogue); `e2e105-walk\shots\title.png` ("Ver. 1.0.4"), `g0.png`–`g6.png`. Against M6's own
Windows run of the same route (`v201-route2`): title 46.8 dB, PC menu 39.4 / 41.8 dB, standing 27.4 dB, bag 29.7 dB, menu 27.3 dB, walking 15.8 dB
(animation, cloud shadows and the follow camera, as in M6). 2.0.1's tool widget (bottom right) is white in both M6's and M7's runs; 1.0.5 draws it.

**Tests.** 43 tests in this tree's static-CRT build, `NXW_DUMP` set: **41 pass, 2 skip** (nxlift.dump, s2tex.corpus: data in `_local`), as before;
the new `scripts.play_defaults` cases (play.json, a switch over the file) pass. The published snapshot built as CI would (nested in the installer,
toolchains named): 392 steps, **40 pass, 3 skip**. The packager twice in a row: the same SHA-256 for both zips. The Mac download on the rented Mac
(`~/m7`, its own folder): unzipped, `nxs2.py --version`, `doctor` (all ok), `setup` (nxnative c1df4dd + the patch, ampere-lower built). The window's
argument assembly on Windows (a harness with `start` replaced): `doctor`/`setup --no-ios`, `build <dump> --windows`, with the IPA ticked
`--windows --ipa`, Play finds the launcher. **Not run**: a CI run, the iPhone build on Windows after these changes (no iOS SDK on this PC), a full Mac
app build from the Mac download.

## What was built

### 1. The builder's Windows target (pokopia-native, `nxs2.py` and the window)

`nxs2.py build <dump> --windows` (the window: **Windows: play on this PC**) turns the player's own dump into a game that plays on their PC, for both
versions (the build line in `profile/.../game.tsv` picks 1.0.5 or 2.0.1 by build ID, as for the other outputs). It reuses the installer's own stages
(extract, shader corpus, lowering, package) and adds five, all incremental and stamped with the content of the tools they ran:

| Stage | What | Output in the build folder |
|---|---|---|
| analysis | `nxanalyze --hle <the runtime's registry>`, then `--check-takeovers` against the same registry | `windows/analyze/program.db` |
| shaders | `nxshader build` (the translator through .NET, the Vulkan edits, reflection, `spirv-val`) | `windows/shaders/` (the cache nxrt reads) |
| fonts | the shared-font stand-ins from the PC's own Arial (nxnative's `make_ext_font.py` for the button icons) | `windows/fonts/` |
| recompile | `nxlift --dll` at idle priority, at most 4 workers, objects cached by their IR; lld-link against `nxrt.lib` | `windows/game.dll`, `windows/objcache/` |
| runtime | `nxrt.exe`, `SDL3.dll`, `play.ps1`, nxshader with SPIRV-Tools, and the on-demand path's own copy of the lowering steps, ampere-lower and the translator | `windows/runtime/`, `windows/play.json`, `out/Play Pokopia <version>.cmd` |

- **The launcher** is `out/Play Pokopia <version>.cmd`: `play.ps1 -Config windows/play.json`, every path relative to the build folder (it can move), the
  save and work folders `%APPDATA%\Pokopia Native\<version>\save` and `%LOCALAPPDATA%\Pokopia Native\<version>` by default; a save or work folder the
  player sets in `play.json` survives rebuilds; any `play.ps1` switch can follow (`-Fullscreen`, `-PadCheck`, `-CoopHost`, a second player's `-Save`).
- **Run-time shaders** are not in the game's files: nxrt records each the first time the game makes it and runs the build's own copy of the on-demand
  path (`nxshader ondemand`: the installer's three lowering steps, ampere-lower, the translator), so the Windows game needs no refresh step. The same
  recordings feed the iPhone build of the same folder (`nxs2.py refresh` looks in `windows/shaders/dumps` too).
- **Saves**: `nxs2.py import-save <build> <folder>` or `--eden` (Eden's own save of the game, found under its NAND, read only; `EDEN_DIR` names another
  data folder), and **Import save…** in the window. The source is only read; what the save folder held, with the commit protocol's leftovers
  (`.work`, `.new`, `.old`, `.commit`), is moved aside into `save.before-<time>`; it refuses while nxrt runs.
- **Setup without Apple's tools**: `setup --no-ios` on Windows fetches nxnative and the translator only; `doctor` checks per output (the Windows game:
  AVX2, `vulkan-1.dll`, the kit; the iPhone: Metal Developer Tools, the iOS SDK, LLVM, SPIRV-Cross).
- **The tools' location**: the download's kit `windows\bin` (or `NXW_TOOLS`), else a source checkout's `windows\build\Release` with lld-link and
  SPIRV-Tools from LLVM for Windows and the Vulkan SDK.
- The window: the **Windows: play on this PC** box (default on Windows), **Play** and **Import save…** buttons, Setup / Check tools without the iPhone
  toolchain when the IPA is unticked, the version in the title. `nxs2.py --version`, `TOOL_VERSION = '1.1.0'`.
- `run` on Windows starts the launcher; `status` lists the Windows outputs and the save folder.

### 2. The tools (this repository)

- **Static C runtime** everywhere (`CMAKE_MSVC_RUNTIME_LIBRARY` defaults to `MultiThreaded`): `nxrt.exe`, `nxlift.exe`, `nxanalyze.exe` import only
  system DLLs (checked with `llvm-objdump`, and by the packager on every package). The game DLL imports only from `nxrt.exe`, as before.
- **`nxanalyze --hle <hle_inventory.tsv>`**: the forced take-overs, the libc passthroughs and the NVN names from the runtime's own registry instead of
  nxnative's sources. On the player's PC program.db then binds exactly what the shipped nxrt takes over, whatever nxnative patch the installer carries
  (co-op's new patch included). **Checked**: 2.0.1's program.db made this way equals M3's byte for byte except 59 one-bit flags in the spin-loop
  records (`spin_in_nxnative`, set only by the Mac rewriter comparison, read by nothing in nxlift); the takeover check reports 0 differences.
- **`play.ps1 -Config <play.json>`** (installer builds) with `-Shaders`, `-ShaderBuild`, `-ShaderTool`, `-Python`, `-Installer`, `-SpirvTools`,
  `-Fonts`, `-PipelineSeed`; arguments with spaces are quoted for nxrt (`%APPDATA%\Pokopia Native`); a save folder named only by the file is created.
  `scripts.play_defaults` checks the config path too.
- **nxshader in shipped layouts**: SPIRV-Tools from `NXSHADER_TOOLS` first, `dmsh_abi.h` beside the tool when the tree is not there, ampere-lower as
  the installer ships it (`ampere-lower/windows/`), the installer found as this tree's parent inside pokopia-native.
- **The nested layout** (this tree as pokopia-native's `windows/`): `NXRT_NXNATIVE` defaults to the installer's own nxnative checkout.

### 3. Packaging (`tools/package_release.py` in pokopia-native)

`dist/<version>/Pokopia-Native-for-Windows.zip` and `Pokopia-Native-for-Mac.zip`, `SHA256SUMS.txt`, `CONTENTS.txt`. The asset names carry no
version, so the README's `releases/latest/download/<name>` links keep working; the version is in the top folder and the zip comment. Reproducible
(sorted entries, every entry dated by the last commit, fixed attributes): two runs give the same SHA-256. The packager refuses paths that look
game-derived, files larger than the biggest tool, and our tools linked against the DLL C runtime; it scans every packed file, text and binary, for user
folders and a private list (`--rules`), writing `PUBLISHING-CHECK.txt` (with `--strict`, a failure).

- **for Windows PCs**: the repository's files minus the Mac-only ones (`Pokopia Native Builder.app`, `host/dmsh_xlate_bin`), plus `windows/bin`:
  `nxanalyze.exe`, `nxlift.exe` (LLVM inside), `nxrt.exe`, `nxrt.lib`, `SDL3.dll`, `lld-link.exe` (LLVM 23.1.4's), `spirv-opt.exe` / `spirv-val.exe`
  (Vulkan SDK 1.4.363) with the Visual C++ runtime DLLs they need app-local, `hle_inventory.tsv`, `play.ps1`, `nxshader/`, `THIRD-PARTY-NOTICES.txt`
  (13 components with their licence texts), `MANIFEST.txt` (every file's size, SHA-256 and origin), `README.txt`.
- **for Macs**: the repository's files minus the Windows-only ones (`windows/`, `host/windows`, `ampere-lower/windows`, the `.cmd`).
- Neither carries `.github/workflows` (the repository's, not the players').

### 4. The source in pokopia-native (`tools/snapshot_windows.py`)

`windows/` is a copy of this tree without its history: the files git tracks or would track, never `_local/` or build folders, with the audit's fixes
(`docs/M7-PUBLISHING-AUDIT.md`), checked against generic rules (user folders) and a private list kept outside the repository, and the s2tex reference
files checked against upstream nxnative by blob id. `windows/SNAPSHOT.md` records the source commit and every fix. **The snapshot builds and tests
as CI would** (nested layout, toolchains named explicitly): 392 build steps, 43 tests, 40 pass and 3 skip (no dump, no corpus, no build201 folder).

### 5. CI (`.github/workflows/windows-tools.yml` in pokopia-native; prepared, never run)

On a `windows-2025` runner: Python 3.12 and .NET 10 (first-party actions), LLVM 23.1.4's official development package and SDL3 3.4.18's VC package
(cached), the Vulkan SDK 1.4.363 (LunarG's installer, silent), `nxs2.py setup --no-ios` (nxnative for the registry check), CMake + Ninja with the
LLVM package's clang, `ctest` (the tests that need a dump or a GPU skip themselves), then `package_release.py` and the zips as a build artifact
(14 days). Read-only permissions; it publishes nothing. YAML checked with PyYAML; not run.

### 6. Changes made along the way

- **Two on-demand translations at once** (`nxs_rt.c`, `NXRT_SHADER_JOBS`, default 2, at most 4): a fresh build's first start asks for about a
  hundred run-time shaders; one at a time 2.0.1's first run had 28 still queued when it ended, two at a time 1.0.5's finished all 93 before the field.
- **Build paths out of the binaries** (`-ffile-prefix-map`, `-fdebug-compilation-dir=.`): the semantics bitcode nxlift embeds carried this PC's
  source and build folders (the packager's scan found them); `nxlift.exe`, `nxrt.exe` and `nxanalyze.exe` now carry none. The lifted code is unchanged
  (the DLL comparison above was repeated after it).
- The log's stale first line ("GPU: none … NVN is stubbed") names the backend's choice instead.
- `nxs2.py status` shows a Windows-only folder's own outputs (and how many run-time shaders it has translated), not the Apple stages.

## Not done, or not verified

- **Co-op** is not merged into either worktree (`main` moved: the installer's patch, `src/coop`, `hle_offline.c`). After the merge the registry may
  change: rebuild, regenerate (`nxrt.generated`), and the builder's takeover check guards the rest.
- **The iPhone build on Windows** after these changes: not run (no iPhone SDK here; `doctor` stops at it). The changes keep its path (rewrite, chain
  and IPA run when `--ipa` is given; `need_setup` asks for the iPhone toolchain only then), but it is unverified. Nor was `refresh` with the Windows
  game's recordings feeding an iPhone build.
- **The Mac app from the Mac download**: setup verified on the Mac, no full build (an hour of shader compiles; the Mac's build path is unchanged).
- **CI**: written and parsed, never run.
- **A clean PC**: the tools' imports were checked (system DLLs only; SPIRV-Tools with their runtime beside them), but no run on a PC without the
  Visual C++ Redistributable, Python's packages or the Vulkan SDK. The test PC has all of them; `NXSHADER_TOOLS` made nxshader use the kit's
  SPIRV-Tools, not the SDK's.
- **AMD and Intel GPUs**, physical controllers (as in M6), Windows 10.
- **The window's look**: the screen was off (a capture of the window came back black); its logic was checked with a harness.
- **SPIRV-Tools built statically**: a static build from source (`vulkan-sdk-1.4.363.0`) was refused by this session's permissions (building
  fetched external code); the kit ships the Vulkan SDK's executables with Visual Studio's app-local runtime instead (decision 3).
- **The README's game versions** say "2.0.0 (v327680)" where the rest of the project says 1.0.5 (unchanged here, the user's text).
- First-start rendering: until the run-time shaders arrive (about a minute) the terrain and some UI draw white; documented, not avoidable without
  shipping game-derived shader dumps.

## Decisions for the user

1. **Asset names**: `Pokopia-Native-for-Windows.zip` and `Pokopia-Native-for-Mac.zip` without a version, so the README's
   `releases/latest/download/<name>` links survive the next release (the version is inside: the top folder, the zip comment, `MANIFEST.txt`).
   Versioned names would need a README change at every release.
2. **The Windows translator binary names the Windows user** (`host/windows/dmsh_xlate_bin/dmsh_xlate.dll` / `.pdb`, public since 1.0.2; the audit's
   finding A). Rebuild it with neutral paths before 1.1.0 (the packager's `--strict` refuses until then), and decide about the history.
3. **Microsoft's runtime DLLs in the Windows download** (for the Vulkan SDK's `spirv-opt` / `spirv-val`; redistributable under Visual Studio's
   licence), or build SPIRV-Tools statically in CI and drop them.
4. **Publishing `windows/`**, and the audit's judgement items (`docs/M7-PUBLISHING-AUDIT.md`): the passphrase's location (left out by default),
   game offsets and strings in the research docs, the rented Mac's paths, the development log's voice, `research/01`.
5. **A pipeline seed list** per game version (`profile/<title>/pipelines.<version>.tsv`, used when present; `NXW_PIPELINE_SEED` names one for a test):
   shader hashes and fixed-function state, like `runtime_keys.tsv`; M6's 201-pipeline list for 2.0.1 would save the first start a few hundred
   milliseconds of pipeline creation. Not shipped.
6. **The save location**: `%APPDATA%\Pokopia Native\<version>\save`, one per game version (a 2.0.1 save may not load in 1.0.5).
7. **Where the version lives**: `TOOL_VERSION` in `nxs2.py` (the window reads it); nxrt still logs "nxrt v0".


## Release day

1. Co-op lands in both repositories' `main`; the main session merges `main` into `m7` (pokopia-native: `host/` patches; windows-native: `src/coop`,
   `hle_offline.c`, which may change the registry: the CI's `nxrt.generated` and the builder's takeover check catch a mismatch).
2. Build this tree's release tools (`scripts\build.cmd`: the static C runtime is the default now), ctest with `NXW_DUMP`.
3. `python tools/snapshot_windows.py <this checkout> --rules <the private list>` in pokopia-native.
4. `python tools/package_release.py --windows-src windows --rules <the private list> --strict` → `dist/1.1.0/`; the user's release-candidate test
   (Windows game from the zip, Mac app and iPhone app from both zips, Link Play between them).
5. Fill in the release notes' co-op section and checksums (`docs/RELEASE-NOTES-1.1.0.md`); commit, tag `v1.1.0`, push and publish as xFractals with
   both zips under exactly the names the README links to.

## Reproducing

```
_local\build-m7.cmd 4                                   (the tree; ctest with NXW_DUMP=<2.0.1 ExeFS>)
py -3 tools\snapshot_windows.py <this checkout> --rules <private list>          (in pokopia-native)
py -3 tools\package_release.py --windows-src <this checkout> --rules <private list>   (dist\1.1.0)
unzip Pokopia-Native-for-Windows.zip; py -3 nxs2.py setup --no-ios; py -3 nxs2.py build <dump> --out <folder> --windows -j 4
py -3 nxs2.py import-save <folder> <save folder> | --eden;  out\Play Pokopia <version>.cmd
_local\m7\e2e.ps1 -Build <folder> -Name <run> -Plan walk|route     (the scripted run: the launcher, DM_CTL, the fixed clock)
```

Sources consulted are logged in `docs/PROVENANCE.md` (2026-11-04, M7).
