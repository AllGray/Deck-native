# M2: nxload

nxload loads the game's modules the way the recompiler and the runtime need them: NSO segments, MOD0, the dynamic section,
symbols, relocations, `.eh_frame`, `main.npdm`, and rtld's binding of the program. It reproduces the census (`research/04`) on
2.0.1, number for number, and gives the same structural results on 1.0.5.

## What was built

`src/nxload/`, a C++20 static library (`nxload`) with no dependencies beyond `nxw_common` and Windows `bcrypt`:

| file | does |
|---|---|
| `include/nxload/module.hpp`, `src/module.cpp` | NSO0 header, segments, MOD0, dynamic section, dynamic symbols, DT_HASH / DT_GNU_HASH lookup, RELA / JMPREL / RELR, the SVC-site scan |
| `src/lz4.cpp` | LZ4 block decompression (exact output size, every bound checked) |
| `src/sha256.cpp` | segment SHA-256 through BCrypt |
| `include/nxload/ehframe.hpp`, `src/ehframe.cpp` | `.eh_frame_hdr` and `.eh_frame`: CIEs, FDEs, augmentations, DW_EH_PE pointer encodings, LSDA pointers |
| `include/nxload/npdm.hpp`, `src/npdm.cpp` | META, ACI0, ACID, service access control, kernel capabilities |
| `include/nxload/program.hpp`, `src/program.cpp` | module bases, rtld-style symbol binding, relocated images, binding statistics |
| `tools/nxload_info.cpp`, `tools/json.hpp` | the `nxload-info` CLI and a small JSON writer |
| `tests/synthetic.cpp`, `tests/builder.hpp`, `tests/check.hpp` | 19 tests on synthetic data, with an NSO builder (LZ4 encoder, MOD0, hash tables, relocations, `.eh_frame`) |
| `tests/dump.cpp` | checks against a real ExeFS named by `NXW_DUMP` (skipped, exit 77, without it) |

The API in one breath: `load_module()` gives a `Module` (pristine image with bss, MOD0, `Dynamic`, `symbols`, `rela`/`jmprel`/`relr`);
`parse_eh_frame(module)` gives CIEs and FDEs; `parse_npdm()` gives an `Npdm`; `load_program(exefs)` or `link(modules)` gives a
`Program` with `bases`, `resolution[module][symbol]` (the binding table), relocated `memory`, `stats` and `problems`. Offsets in a
`Module` are module-relative; `Resolution::address` and the relocated images are absolute.

`nxload-info <exefs dir> [--json <file>] [--base <addr>] [--base-of <module>=<addr>] [--no-rtld] [--no-hash-check]` prints the
summary. `--json` adds, per module, the full symbol table, the binding table (every symbol not bound to its own module), the RELA and
JMPREL lists, the distinct relative-relocation targets in text, the relocated init/fini arrays, CIEs, every FDE
(`start, size, lsda, cie, offset, insns, insns_size`), SVC sites and the npdm. Column names are in the file. On 2.0.1 it is 12 MB;
the whole run takes 0.45 s in one thread. Write it under `_local/` only: it is derived from the game.

Tests: `ctest --test-dir build\Release -R nxload` (set `NXW_DUMP` to an ExeFS folder for the dump checks). Nothing was added to
`src/common`.

## Format notes worth keeping

**NSO.** The memory image is text, ro and data at their header offsets (all page-aligned, text at 0), then bss. The header's bss size
is what makes `data end + bss` page-aligned (main: 0x34cbec0 + 0x854140 = 0x3d20000), so the image size is
`align(data end + bss, 0x1000)`. MOD0's bss starts later, at the next page (0x34cc000) and ends before the image does. All four modules
have flags 0x3f: three LZ4 segments, all hashed. The 32-byte module ID holds a 20-byte GNU build ID and 12 zero bytes; nxload shows
the 20 bytes (the census shows the first 16).

**MOD0** is found through the word at text+4; its six fields are signed offsets from MOD0 itself (dynamic, bss start, bss end,
`.eh_frame_hdr` start and end, module object).

**Dynamic section.** sdk and rtld have only DT_GNU_HASH; main and subsdk0 have both tables. The symbol count agrees three ways in every
module: NSO header dynsym size / 24, DT_HASH nchain, and the end of the highest GNU hash chain. Every export is found through every
hash table the module has (checked, `hash_mismatches() == 0`). Only subsdk0 has DT_RELACOUNT and RELATIVE relocations; the others
use DT_RELR (an even entry is an address, an odd one a bitmap of the next 63 words). DT_PLTREL is always RELA, and there is no DT_REL,
TEXTREL, versioning or TLS anywhere. JUMP_SLOT is applied as S + A (the AArch64 ELF rule); every JUMP_SLOT and GLOB_DAT addend
is 0, while ABS64 addends are not (main 343, sdk 2,175, subsdk0 102 non-zero).

**Visibility.** main, subsdk0 and sdk each define `_init` and `_fini` with STV_PROTECTED (sdk also `__uflow` and `__overflow`). Protected
definitions bind to their own module but can still satisfy other modules' imports. A first version of the linker let main's `_init`
preempt subsdk0's and sdk's; the dump test now checks that no protected or hidden definition is ever preempted.

**Binding (rtld's rule).** Lookup scope is the load order main → subsdk0..9 → sdk (03 §3.2, census `LOAD_ORDER`). An undefined
symbol, *and* a module's own global/weak default-visibility definition, binds to the first module in scope that exports the name with
default or protected visibility; an undefined weak symbol nothing exports binds to 0. This is the ELF default and nxnative's
`dm/host/loader.c` rule, which runs this game on the Mac. Its practical effect beyond imports: 64 of sdk's own exports bind to an
earlier module's copy (60 to main's, 4 to subsdk0's), and 60 of those are used by sdk's own relocations. They are C++ typeinfo and
typeinfo names, a few vtables, `__cxa_pure_virtual`, `nn::movie` function-local statics and their guard variables, and the per-module
markers `end`, `nndetailRoGetRoDataStart/End` and `__nnDetailNintendoSdkRuntimeObjectFileRefer`. The runtime must bind them the same
way, or C++ type identity across modules (exception matching, `dynamic_cast`) can break.

**Layout.** main at 0x71_0000_0000 by default; every other module follows the previous one, page-aligned, in load order. rtld, when
loaded, goes last and is outside the lookup scope (nxload and the runtime do its job; it exports nothing anyway). 2.0.1: main
0x7100000000, subsdk0 0x7103d20000, sdk 0x7103e60000, rtld 0x7104e50000, end 0x7104e70000.

**`.eh_frame`.** The header uses encodings 0x1b / 0x03 / 0x3b (pcrel sdata4 pointer, udata4 count, datarel sdata4 table relative to
the header). All CIEs are version 1 with code alignment 1, data alignment −4 and return register x30, and use FDE encoding 0x1b. The
`zPLRB` CIEs (C++ frames with LSDAs) name the personality indirectly (encoding 0x9b) through a GOT slot that a GLOB_DAT relocation
binds to `__gxx_personality_v0` (main slot 0x34c3980, sdk 0xe00000), and encode LSDA pointers as 0x1c, pcrel **sdata8**. An LSDA
field whose raw value is 0 means "no LSDA" (libunwind's rule). `B` marks B-key return-address signing; it has no data.

**npdm.** In this Switch 2 npdm the ACID block carries 0x300 bytes of signature and key before its magic, not 0x200; table offsets
count from the start of the block. Kernel capability descriptors are typed by their number of low one bits (3 thread info, 4 SVC mask
of 24 SVCs × index, 13 program type, 14 kernel version, 15 handle table size, 16 misc flags, all ones = padding). This game: priorities
28–59, cores 0–5, program type 1, kernel version field 9.1, 512 handles, misc flags 0x1, FS permissions 0x4000000000000000, 46
services, none as server. The META word at 0x10 is 0x04000000, a field the Switch 1 layout calls reserved.

**SVC sites** are found by matching `svc #imm` words (`(w & 0xffe0001f) == 0xd4000001`) over text. That is exact here because text holds
no data besides two header words (census §3).

## Cross-check with the census (2.0.1)

Everything matches except the bss/other split of relative targets, which is a classification difference (explained below).
"=" means the same number as the census.

| quantity | main | sdk | subsdk0 | rtld |
|---|---|---|---|---|
| file bytes | 29,405,084 = | 7,327,611 = | 750,812 = | 10,477 = |
| text / ro / data / bss | 0x2749480 / 0xadfdf4 / 0x2a1ec0 / 0x854140 = | 0x7b7bf0 / 0x5f0864 / 0x5f870 / 0x1e7790 = | 0xb0f60 / 0x69664 / 0x15010 / 0xfff0 = | 0x2680 / 0xa84 / 0xc008 / 0xfff8 = |
| MOD0, .dynamic, bss, .eh_frame_hdr, module object | all = | all = | all = | all = |
| dynamic tags | 26 = | 24 = | 23 = | 24 = |
| INIT_ARRAY entries | 280 = | 91 = | 1 | 0 |
| dynsym entries | 1,052 = | 27,913 = | 1,682 = | 3 = |
| imports (types) | 947 (func 918, object 27, notype 2) = | 8 (notype) = | 357 (notype) = | 2 (func) = |
| exports (weak) | 104 (54) = | 27,904 (4,632) = | 1,324 (210) = | 0 = |
| export types | object 87, func 16, notype 1 = | func 23,136, object 4,766, notype 2 = | func 1,098, object 225, notype 1 = | – |
| RELA | ABS64 1,732, GLOB_DAT 28 = | ABS64 9,818, GLOB_DAT 1,449 = | RELATIVE 689, ABS64 610, GLOB_DAT 104 = | GLOB_DAT 1 = |
| JMPREL (PLT entries) | 885 = | 9,683 = | 821 = | 1 = |
| **RELR** | **285,495 =** | 15,662 = | 0 = | 39 = |
| relative targets: text / data / ro | 246,274 / 16,369 / 15,069 = | 12,696 / 1,063 / 1,824 = | 275 / 48 / 363 = | 13 / 17 / 6 = |
| relative targets: bss (census JSON bss + outside) | 7,783 (7,781 + 2) | 79 (77 + 2) | 3 (3 + 0) | 3 (1 + 2) |
| distinct text targets / at an FDE start | 92,327 / 92,241 = | 10,323 / 10,320 = | 218 / 215 = | 8 / 6 = |
| symbol relocations bound to another module or to 0 | 2,641 + 4 weak = 2,645 = census's relocs against imports | 505: the census's 10 against imports + 495 to sdk's preempted exports | 527 = | 2 = |
| imports from main / subsdk0 / sdk / unresolved | – / 20 / **925** / 2 weak = | 8 / – / – / 0 = | 40 / – / 317 / 0 = | 0 / 0 / 2 / 0 = |
| **FDEs** (header table = walk) | **147,875** = (yes) | **38,283** = (yes) | **1,909** = (yes) | **34** = (yes) |
| CIEs; FDEs by augmentation | 3; zRB 79,966, zR 64,521, zPLRB 3,388 = | 3; zRB 20,238, zR 17,471, zPLRB 574 = | 2; zRB 1,276, zR 633 = | 2; zR 19, zRB 15 = |
| FDEs with LSDA | 3,388 = | 574 = | 0 = | 0 = |
| text covered by FDEs | 98.41% = | 96.05% = | 96.93% = | 96.23% = |
| zero-size / outside text / duplicate / overlapping FDEs | 0 = | 0 = | 0 = | 0 = |
| SVC sites | 0 = | 57 = | 0 = | 7 (0x02, 0x03, 0x06, 0x26–0x29) = |

Program-wide: names exported by two modules main & sdk 62, subsdk0 & sdk 10, main & subsdk0 6 (=). main's 2 unresolved imports are
the weak `_ZTHN9grpc_core7ExecCtx9exec_ctx_E` and `_ZTHN9grpc_core26ApplicationCallbackExecCtx18callback_exec_ctx_E`, bound to 0 (=).
No other symbol is unresolved and no relocation type is unsupported.

**npdm and SVCs** (=): ACI0 and ACID agree; allowed SVCs 0x01–0x29, 0x2c, 0x2d, 0x32–0x36 and 0x80 (49); program ID
0400a3a01ee4c000; 46 services. sdk issues 57 distinct SVCs, of which **11 are not permitted**: 0x40, 0x41, 0x43, 0x44, 0x45, 0x4b,
0x50, 0x51, 0x52, 0x70, 0x72. Permitted but never issued: 0x1e, 0x20, 0x24. rtld's 7 SVCs are all permitted.

**The one mismatch, explained.** In each of main, sdk and rtld, two relative relocations point exactly at the end of the data segment
(main: two words at 0x3481c08 and 0x3481c10 both hold 0x34cbec0). The census counts bss from MOD0's bss start, which is the next page,
so those two land in its "other"; nxload counts bss from the end of data, so they are bss. subsdk0's data ends on its bss start, so it
has none. (04's rtld row folds its one bss target into "other 3".) The census's 16-byte build IDs are prefixes of nxload's 20-byte ones.

## Pokopia 1.0.5

`sdk`, `subsdk0` and `rtld` are byte-identical to 2.0.1's, so all their numbers above hold. `main.npdm` differs only in the first 0x300
bytes of the ACID block (signature and key); every decoded field, the SVC mask and the 11-SVC finding are the same. main:

| quantity | 1.0.5 main | (2.0.1 main) |
|---|---|---|
| build ID | 816c5358e8178d802fcec4a7ce5a741a002c8e9e | 76a489f3bd3642fa5e18bd83d32c98bacc5f7d0e |
| file bytes | 28,757,489 | 29,405,084 |
| text / ro / data / bss | 0x265a3e0 (38.35 MiB) / 0xac254c / 0x29da70 / 0x844590 | 0x2749480 / 0xadfdf4 / 0x2a1ec0 / 0x854140 |
| image | 0x3c00000 | 0x3d20000 |
| MOD0; .dynamic; bss; .eh_frame_hdr; module object | 0x265b078; 0x336cb50; 0x33bc000..0x3bffb00; 0x2bf7a68..0x2d1317c; 0x33bc008 | |
| dynamic tags; INIT_ARRAY | 26; 279 | 26; 280 |
| dynsym; imports (types); exports (weak) | 1,048; 943 (func 914, object 27, notype 2); 104 (54) | 1,052; 947; 104 (54) |
| RELA; JMPREL; RELR | ABS64 1,732, GLOB_DAT 28; 881; 280,778 | 1,732, 28; 885; 285,495 |
| relative targets text / data / ro / bss | 241,955 / 16,166 / 14,960 / 7,697 | |
| distinct text targets / at an FDE start | 90,798 / 90,712 | 92,327 / 92,241 |
| imports from subsdk0 / sdk / unresolved | 20 / 921 / 2 (the same two weak gRPC wrappers) | 20 / 925 / 2 |
| FDEs; CIEs; by augmentation | 145,121; 3; zRB 78,298, zR 63,435, zPLRB 3,388 | 147,875; 3; 79,966, 64,521, 3,388 |
| FDEs with LSDA; text covered | 3,388; 98.39% | 3,388; 98.41% |
| SVC sites | 0 | 0 |

Layout at the default base: main 0x7100000000, subsdk0 0x7103c00000, sdk 0x7103d40000, rtld 0x7104d30000. The 1.0.5 ExeFS was only
read.

## Known limits

- **The binding rule is inferred**, not observed on hardware: ELF semantics plus the census's load order, as nxnative applies them.
  Horizon's rtld itself was not studied. Weak and global definitions rank equally (first in scope wins). Lazy PLT binding is not modelled:
  everything is bound at load, which is what a static recompiler wants anyway.
- **Relocation types:** RELATIVE, RELR, ABS64, GLOB_DAT and JUMP_SLOT are applied. Anything else (ABS32, PREL64, COPY, TLS, IRELATIVE)
  is reported in `problems` and left unapplied; none occurs in either build. DT_REL and TEXTREL are rejected.
- **`.eh_frame`:** textrel, funcrel and aligned pointer encodings, and the old `eh` augmentation, are rejected (none occur). CFA
  instructions are located (`insns`, `insns_size`), not interpreted. LSDAs (`.gcc_except_table`) are located, not parsed: landing pads
  are nxanalyze's job.
- **Layout:** the default bases are our choice (03 §3.3), not Horizon's; they are configurable. rtld is placed last and kept out of
  the lookup scope.
- **npdm:** memory-map, I/O and interrupt descriptors are counted, not decoded; misc-flag bits and the META word at 0x10 are not named;
  signatures are not verified.
- **SVC sites** come from a word match over text, not a decoder. That is exact for these modules (no data in text) but would count a
  matching data word elsewhere.
- **Memory:** a `Program` keeps both pristine and relocated images (about 160 MB for this game). Pass `LinkOptions::relocate = false`
  when only the binding table is needed.
