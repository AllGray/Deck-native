# s2tex: the Switch 2 texture layout

`src/s2tex` converts NVIDIA block-linear textures to linear and back for the Tegra X1 (Switch) and the Switch 2. It has a portable C99
module (`s2tex.h`, `s2tex.c`), a Metal compute kernel with the same math (`s2tex.metal`), and tests. It replaces the Switch 2
texture-layout routine of our nxnative patch, which followed GPL-3.0 code, with MIT code written in a clean room.

## How it was written

The only input taken from the original routine was a one-paragraph functional specification:

> On the Switch 2 GPU, the byte offset of a texel inside a GOB follows the Tegra X1 (Maxwell) in-GOB formula with address bits 5 and 6
> exchanged. Everything else (GOB order within blocks, block height and depth, how blocks tile the surface) is as on the Tegra X1.

The specification was treated as a hypothesis and checked against 591 real textures from the game (see [The corpus](#the-corpus)). The code
was written from the public description of block linear, from upstream nxnative's unpatched sources at commit c1df4dd (MIT), and from the
data. None of these was opened: Eden, Eden-NS2, yuzu, suyu, Citron, Sudachi, QEMU or Unicorn sources; our patch file
`installer/host/nxnative-switch2.patch`; or the patched copies of `nvn_deswizzle.c`, `nvn_deswizzle_gpu.m`, `deswz.metal` and `nvn_tex.c`. The
current Mac host was used only as a black box, through its control file and its outputs. [Sources](#sources) and `PROVENANCE.md` list
everything that was consulted.

## The layout

### Inside a GOB

A GOB (group of bytes) is 512 bytes covering 64 bytes × 8 rows of a surface. With x the byte inside the GOB's row (0–63) and y the row
(0–7), the byte's offset inside the GOB is a permutation of their bits:

```
offset bit       8    7    6    5    4    3    2    1    0
Tegra X1        x5   y2   y1   x4   y0   x3   x2   x1   x0
Switch 2        x5   y2   x4   y1   y0   x3   x2   x1   x0
                          \_______/
                       bits 5 and 6 exchanged
```

The Tegra X1 line is the usual formula `(x%64/32)·256 + (y%8/2)·64 + (x%32/16)·32 + (y%2)·16 + x%16`. Both orders keep 16 bytes of a row
together (bits 0–3), so a GOB is 32 segments of 16 bytes. Their position (offset / 16) by row and by 16-byte column:

```
          Tegra X1                      Switch 2
row   x: 0-15 16-31 32-47 48-63     x: 0-15 16-31 32-47 48-63
 0        0     2    16    18           0     4    16    20
 1        1     3    17    19           1     5    17    21
 2        4     6    20    22           2     6    18    22
 3        5     7    21    23           3     7    19    23
 4        8    10    24    26           8    12    24    28
 5        9    11    25    27           9    13    25    29
 6       12    14    28    30          10    14    26    30
 7       13    15    29    31          11    15    27    31
```

On the Tegra X1 a 16-byte column runs down two rows before the next column starts (32-byte pieces of 16 bytes × 2 rows); on the Switch 2
it runs down four rows (64-byte pieces of 16 bytes × 4 rows). The two 256-byte halves (bit 8 = x5, the left and right 32 bytes) are the
same in both.

### Blocks and the surface

Unchanged from the Tegra X1, and confirmed by the corpus: a block is 1 GOB wide, 2^bh GOBs high and 2^bd GOBs deep; inside a block the GOBs
go down first, then back in depth; blocks fill the surface left to right, then top to bottom, then front to back. For byte x of block row
y of slice z, with `S = 512 · 2^(bh+bd)` the bytes of a block, `Gx` the GOB columns (`ceil(width · bytes per block / 64)`, more when rows
are padded) and `By = ceil(rows / (8 · 2^bh))` the block rows:

```
offset = (z >> bd) · By · Gx · S                     block layer
       + (y >> (3 + bh)) · Gx · S                    block row
       + (x >> 6) · S                                block column
       + ((z mod 2^bd) · 2^bh + (y >> 3) mod 2^bh) · 512    GOB inside the block
       + gob(x mod 64, y mod 8)                      byte inside the GOB (above)
```

The surface takes `Gx · By · ceil(depth / 2^bd) · S` bytes. Sizes count format blocks: texels for uncompressed formats, 4×4 blocks for BC,
the footprint for ASTC; any bytes-per-block value works, since the layout moves bytes.

### Mip levels and arrays

A texture's levels lie one after another, each in whole blocks of its own block height and depth. A level keeps the texture's block height
while its rows exceed half a block (`8 · 2^(bh-1)` rows), and halves it otherwise; the same for depth with 1-slice GOBs. An array's layers
are a layer's size apart, rounded up to level 0's block. This is the Tegra X1 rule; `s2tex_level`, `s2tex_layer_size` and
`s2tex_layer_stride` implement it. The corpus checks it only through the storage sizes (see below): its decoded data is level 0.

## The code

| File | Contents |
|---|---|
| `s2tex.h`, `s2tex.c` | `s2tex_gob_offset`, `s2tex_offset`, `s2tex_size`, `s2tex_deswizzle` / `s2tex_swizzle` (2D and 3D, any row and slice pitch, padded GOB columns, either order), `s2tex_level` / `s2tex_layer_size` / `s2tex_layer_stride` for mip chains. C99, no dependencies |
| `s2tex.metal` | Kernels `s2tex_deswizzle` and `s2tex_swizzle`: one thread per 16-byte segment, grid (segments, rows, slices), parameters in `s2tex_params` |
| `tests/t_s2tex.c` | Synthetic tests (`s2tex.synthetic`) |
| `tests/t_ref.c`, `tests/ref/` | Tegra X1 order against upstream nxnative c1df4dd (`s2tex.tx1_vs_nxnative`); `ref/` holds its `nvn_deswizzle.c/.h` and `nvn_tex.c/.h`, unchanged, with its MIT licence |
| `tests/t_corpus.c`, `tests/corpus.h` | The real-data corpus (`s2tex.corpus`) |
| `tests/metal_check.m` | Mac only: the kernels against the C code, and the corpus on the GPU (`s2tex.metal`) |
| `tools/s2drive.py`, `tools/collect.py` | Mac only: drive the game and collect the corpus (below) |

Build and test on the PC: `scripts\build.cmd`, then `ctest -R s2tex` in the build folder. The corpus test reads `_local/s2tex/corpus`
(CMake variable `S2TEX_CORPUS_DIR`) and is skipped when it is absent. The folder also builds on its own, which is how the Mac runs the Metal
check: `cmake -S src/s2tex -B build -DS2TEX_CORPUS_DIR=~/s2tex/corpus && cmake --build build && ctest --test-dir build`.

## Tests and results

| Test | What | Result |
|---|---|---|
| `s2tex.synthetic` | Both in-GOB orders against the formula above for every byte of a GOB, one to one; hand-computed 2D and 3D addresses; 1,500 random surfaces (1–16 bytes per block, block heights and depths 0–5, padded GOB columns, row and slice pitches) where the copy loops agree with `s2tex_offset` byte for byte, the mapping is one to one inside `s2tex_size`, row padding and storage outside the texels are left alone, and swizzle undoes deswizzle; Switch 2 storage equals Tegra X1 storage with every GOB's bits 5 and 6 exchanged; hand-computed mip chains; parameter checks | passes |
| `s2tex.tx1_vs_nxnative` | Tegra X1 order against upstream nxnative c1df4dd compiled from its source: 480 random textures (300 2D, 120 3D, 60 2D arrays; R8 to RGBA32F, 3/6/12-byte RGB, BC1/4/7, ASTC 5×4/10×8/12×12; automatic and packaged block sizes), every level: level offsets, per-level block height and depth, GOB columns, layer sizes and strides, single addresses, and 2,493 level/layer copies in both directions compared byte for byte (untouched storage and padding included) | identical |
| `s2tex.corpus` | The 591 real pairs, Switch 2 order | 591 of 591 bit for bit |
| `s2tex.metal` (Mac) | 400 random surfaces, both orders and directions, GPU against C byte for byte (padding included); the 591 real pairs on the GPU | identical; 591 of 591 bit for bit |

The Tegra X1 order, run on the same pairs for contrast, reproduces 46 of them. Those are exactly the 46 pairs whose bytes come out the same
in both orders (1×1 textures, which use only the first 16 bytes of a GOB, and a few of uniform content). The other 545 pairs tell the
orders apart: the Switch 2 order reproduces all 545, the Tegra X1 order none.

The specification holds as stated; nothing in it needed correcting.

## The corpus

**Collection (black box).** The Mac's current host (`~/installer-test/build201-clean`, `host/dmhost`) ran the game from a fresh copy of the
run-4 save, driven by `tools/s2drive.py` (a copy of `~/setup/menudrive.py`) through its control file (`DM_CTL`). At each stage
`tools/collect.py` listed the registered textures (`texlist 1`, `texlist 2`), saved each new sampled texture's guest storage (`memdump`,
all levels), and then the host's decoded level 0 of the same serials. `texraw <serial>` answers "no host texture" for packaged textures
(the game's pre-swizzled textures), so the decoded level 0 came from the host's `DM_NVN_TEXRAW` switch instead, set through `DM_CTL env`
and written with the next `texdump`. Its file is a text line (width, height, depth, Metal format, bytes per row) and then the level as
the GPU holds it. Both mechanisms are documented in upstream nxnative c1df4dd (`dm/host/hle_hid.c`, `dm/gfx/nvn/nvn_mtl.m`).

**Selection, before any comparison.** Every listed 2D and 3D texture was taken, except render targets (flag COMPRESSIBLE, 0x8: the host
draws them on the GPU and never decodes their storage), linear textures (flags 0x10, 0x40), formats the host converts on upload
(3-, 6- and 12-byte RGB, 24-bit depth) and textures over 16 MB. No pair was dropped after collection.

**Stages.** Two runs: the title screen; gameplay after loading; the bag (item icon); the main menu (UI icons); walking in two directions;
the Pokédex; a habitat page; walking again.

| | |
|---|---|
| Pairs | 591 (439 distinct textures: the same texture is listed again in later stages and in the second run) |
| Stages | title 16, game 247, bag 4, menu 15, walk1 10, walk2 133, Pokédex 125, habitat 2, walk3 39 |
| Bytes per block | 4: 27 (RGBA8 24, R32F 3); 8: 265 (BC1 247, BC4 18); 16: 299 (BC7 148, BC5 108, RGBA32F 36, BC3 7) |
| Level 0 block height | 2^0: 53, 2^1: 12, 2^2: 17, 2^3: 171, 2^4: 338 |
| 3D | 3 (a 32×32×32 RGBA8 volume with block depth 2^4, twice, and a 1×1×1) |
| Sizes | 1×1 to 4096×4096; 50 with rows that are not a whole GOB (64 bytes), 48 whose height is not a whole block |
| Mip chains | 500 textures have several levels; for all 591 the storage size of the whole chain from `s2tex_layer_size` equals the size the host lists |
| Origin | 588 packaged or uploaded by the GPU (pre-swizzled game data), 3 written by the CPU through the host |
| Texels compared | 211.6 MB |

**Not covered by the data**: block height 2^5 and block depths 2^1–2^3 and 2^5; 1-, 2-, 3-, 6- and 12-byte formats; non-power-of-two
sizes (the game's sampled textures are all powers of two; its non-power-of-two textures are render targets); mip levels above 0 (only
their sizes); arrays, cubes, sparse textures and ASTC (not used by the textures listed). These follow the specification ("everything else as
on the Tegra X1") and are covered by the synthetic and reference tests only.

**Where it lives.** The corpus is game data: it is kept on the Mac (`~/s2tex/corpus`) and under `_local/s2tex/corpus` on the PC
(`.gitignore` excludes `_local/`), nowhere else. Per texture `<id>.bl` (storage) and `<id>.raw` (decoded level 0), and one row in
`manifest.tsv`.

## Sources

- Upstream nxnative (MIT) at commit c1df4dd, unpatched: `dm/gfx/tex/nvn_deswizzle.c` / `.h` and `README.md` (the Tegra X1 in-GOB formula
  and block order; used as the test reference), `dm/gfx/nvn/nvn_tex.c` / `.h` (level offsets, per-level block sizes, layer alignment, the
  NVN format sizes), `dm/gfx/tex/nvn_fmt_mtl.c` (which formats the host converts), `dm/host/hle_hid.c` (the `DM_CTL` commands),
  `dm/gfx/nvn/nvn_mtl.m` (`dm_gfx_texraw`, `dm_gfx_texlist` and the `DM_NVN_TEXRAW` switch, to read the outputs).
- The public description of NVIDIA's block-linear layout (GOBs of 64 bytes × 8 rows, blocks of 2^n GOBs), as summarised in the upstream
  comment, and Mesa's rule for shrinking the block height of small mip levels (MIT, from prior knowledge, not reopened).
- Apple's Metal documentation (compute pipelines, `dispatchThreads`, `newLibraryWithSource`).
- The outputs of the current Mac host, used as a black box.
