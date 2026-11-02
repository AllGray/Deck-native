# ampere-lower

A cut-down build of the `ampere-spirv` tool from the Eden-NS2 emulator fork
(https://github.com/0x2593/Eden-NS2, GPL-3.0-or-later): the Switch 2 (Ampere) shader capture and the
Ampere-to-Maxwell lowering headers from `src/shader_recompiler/frontend/ampere/`, with `main.cpp` reduced
to the lowering stage. The headers are copied unchanged except for the four Pokopia fixes submitted
upstream (FSEL operand modifiers, HFMA2 `.H0_NH1` lane swap), so this folder is GPL-3.0-or-later like its
source; `LICENSE` is the full text. It contains no game code.

Build: `sh build.sh` (any C++20 compiler). Verified bit-identical to the full emulator build's `--maxwell`
output on the Pokopia shader corpus.
