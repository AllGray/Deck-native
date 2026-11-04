# M7: publishing audit of the Windows source

2026-11-04. Before this tree is published inside pokopia-native as `windows/` (a snapshot without its private history; `tools/snapshot_windows.py`
in the installer makes it), it was searched for game-derived data, personal identifiers, absolute local paths and private notes. This page lists what
was found, what the snapshot fixes on its own, and what needs the user's judgement. Publishing is the user's decision; nothing has been published.

## Method

- **Scope:** every file git tracks or would track (`git ls-files -co --exclude-standard`): 407 files at 65a569a plus this milestone's changes. Never
  `_local/` (game-derived data, by `.gitignore`) or build folders.
- **Searches:** the user's names and addresses (Windows user name, GitHub names, e-mail, Apple team ID, device IDs, the Mac's address and host),
  absolute paths (`C:\…`, `/c/…`, `/Users/…`, the work folder's name, home-relative Mac paths), third-party user handles, hex runs of 100 digits or
  more (keys, passphrases, dumped data), game offsets (`main+0x…`, `sdk+0x…`, `ro+0x…`), game strings (KTGL error names, Nintendo host names), and file
  types and sizes (anything not text; the largest files).
- **Not opened** (the clean-room rule): `src/s2tex/tests/ref/tex/nvn_deswizzle.c` / `.h`. They and `ref/nvn/nvn_tex.c` / `.h` are checked by git blob
  id against upstream nxnative c1df4dd instead: **all four are byte-identical to upstream** (MIT, `tests/ref/LICENSE`), so they hold nothing of ours.
- The private list of names and values the snapshot must never contain is kept outside the repository (`snapshot_windows.py --rules <file>`), so
  the published script does not spell them out.

## Findings

| # | What | Where | Action |
|---|---|---|---|
| 1 | **Game-derived data: none.** All 407 files are text (sources, scripts, Markdown, tables); the largest is the HLE registry generated from nxnative's sources (460 KB). No hex run of 100+ digits, no shader code, cache, dump, save, key or memory dump. | – | none needed |
| 2 | **The Windows user name**, in absolute paths | `docs/coop-step1b.md` (an `NXW_DUMP` example), `research/02-nxnative-portability.md` (the audited tree's path) | **fixed** in the snapshot: `<work>\`, `<2.0.1 ExeFS>` |
| 3 | **The work folder's name and this PC's folders** | `docs/PROVENANCE.md`, `docs/m5-record-shaders.md`, `docs/m5-vulkan.md`, `docs/m6-phase-b.md`, `docs/m3-runtime.md` (a dump folder's name) | **fixed**: `<work>\…`, `<another ExeFS>` |
| 4 | **The LDN passphrase's location** in main (the bytes are in no file) | `research/06-multiplayer.md` §2.4 | **left out** in the snapshot ("location not published"); see decision 2 |
| 5 | README: "stays local and private" | `README.md` | **reworded** for publication |
| 6 | No GitHub account name other than the public one, no e-mail, no team ID, no device ID, no IP address or host name of the rented Mac, no third-party user handles | – | none needed |
| 7 | **Home-relative paths of the rented Mac** (`~/installer-test/…`, `~/setup/…`, `~/mp/…`, `~/nxnative…`) | 22 files: research and milestone docs, `research/mac-traces`, `research/mac-coop`, `src/swapin/tools` | kept (no user or host name in them); decision 4 |
| 8 | **Game offsets and symbols**: 264 citations of `main+0x…` / `sdk+0x…` / `ro+0x…` (function addresses, the camera slots, the per-save player ID at `S00/SYDAT.BIN +0x61`, the KTGL user-ID accessor) | `docs/m2-*`, `m3-*`, `m4-*`, `research/04`, `research/05`, `research/06`, `docs/coop-step1.md`; the ported nxnative files carry their own | kept; decision 3 |
| 9 | **Strings read from the game's binary**: KTGL online error names, two Nintendo NPLN host names, LDN parameters | `research/06-multiplayer.md`, `docs/coop-step1.md` | kept; decision 3 |
| 10 | **A development log written to the user** ("the user's Eden save", "the user's check", this PC's BIOS and microcode, "the rented Mac", the co-op test saves by their folder names) | most of `docs/` and `research/` | kept: it is the honest record of how this was made; decision 5 |
| 11 | **Notes on another project**: `research/01-their-issues.md` summarises pokopia-recomp's public issue tracker (issue numbers, quotes of up to a sentence) | `research/01` | kept; decision 6 |
| 12 | Third-party code with its licences: astc-encoder (Apache-2.0), bc7enc_rdo (MIT / Unlicense), nxnative's `nvn.h` (MIT, h1k421) and `nvn_tex.c` (MIT; its deko3d note, zlib), the s2tex reference (upstream nxnative, MIT) | `src/nxrt/vk/third_party`, `src/nxrt/gfx`, `src/s2tex/tests/ref` | none needed (`docs/PROVENANCE.md` lists them) |

### Found outside this tree (the installer repository, already public)

| # | What | Where | Action |
|---|---|---|---|
| A | **The Windows user name inside a published binary**: the PDB path the .NET compiler embedded in the Windows build of the shader translator, and the source paths in its `.pdb` | `host/windows/dmsh_xlate_bin/dmsh_xlate.dll`, `dmsh_xlate.pdb` (committed in d65c6b1, public since 1.0.2) | **not changed** (the binary's hash is part of the shader chain's identity, and the main session owns `host/`); decision 1 |
| B | The Mac build of the same translator names the rented Mac's user folder (a generic account name, no personal name) | `host/dmsh_xlate_bin/dmsh_xlate.dll`, `.pdb` | none needed |
| C | No metadata in the README's images or the demo video beyond the encoder's name | `docs/images`, `.github/media` | none needed |
| D | **This PC's source and build folders inside `nxlift.exe`** (the semantics bitcode it embeds kept its file names and compile folder), found by the packager's scan of the first kit | the Windows download's `windows/bin` | **fixed**: `-ffile-prefix-map` / `-fdebug-compilation-dir=.` in `CMakeLists.txt` and the bitcode's command; the three tools now name no folder of the build PC |

`tools/package_release.py` now scans every file it packs, text and binary (UTF-8 and UTF-16), for a user folder and the private list; it writes
what it finds to `dist/<version>/PUBLISHING-CHECK.txt` and, with `--strict` (release day), refuses to package. It currently reports finding A.

## Decisions for the user

1. **The translator binary (finding A).** Rebuild `host/windows/dmsh_xlate_bin` with neutral paths (`dotnet publish -c Release
   -p:PathMap=<checkout>=/_/ -p:DebugType=embedded` or without the `.pdb`) and commit it; Windows iPhone builders then rebuild their Metal libraries once
   (the translator is part of the chain's identity). The copies in the published history stay unless the history is rewritten.
2. **The LDN passphrase's location** (finding 4): leave it out (the snapshot's default), or publish the research as written.
3. **Game offsets and strings** (findings 8, 9): publish the research and milestone docs as they are (facts about the binary, like the camera slots
   the installer's profile already publishes), or leave `research/04`–`06` and the offset-heavy docs out of `windows/`.
4. **The rented Mac's paths** (finding 7): keep, or replace `~/…` with placeholders.
5. **The development log's voice** (finding 10): publish as is, or trim the personal parts (the PC's BIOS, the user's saves).
6. **research/01** (finding 11): keep (it explains the design's choices), or leave it out.
7. Whether to publish `windows/` at all in 1.1.0: the Windows download works without it (its tools are prebuilt); the source is what makes the
   binaries checkable.
