# What this repository contains, and what it never contains

This project lets you run a game you own natively on your own Mac or iPhone. It ships **tools only**. It
does not contain, and must never be made to contain, any part of the game.

## Never in this repository, never in a release

- The game's executable (`main`, `sdk`, the other ExeFS modules), in original or rewritten form.
- The game's data (`RomFS`, `romfs.img`, any file extracted from it).
- The game's shaders in any form: the shader containers from the game files, runtime shader dumps, lowered
  (Maxwell) programs, translated Metal source, compiled Metal libraries, or shader packages built from them.
- Console firmware files such as the system fonts.
- Keys, tickets, certificates, saves, screenshots of other people's content, or logs that name your files.

Every one of those is produced **on your machine, from your own dump, at build time**, and stays in your
build folder (`<dump folder>_native`). The build takes only a dump you provide yourself, checks it is the version
the recipe is for, and refuses any other. Never share a build folder: it holds the game.

## What is in the repository

- The native host and the iOS app sources (the `nxnative` project, MIT, with the Switch 2 changes made for
  this port), as a fork or as a patch set.
- `ampere-lower`: a standalone build of the Switch 2 → Maxwell shader lowering from the Eden-NS2 emulator
  (GPL-3.0-or-later; its own `LICENSE` and `NOTICE.md` are in that folder).
- The build scripts and the per-game recipe: tables that describe *where* to patch and *how* to treat each
  shader (hashes, slot kinds, formats). They contain no game code.
- Documentation.

## What the tool does not do

It decrypts nothing and bypasses nothing. It takes a dump that is already plain files, made by you from your own
console with your own tools, and needs no keys, tickets or certificates. If your dump is encrypted or packaged, this
tool cannot read it and will not try.

## Licences

- `ampere-lower/`: GPL-3.0-or-later (Eden-NS2 code; its `LICENSE` and `NOTICE.md` are in that folder).
- `host/`: the changes are MIT; the nxnative and Ryujinx code they modify or build is MIT under their own notices,
  reproduced in `host/NOTICE.md`.
- Everything else here: MIT (`LICENSE`), the same licence as nxnative.

## Trademarks and affiliation

Pokémon and Pokémon Pokopia are trademarks of Nintendo, Creatures Inc. and GAME FREAK inc. / The Pokémon Company;
Nintendo Switch is a trademark of Nintendo; the game is published by Nintendo and developed by Koei Tecmo. This
project is an independent, non-commercial fan project. It is not affiliated with, endorsed by or supported by any of
them, and the names are used only to say which game it is for.

The screenshots in the README show the game only to document the result. They are not game assets for reuse.

If you fork this project, keep these rules: they are what makes it publishable.
