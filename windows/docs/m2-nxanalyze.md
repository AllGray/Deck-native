# M2: nxanalyze

Status, 2026-11-03: done for M2. nxanalyze turns the modules nxload loads into the program analysis the lifter reads
(`program.db`), plus a human report. On Pokopia 2.0.1 and 1.0.5 it finds **0 unresolved direct branches** in main, sdk and
subsdk0, and on 2.0.1 it covers **100 % of the function starts the Mac executed** (both trace runs) and agrees with every
indirect-branch target the Mac recorded. It answers open questions 4 and 6 (sections 6 and 7). The whole analysis takes
2.5 s in one thread.

## 1. What was built

`src/nxanalyze/`, a C++20 static library (`nxanalyze`) on top of `nxload` and `nxw_a64`, no other dependencies:

| file | does |
|---|---|
| `include/nxanalyze/db.hpp`, `src/db.cpp` | the program.db records, writer and reader (section 2) |
| `include/nxanalyze/analyze.hpp`, `src/analyze.cpp` | `analyze(Program, Options)`, the pass order, class and site names |
| `src/insn.hpp`, `src/insn.cpp` | per instruction: control flow (`flow`), registers read and written, NZCV, memory class (`effects`) |
| `src/discover.cpp` | function discovery (3.1) |
| `src/cfg.cpp` | per function: blocks and edges, reaching definitions, jump tables, indirect-branch classes, calls, special sites, spin loops |
| `src/lsda.cpp` | `.gcc_except_table` parsing |
| `src/hle.cpp` | nxnative's HLE tables read from its sources, symbol binding, noreturn functions |
| `src/traces.cpp` | checks against the Mac traces and nxnative's rewriter tables |
| `src/report.cpp` | report.json and report.md |
| `tools/nxanalyze.cpp` | the CLI |
| `tests/synthetic.cpp` | effects, bitmask immediates, db round trip, and the whole analysis on a synthetic NSO (a switch, a spin loop, a tail call into code without an FDE) built with nxload's test builder |
| `tests/dump.cpp` | acceptance checks on the dump named by `NXW_DUMP` (exit 77 without it); with `NXW_TRACES` also the Mac-trace checks |

```
nxanalyze <exefs dir> --out <dir> [--nxnative <dir>] [--traces <run dir>]... [--rewriter <dir>] [--no-defaults]
```

It writes `program.db`, `report.json` and `report.md` to `<dir>`. Run from the repository root, it also picks up, unless
`--no-defaults`: `..\installer\nxnative` for HLE binding, every `_local\mac-traces\<run>` whose `trace\modules.tsv` build IDs match
the dump, and then `_local\mac-traces\rewriter`. Everything it writes is derived from the game, so `--out` belongs under `_local\`.
Nothing was added to nxload or a64; the tests reuse nxload's `check.hpp` and `builder.hpp`, the report nxload's `json.hpp`. rtld is
not analysed (it is not recompiled; nxload and the runtime do its work).

## 2. program.db (version 1)

Little-endian, fixed-size records, no pointers. A 64-byte header, a section table, then the sections, each 8-byte aligned:

| header field | type | |
|---|---|---|
| magic | char[8] | `NXANADB\0` |
| version, header_size | u32, u32 | 1, 64 |
| section_count, section_entry_size | u32, u32 | 14, 24 |
| file_size | u64 | must equal the file's size |
| producer | char[32] | `nxanalyze 1` |

A section entry is `{u32 id, u32 record_size, u64 offset, u64 count}`. Readers check the magic, the version, the file size and
each known section's record size, and ignore unknown section ids, so a section can be added without a version bump; changing a
record bumps the version. `read_db`/`deserialize` in `db.hpp` do all of this.

Addresses are **module offsets** (u32) with a module index (u8, into the module records); absolute = `base + offset`. Strings
are offsets into the string section (offset 0 is ""). `none32` = 0xffffffff.

| id | section | record | fields |
|---:|---|---:|---|
| 1 | strings | 1 | NUL-terminated strings |
| 2 | modules | 64 | name, text_size, u64 base (nxload's layout), image_size, first_function, function_count, first_symbol, symbol_count, flags, build_id[20], reserved |
| 3 | functions | 40 | start, size, name, flags, u16 sources, u8 module, reserved, first_block, block_count, lsda (offset or 0), first_call_site, call_site_count |
| 4 | blocks | 20 | start, size, function, first_edge, u16 edge_count, u8 term, u8 flags |
| 5 | edges | 8 | block (global index), u8 kind |
| 6 | calls | 24 | site, function, target (as branched to), callee (function index after PLT binding, or none32), symbol (symbol record of a PLT target, or none32), u8 module, kind, binding, reserved |
| 7 | indirect | 20 | site, function, aux, symbol (string), u8 module, insn, cls, flags |
| 8 | jump_tables | 28 | site (the BR), function, table, anchor, first_target, count, u8 module, kind, bound, shift |
| 9 | jt_targets | 4 | one per table entry, in index order |
| 10 | call_sites | 32 | function, start, size, landing_pad (0 = none), action (0 = none, else 1 + offset into the action table), i32 first_filter, type_name (string), u8 module |
| 11 | sites | 16 | site, function, detail, u8 module, u8 kind |
| 12 | spin_loops | 20 | head, last (highest back-edge), function, flags, u16 insns, u8 module, kind |
| 13 | symbols | 36 | name, dynsym index, address (in the provider), bl_sites, b_sites, blr_sites, data_refs, forced_in (string), u8 module, flags, binding, provider |
| 14 | plt_stubs | 20 | stub, got, symbol (record index), callee (function index or none32), u8 module, binding |

Functions are sorted by module, then start; every module's functions are disjoint and inside text. A function's blocks are
contiguous and tile it exactly; a block's edges are contiguous and point at blocks of the same function.

**Codes** (all in `db.hpp`):
- function sources (bits): fde 1, bl 2, tail 4, reloc 8, export 16, init 32, plt 64, adr 128, prologue 256, entry 512, symbol 1024.
- function flags: no_fde 1, plt 2, noreturn 4, lsda 8, jump_tables 0x10, indirect_tail 0x20, sp_switch 0x40, sp_dynamic 0x80,
  lr_write 0x100, context_branch 0x200, sysreg 0x400, svc 0x800, trap 0x1000, spin 0x2000, setjmp_caller 0x4000, unresolved 0x10000,
  exclusive 0x20000, leaf 0x40000, ret_other 0x80000.
- block terminators: fall 0, branch 1, cond 2, jump_table 3, tail 4, indirect_tail 5, return 6, trap 7, noreturn_call 8, end 9 (falls
  off the function: none in either build), eret 10. Block flags: entry 1, landing_pad 2, jt_target 4, resume (after a call to setjmp) 8,
  spin_head 0x10, unreachable 0x20 (no predecessor and not an entry, pad, table target or alignment nops).
- edge kinds: fall 0, branch 1, taken 2, not_taken 3, jump_table 4 (one edge per distinct target).
- call kinds: bl 0, tail (b) 1, cond_tail 2. Bindings: guest 0, forced 1, swhle 2, stub 3, weak_zero 4, none 6 (unresolved).
- indirect insn: br 0, blr 1, ret 2 (`ret xN`, N ≠ 30, and `ret` through a loaded x30). Classes: section 7's table, in `db.hpp` order.
  `aux` is the jump-table index for tables, the slot for a GOT/global load, the slot offset for vtable calls, the value for
  constants. Flags: 1 several reaching definitions, 2 the slot's symbol binds to host code, 4 a BR whose x30 is not the caller's
  (a branch inside the function, not a tail call).
- jump tables: kind s8 0, u8 1, s16 2, u16 3, s32 4, u32 5 (target = anchor + (entry << shift)), abs64 6 (a table of pointers);
  bound cmp 0, mask 1, load_width 2, heuristic 3. `jt_targets[first_target + i]` is index i's target: a module offset, a function
  index for a table of function pointers, or none32 for an entry the program never takes (a null pointer, or LLVM's unreachable
  label just past the function's end).
- site kinds: svc 0 (detail = number), mrs 1, msr 2 (detail = o0:op1:CRn:CRm:op2), msr_imm 3, sys 4, udf 5 (imm), brk 6, hlt 7, wfe 8,
  wfi 9, yield 10, sev 11, sevl 12, excl_load 13, excl_store 14, clrex 15, sp_switch 16 (detail = origin: 1 load, 2 argument,
  3 call, 4 other), sp_dynamic 17, lr_write 18 (x30 at a return or tail branch is not the caller's; detail = origin, site = the
  writer), ret_reg 19, context_branch 20, setjmp_call 21, longjmp_call 22, pac_data 23, unresolved_branch 24.
- spin kinds: poll 0, poll_store 1 (invariant stores), wfe 2, yield 3, register 4 (no memory read). Flags: in_nxnative 1, acquire 2.
- symbol flags: import 1, export 2, function 4, NVN name 8.

## 3. How it works

### 3.1 Function discovery

Sources, all recorded per function: FDEs; the module entry (text+0); exported and local symbols in text; DT_INIT/DT_FINI and the
init/fini/preinit arrays; every relative relocation target in text (RELR and RELATIVE) and every symbol relocation bound to text
in any module; PLT stubs (`adrp x16; ldr x17, [x16]; add x16; br x17`, bound through their JUMP_SLOT; the PLT header too); BL
targets; B and B.cond targets that leave their FDE; `adr` and `adrp+add` targets outside the referencing function. Targets inside an
FDE but not at its start are counted, not used (none in either build).

Code outside the FDEs is partitioned by those starts plus a conservative scan: the first non-zero word after `udf #0` padding, and
`pacibsp`/`paciasp`, `stp …, [sp, #-n]!` of callee-saved pairs or `sub sp, sp, #n` right after a terminator. A scan start that the
preceding code branches to is a label of that code (shrink-wrapped prologues) and is dropped. A `b` from one of these functions into
another, right after a terminator there, makes the target a start (tail calls inside libcurl). Trailing padding is trimmed. Words
4 and 8 of text (MOD0 offset and the next header word) are never code.

A function is **noreturn** when nothing in it returns, branches indirectly or leaves by a direct branch, or by name (longjmp,
abort, exit, `__cxa_throw`, `_Unwind_Resume`, …); a PLT stub takes its target's flag. A BL or BLR to a noreturn function, or as the
last instruction of a function, ends its block with no fall-through.

### 3.2 CFG and data flow

Leaders are the entry, direct branch targets in the function, the instruction after any branch, return, trap or noreturn call,
landing pads, jump-table targets and the instruction after a call to setjmp. Calls do not end blocks. Jump-table resolution runs
in up to five rounds, adding the targets found as leaders, since a nested switch's index often comes from a block only the outer
table reaches. Each module is analysed twice: the first pass only collects where every table starts, so that a table without a
bound is read no further than the next one.

Data flow is local and path-insensitive: reaching definitions found by walking the CFG backwards (calls define x0–x18 and x30;
blocks that nothing is known to reach are ignored). Constants follow `adr`, `adrp`, `add/sub #imm`, register copies and a pointer
loaded from a relocated slot of the module.

### 3.3 Jump tables

The pattern `adr xA, anchor; ldr{b,h,sw} wE, [xT, xI{, lsl}]; add xB, xA, wE, {s,u}xt{b,h} #k | xE, lsl #k; br xB`, and the
absolute form `ldr xT, [xTab, xI, lsl #3]; br xT`. The bound comes from the dominating `cmp #N` with `b.hi`/`b.hs` (or `b.ls`/`b.lo`
into the table), found through register copies, `sub/add #k` on the index, an earlier compare of the same masked value, and
single-predecessor blocks between the compare and the branch; else a mask or bit-field extract; else the index's load width.
A compare bound must hold for every entry (each target must land in the function or be the unreachable label at its end); width
and mask bounds, and tables with no bound, are read up to the next table and while targets stay in the function. When the
table base has several reaching definitions that disagree only because some cannot be followed, the one constant among them is
used (`jt_base_inferred`); its entries are still checked.

### 3.4 Indirect branches

Every BR, BLR and `ret xN` is classified by the instruction that produced its target (following copies and PAC hints):
jump tables and function tables, PLT, vtable-style `ldr fn, [ldr vptr, [obj, #o]], #slot`, a known slot (GOT or global, named
by its relocation), a pointer in a global object, memory with a base from an argument or computation, a register-indexed table,
`ldp`, a stack reload, an incoming argument, a call result, a constant, `ldapr`/`ldar`, `csel`, and a context restore (in a
function that loads SP from memory). A BR is a tail call only when x30 there is the caller's (flag 4 otherwise).

### 3.5 Landing pads

For every FDE with an LSDA: the header (LPStart, TType encoding and base, call-site encoding), every call-site record with its
landing pad, and the first action's type filter and, for a catch, the typeinfo symbol through its GOT slot. A raw type-table entry
of 0 is `catch (...)`, read before the pc-relative adjustment as libc++abi does. Landing pads become blocks with no predecessors.

### 3.6 Special sites (question 4)

SP writes: `sp ± imm`, writeback on SP and `mov sp, x29`/`sub sp, x29, #n` are frame code; `sp ± reg` and SP set from a value
derived from SP (any arithmetic on SP or x29, or a frame slot that a saved SP was stored to) are dynamic allocation; anything
else is an **SP switch**, with its origin. x30: at every return, `br x30` and tail `b`, the reaching x30 must be the caller's
(never written, or reloaded from SP/x29-based memory; PAC hints keep it); otherwise the writer is a **return-address site**. x30
used as a scratch register between a saving prologue and a restoring epilogue is ordinary register allocation and is not reported.
A BR or RET whose target is loaded from non-stack memory in a function that switches SP is a **context branch**. Also recorded:
every MRS/MSR/SYS, SVC, UDF/BRK/HLT and unallocated word inside a function, WFE/WFI/YIELD/SEV/SEVL, exclusives, PAC data
instructions, and calls to setjmp/longjmp.

### 3.7 Spin loops

nxnative's criterion (`patch_nso.py`, "Spin waits"), reimplemented: a backward direct branch at most 512 bytes back; its region
(from the head to the highest back-edge, the instructions reachable from the head that can reach a back-edge) runs only loads
(any, including acquire and exclusive), integer and FP arithmetic, direct branches, barriers and hints; no register, vector
register or flag live at the head is written in it (no counter or cursor). A store whose value or address the region changes is
cut out and the region found again; at most four invariant stores remain. When the whole loop fails, each back-edge is tried
alone. `b .` alone is not a spin. WFE and YIELD loops are their own kinds.

### 3.8 HLE binding

nxnative's sources are read as text: every `HLE_FORCE`, `HLE_FORCE_BIG` and macro defined on them (`PALMA_FAIL`, `SKF`, `SSF`,
`SSC`, `IKB_IGNORE`) outside comments and `#if 0`; `HLE(...)` and `hle_table.c`'s `L(...)` (the swhle table); and the 734 names in
`dm/gfx/nvn/names.txt`. Each import and export is then bound as nxnative's loader binds a symbol relocation: a forced name first,
then the modules in load order (nxload's binding), then an undefined weak symbol nothing defines is 0, then swhle, then a named stub.
BL/B sites through PLT stubs, BLR/BR through GOT slots and data relocations are counted per symbol.

## 4. Results

### 4.1 Functions

| | 2.0.1 main | 1.0.5 main | sdk | subsdk0 |
|---|---:|---:|---:|---:|
| functions | 149,688 | 146,930 | 47,994 | 2,732 |
| with an FDE | 147,875 | 145,121 | 38,283 | 1,909 |
| PLT stubs (+ header) | 886 | 882 | 9,684 | 822 |
| without FDE, not PLT | 927 | 927 | 27 | 1 |
| of which from: BL targets / relocations / prologue scan / tail calls / exports / entry | 780 / 84 / 56 / 17 / 0 / 1 | the same | 3 / 4 / 16 / 1 / 7 / 1 | 0 / 1 / 0 / 0 / 0 / 1 |
| noreturn | 547 | 547 | 348 | 27 |
| text covered by functions | 99.03 % | 99.02 % | 98.03 % | 98.74 % |
| code words outside every function | 0 | 0 | 0 | 0 |
| **unresolved direct branches** | **0** | **0** | **0** | **0** |
| blocks / edges | 1,638,602 / 2,364,249 | 1,596,417 / 2,299,018 | 341,184 / 447,668 | 30,810 / 43,169 |

(sdk and subsdk0 are byte-identical in both builds, and so are their results.) The rest of text is `udf #0`/nop padding and the
header words. Sources overlap: 92,332 of main's starts are relocation targets, 56,371 BL targets, 9,151 tail-call targets.
main's code without FDEs is libcurl (926 functions in its one region) plus the entry word; sdk's is its assembly helpers
(`UserExceptionHandler`, `unw_getcontext`, the fenv helpers, …). Relocation targets that are not function starts: one each in
sdk and subsdk0, both the end of the code just before the PLT header.

**Mac traces (2.0.1, research/05)**: every executed function start is in the set: main 36,439 (`pilot`) and 36,420 (`fullb`),
sdk 582, subsdk0 3, **0 missing (100 %)**. Every recorded br/blr target agrees (19,570 and 19,593 distinct site–target pairs):

| target recorded on the Mac (`fullb`) | pairs | our class of the site |
|---|---:|---|
| BLR → function start | 15,765 | (vtable, memory, argument, …) |
| BLR → host code | 1,078 | 726 incoming argument (e.g. pointer-to-member calls), 350 known slot filled at run time (most likely NVN proc pointers), 2 memory |
| BR → function start | 898 | indirect tail calls |
| BR → function start | 12 | table of function pointers |
| BR → label in its own function | 1,707 | jump table, **target in the decoded table every time** |
| BR → host code | 133 | 103 PLT stubs, 30 GOT slots |
| anything else | **0** | |

### 4.2 Jump tables

| | 2.0.1 main | 1.0.5 main | sdk | subsdk0 | census (2.0.1 main) |
|---|---:|---:|---:|---:|---:|
| relative tables (BR of anchor + entry) | 6,172 | 6,043 | 696 | 89 | 6,172 |
| bound from cmp + b.hi/b.hs | 5,999 | 5,881 | 649 | 87 | 5,492 decoded |
| bound from a mask / index width | 54 / 1 | 47 / 1 | 15 / 0 | 0 | |
| no bound (read to the next table / while in the function) | 118 | 114 | 32 | 2 | 680 not found |
| not resolved | 0 | 0 | 0 | 0 | |
| entries u8 / u16 / s32 | 5,508 / 585 / 79 | 5,397 / 567 / 79 | 562 / 122 / 12 | 78 / 10 / 1 | 5,508 / 585 / 79 |
| entries read | 67,985 | 62,460 | 13,273 | 1,762 | 56,265 |
| absolute tables of function pointers resolved | 43 | 44 | 0 | 0 | 43 of 174 |

**Resolution rate: 100 % of main's 6,172 relative tables** (97.2 % with a proven compare bound, 98.1 % with any bound), against
the census's 89 %. The entry widths match the census exactly. The 118 unbounded tables index with values the compiler proved in
range (a `csel` of constants, a lookup through another table, a call's result); the Mac saw no target outside the decoded tables.
Of the census's 174 absolute-pointer BRs, 43 are static tables of function entries (decoded; indexed tail calls); the others
read tables filled at run time or through computed bases (21 candidates, `jt_abs_failed`) and stay indirect tail calls.

### 4.3 Landing pads

| | main (both builds) | sdk |
|---|---:|---:|
| functions with an LSDA | 3,388 | 574 |
| call-site records | 15,921 | 2,866 |
| with a landing pad | 10,036 | 1,738 |
| **distinct landing pads** (extra entry blocks) | **8,934** | **1,522** |
| cleanup only | 6,182 | 1,050 |
| first clause `catch (...)` | 3,854 | 688 |
| first clause catching a type / an exception specification | 0 / 0 | 0 / 0 |

No catch clause in either module names a type. 3,537 of main's 3,854 catch-all pads go straight to `__clang_call_terminate`
(main+0x130b150: `__cxa_begin_catch`, then `std::terminate`): they are clang's `noexcept` guards. sdk's equivalent is at sdk+0x40
(298 pads). The others call `__cxa_begin_catch` and swallow or rethrow.

### 4.4 Spin loops

| | 2.0.1 main | 1.0.5 main | sdk | subsdk0 |
|---|---:|---:|---:|---:|
| poll (loads only) | 77 | 79 | 6 | 4 |
| poll with invariant stores | 14 | 14 | 2 | 0 |
| registers only | 4 | 4 | 1 | 2 |
| wfe | 0 | 0 | 1 | 0 |
| **total** | **95** | **97** | **10** | **6** |
| nxnative's rewriter (2.0.1) | 49 | – | 5 | 5 |
| found by both | 49 | – | 5 | 5 |

Every spin wait nxnative's rewriter found is found. Of main's 46 more, 18 poll with `ldapr`: nxnative's quick filter
(`SPIN_STOP`) takes the LSE encoding group `(w & 0x3f200c00) == 0x38200000`, which also matches LDAPR, so it never looks at them
(a small nxnative bug worth fixing there). The other 28 pass the same criterion; nxnative may skip some for its own reasons (code
it already relocates for other rewrites), which a static recompiler does not have.

### 4.5 HLE binding (nxnative as of today: 918 forced takeovers in 23 files, 65 swhle entries, 734 NVN names)

| 2.0.1 | main | subsdk0 | sdk |
|---|---:|---:|---:|
| imports bound to guest code / forced to host / weak 0 | 613 / 332 / 2 | 343 / 14 / 0 | 8 / 0 / 0 |
| exports forced to host | 0 | 23 | 842 |
| BL sites through PLT stubs bound to host | 1,205 | 87 | 6,965 |
| B (tail) sites through PLT stubs bound to host | 28 | 1 | 110 |
| BL sites through the 2 weak-zero stubs (gRPC thread_local wrappers, guarded by a null check) | 312 | 0 | 0 |
| data relocations bound to host | 0 | 19 | 14 |

465 distinct forced names are referenced by some relocation. No import falls through to swhle or a named stub: every import is
defined by a module or forced. The forced takeovers apply to relocations only (as in nxnative's loader): direct BLs inside sdk
to a forced function's own body, and sdk's vtables that point at its own functions through RELR, still reach guest code.
NVN entry points are not relocations: main reaches them through proc pointers it stores at run time; statically those calls are
GOT/global-slot or argument BLRs (the 1,078 host-bound BLR pairs above).

1.0.5 main: 611 imports to guest code, 330 forced, 2 weak zero; 1,204 BL and 26 B sites through forced PLT stubs.

## 5. 1.0.5 and the decoder cross-check

`a64-xcheck` on 1.0.5 main (`nso_text.py` into `_local\a64\1.0.5`): **10,053,880 words, 10,053,880 identical to LLVM 23's
disassembly, 2 undecodable in both (the header words), 0 by documented rules, 0 unexplained**; 304 of 765 encodings used, as in
2.0.1. sdk, subsdk0 and rtld are byte-identical to 2.0.1's, already checked. The decoder now matches LLVM on every word of both
builds.

nxanalyze on 1.0.5: the numbers are in section 4; nothing behaves differently. main has 2,758 fewer functions, 129 fewer
relative jump tables (6,043, all resolved) and the same special sites, landing pads and setjmp/longjmp sites. The 1.0.5 ExeFS was
only read.

## 6. Open question 4: functions that rewrite SP or the link register

**main and subsdk0: none.** Nothing in either loads SP from memory, an argument or a call, and nothing hands on an x30 that
is not the caller's. Their SP writes are frame code and 587 + 73 dynamic stack allocations (`sub sp, sp, xN`, `mov sp, xN` with xN
computed from SP, and SP restored from a frame slot after a variable-length array). x30 is used as a scratch register in many
functions, always between a prologue that saves it and an epilogue that restores it. main's non-local control flow is one
`setjmp` call (in main+0x15744e0) and one `longjmp` call (main+0x1574f00), both in the gRPC-region decoder research/05 found.

**sdk: four functions**, all assembly the recompiler must replace (D5):

| function | what it does | sites |
|---|---|---|
| `nn::os::detail::DispatchFiberContext(FiberContextForHorizon**, FiberContextForHorizon**)` (sdk+0x14f648) | fiber switch: saves x19–x30 and q8–q15 on the current stack, stores SP to `*x0`, loads SP from `*x1`, restores the other fiber's registers from its stack; bit 0 of the loaded x30 marks a first start (`autiasp; eor x30, x30, #1`) | SP from a load at 0x14f68c; x30 written at 0x14f6cc |
| `nn::os::detail::UserExceptionHandler()` (sdk+0x152a68, no FDE) | switches SP to the exception stack and back (`mov sp, x23`) around the user handler; saves and restores FPCR/FPSR | SP switches at 0x152b10, 0x152c64 |
| `longjmp` (sdk+0x7044b4) | restores x19–x30, d8–d15 and SP from the `jmp_buf`, `autibsp; br x30` | SP from a load at 0x7044d0; x30 from the buffer at 0x7044c8; context branch at 0x7044f0 |
| libunwind's `Registers_arm64::jumpto` (sdk+0x78fc14, after `__deregister_frame`) | restores x0–x30, d0–d31 and SP from the unwind context, `ret` to the loaded x30 | SP from a load at 0x78fc98; x30 at 0x78fc48; context return at 0x78fc9c |

Around them: `setjmp` (sdk+0x7044f4, stores only), its 10 callers in sdk (libjpeg and libpng error recovery, `pthread_create`'s
start at sdk+0x154ce0, the TrueType interpreter `fnt_Execute`) and 7 longjmp callers (`HandleLibjpegError`, `HandleError`,
`png_safe_error`, `pthread_exit`, three `fnt_*` paths); their return addresses are marked as resume blocks. Thread entry needs
nothing special: the kernel sets SP, and sdk's thread-start code (sdk+0x1507c0) only writes `tpidr_el0`. One unnamed
hand-written function, sdk+0x405cc0, uses x30 as a jump-table register (`adr x30, anchor; …; br x30`): it is a resolved table,
not a return.

**System registers and traps** (unchanged from the census, now per function): main reads only `tpidr_el0` (145 sites, 138
functions). sdk: `tpidrro_el0` ×430, `cntpct_el0` ×43 (28 functions, the timeout helpers), FPCR 7/5 and FPSR 8/7 (the fenv
functions, `UserExceptionHandler` and two helpers after `unw_getcontext`), `tpidr_el0` 2/3 (`ThreadManagerImplByHorizon`'s
constructor and the thread start), `nzcv` ×2 (bignum carry), `ctr_el0` ×2, `dc civac`/`ic ivau` (`FlushDataCache` and the helper
after `BroadcastToAddress`). 57 SVC stubs, one function each. Traps inside functions: `udf #0x8002` main 351 / sdk 71 / subsdk0 7,
sdk's `#0x8005` ×34, `#0x8009`, `#0x800a`, one `udf #1` per module, and sdk's MTE `stg` word (unallocated for our decoder) inside
the function at sdk+0x78d000. PAC data instructions: sdk only (41 sites, `xpaci`, `pacia`, the 1716 forms and `xpaclri`, in
`nn::ro`, diagnostics and libunwind). No BRK, HVC or ERET anywhere.

## 7. Open question 6: main's other BRs

main has 12,944 BRs. 6,172 are relative jump tables (all resolved), 43 tables of function pointers, 886 PLT (with the header).
The census's "other 5,712" (it also set aside 174 absolute-pointer dispatches) are, by the instruction that makes the target:

| class (2.0.1 main) | BRs | example |
|---|---:|---|
| vtable-style tail call: `ldr x8, [x0]; ldr x1, [x8, #slot]; br x1` | 5,412 | main+0x6e44 |
| vtable-style, object reloaded from the stack | 2 | main+0x151e11c |
| register-indexed pointer table (`ldr xT, [xB, xI, lsl #3]`) not statically decodable | 135 | main+0x52658 (table pointer from the GOT, filled at run time) |
| function pointer from a known slot (GOT or global; `adrp; ldr; br`) | 92 | main+0x480374 |
| function pointer from a global object (`adrp; ldr obj; ldr fn, [obj, #o]`) | 82 | main+0x22fca4 |
| function pointer from memory, base an argument or computed | 82 | main+0xa1b21c |
| function pointer from `ldp` | 25 | main+0xa1b1dc |
| reloaded from the stack | 7 | main+0x4514e0 |
| `csel` between two pointers | 3 | main+0x144ea5c |
| incoming argument register | 2 | main+0x1589fd0 (a one-instruction `br x1` thunk) |
| two different definitions (a table entry or `adr` of a fixed function) | 1 | main+0x251c83c |
| **total** | **5,843** | = 12,944 − 6,172 relative tables − 43 function tables − 886 PLT |

The census's 5,712 differs only in the split: it set aside all 174 register-indexed dispatches, of which 43 are static tables of
function entries and 131 (with 4 other indexed loads) are the 135 above. So **every other BR in main is an indirect tail call**:
92.7 % through vtables, the rest through function pointers held in memory (known slots, global objects, arguments, run-time
tables). No BR in main jumps into the middle of a function or is a computed goto, and none restores a context: every one is
reached with the caller's x30 (flag 4 is set on none in main; in sdk only on longjmp's `br x30`). The Mac agrees: every target
recorded for these sites was a function start or host code. For the lifter they are `musttail` calls through the indirect-branch
table (D4).

## 8. Remaining unknowns for M3

- **Unbounded tables** (118 in main, 32 in sdk): read up to the next table; correct on every target the Mac saw, but their
  `switch` needs a default that traps or goes to the interpreter. 103 tables have a bound that holds on some paths only
  (`jt_bound_partial`); their entries all land in the function.
- **Path-insensitive data flow**: classes use the first reaching definition (flag 1 marks several). Good enough to classify,
  not to prove a target; the lifter must not drop the table lookup for any indirect branch.
- **Host-reaching BLRs**: 1,078 Mac-observed pairs go to host code through runtime-filled slots and pointer-to-member
  arguments, which static analysis cannot name. The runtime's table must accept host tokens at every BLR (research/05).
  Binding NVN slots to names (from `nvnLoadCProcs`'s stores) is possible later.
- **Forced takeovers vs. direct calls**: nxnative replaces symbols at relocations only. Whether the recompiler should also
  redirect direct BLs inside sdk to a forced function's body (and sdk's own vtable entries) is a policy question for M4.
- **libcurl**: 925 functions found from BL targets, relocations, a prologue scan and tail calls; boundaries are inferred
  (the Mac never ran libcurl). Any wrong boundary would show up as an unresolved branch, and there are none.
- **The 4 sdk context functions** need HLE replacements (D5) before fibers, longjmp or exceptions can run; the static inventory
  is complete for this build.
- **Spin loops**: 46 more than nxnative rewrites in main; the lifter's yield on their back-edges is cheap, but each should be
  checked once on the Mac before relying on it for scheduling.

## 9. Re-running

```
scripts\build.cmd
build\Release\bin\nxanalyze.exe ..\installer-test-win\build201\extract\ExeFS --out _local\analyze\2.0.1
build\Release\bin\nxanalyze.exe "<1.0.5 exefs>" --out _local\analyze\1.0.5
ctest --test-dir build\Release -R nxanalyze                       (synthetic; set NXW_DUMP [and NXW_TRACES] for the dump checks)
py -3 -I src\a64\tools\nso_text.py "<1.0.5 exefs>" _local\a64\1.0.5 main
build\Release\bin\a64-xcheck.exe _local\a64\1.0.5\main.text --scratch _local\a64\1.0.5
```

program.db is about 95 MB for 2.0.1 (93 MB for 1.0.5); report.json 0.3 MB; report.md 40 KB.
