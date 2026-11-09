# Kiln

**Put your own game files in. Get a native build out.**

Kiln turns a game you own into a program that runs natively on your own machine: no emulator in between. You give it
your own dump, it spends a long build step taking the game apart and translating it, and what comes out runs on your
hardware with your GPU's own API (Vulkan on Linux and the Steam Deck, Metal on Apple devices).

Kiln is built to work across games. Each game gets a small recipe: tables that describe where to patch and how to treat
its shaders. The engine underneath is shared.

> Status: early and moving fast. One game is playable, one is in bring-up. See [the development log](docs/index.html)
> for where things stand.

## Games

| Game | Platforms | State |
|---|---|---|
| Pokémon Pokopia | Mac, iPhone, Windows | Playable. Full guide: [docs/POKOPIA.md](docs/POKOPIA.md) |
| Mario Kart World 1.0.0 | Steam Deck / Linux (Vulkan) | In bring-up. Full 3D with correct colours; about 10–14 fps, being optimized |

More games will follow. A new game needs its recipe and whatever runtime pieces it uses that no earlier game did.

## How it works

```
your dump ──► unpack ──► translate shaders ──► recompile / host the code ──► native build
 (NUP, …)     ExeFS,      Switch 2 (Ampere)      the game's own code runs     Vulkan or Metal,
              RomFS       → Maxwell → SPIR-V     on your CPU; its NVN         your files only,
                          / Metal                calls are replayed           in your build folder
```

1. **Unpack** your dump into the game's code (ExeFS) and data (RomFS).
2. **Translate shaders.** The game's GPU programs are lowered from the Switch 2's Ampere format to Maxwell
   (`ampere-lower`), translated by a modified Ryujinx shader compiler, then patched for the host GPU and validated.
3. **Run the game's code natively.** The game's own code runs on your CPU. Its system calls and graphics (NVN) calls are
   implemented by Kiln's runtime and replayed through Vulkan or Metal.
4. **Build step, once per game.** The long part: translating every shader, preparing pipelines and textures. Done once,
   so playing does not stall on it later.

## The rule: your copy, your dump, your build

This repository contains **tools only**. It never contains any part of a game: no code, no data, no shaders in any
form, no screenshots of game content. Everything that comes from a game is produced on your machine, from your own dump,
at build time, and stays in your build folder. Never share a build folder: it holds the game. [LEGAL.md](LEGAL.md) is the
full statement.

Kiln is an independent fan project with no affiliation with Nintendo, The Pokémon Company or any game's publisher. All
game names belong to their owners.

## Development log

[docs/index.html](docs/index.html) follows the Mario Kart World bring-up from a black screen to a full 3D track:
what broke, how it was found, and the commit that fixed it. It is also published with GitHub Pages when Pages is enabled
for the `docs/` folder.

## Licences and credits

Kiln is a fork of the Pokopia native builder by [xFractals](https://github.com/xFractals), which made the first game
playable and set the "tools only" rule this project keeps. Everything here is MIT except `ampere-lower/`, which is
[Eden-NS2](https://github.com/0x2593/Eden-NS2) code and stays GPL-3.0-or-later (its own `LICENSE` and `NOTICE.md` are in
that folder). The nxnative and Ryujinx code that `host/` modifies and builds keeps their MIT notices
([host/NOTICE.md](host/NOTICE.md)).

Thanks to the people behind [nxnative](https://github.com/seedds/nxnative) and everything it is built from (Ryujinx,
SPIRV-Cross, glslang, Capstone, lz4 and more), to Eden-NS2 for the Switch 2 shader work, and to xFractals for the
builder this started from. Much of the work is done with the help of an AI assistant (Claude).
