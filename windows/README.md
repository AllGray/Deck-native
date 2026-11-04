# pokopia-native for Windows

Pokémon Pokopia running natively on Windows x86-64: the game's arm64 code statically recompiled through LLVM, on a runtime
ported from nxnative, with a Vulkan renderer. Design: [DESIGN.md](DESIGN.md). Research behind it: [research/](research/).

Published inside [pokopia-native](../README.md) as `windows/`, from release 1.1.0 (a snapshot of the private development repository, without its history; `docs/M7-REPORT.md`). Rules: [docs/PROVENANCE.md](docs/PROVENANCE.md).

## Layout

| Folder | What |
|---|---|
| `src/common/` | shared helpers (byte reading, files, errors) |
| `src/nxload/` | NSO, MOD0, relocations, symbols, npdm: the game's modules as the runtime and the recompiler see them |
| `src/a64/` | the AArch64 instruction decoder (generated from a pattern table) |
| `src/nxanalyze/` | function discovery, control flow, jump tables, landing pads, special functions → program.db |
| (each component) | builds its own command-line tools and tests in its folder |
| `scripts/` | build and check scripts |
| `research/` | milestone 1 reports and the census scripts |
| `_local/` | game data and anything derived from it: **never committed** (in `.gitignore`) |

## Build (Windows)

```
scripts\build.cmd            # Release, 4 jobs at low priority (gentle)
scripts\build.cmd -j 2       # fewer jobs
```

Needs Visual Studio 2022 Build Tools (C++), LLVM 23.1 in `C:\Program Files\LLVM`, and CMake and Ninja (MSYS2's mingw64 ones
work). Tools read the game from a dump folder you pass them; nothing from the game is stored in the repository. The development
package of LLVM 23.1.4 (`NXW_LLVM_DIR`), SDL3's VC package (`NXRT_SDL3_DIR`) and the Vulkan SDK (`NXRT_VULKAN_SDK`) are found beside
the tree in `toolchains/` by default. The tools link the C runtime statically: they run on a PC without the Visual C++ Redistributable.

## In pokopia-native

The installer's Windows target (`nxs2.py build <dump> --windows`, "Windows: play on this PC" in its window) runs these tools on the
player's PC from their own dump: nxanalyze (`--hle`, the runtime's registry), nxshader, nxlift and nxrt with `scripts/play.ps1 -Config`.
Its "for Windows PCs" download carries them prebuilt in `windows\bin`. How it fits together: [docs/M7-REPORT.md](docs/M7-REPORT.md).
