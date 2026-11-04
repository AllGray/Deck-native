#!/bin/zsh
# build_dylib.sh <out dir> [nxlift options...]: lifts functions with nxlift --swapin for the swap-in bases and links
# <out dir>/swapin.dylib from the partitions, the table and the runtime (docs/m4-swapin.md). Mac only. Examples:
#   build_dylib.sh ~/swapin/work/boot --fn-list ~/swapin/lists/boot.list
#   build_dylib.sh ~/swapin/work/all                         (every function of main, sdk and subsdk0)
#   NXW_LINK_ONLY=1 build_dylib.sh ~/swapin/work/all         (the runtime changed: link again, no lifting)
# NXW_REPO (default ~/swapin/windows-native), NXW_EXEFS (build201-clean's ExeFS), NXW_DB (~/swapin/data/program.db)
set -e
OUT=$1; shift
R=${NXW_REPO:-$HOME/swapin/windows-native}
E=${NXW_EXEFS:-$HOME/installer-test/build201-clean/extract/ExeFS}
DB=${NXW_DB:-$HOME/swapin/data/program.db}
BASES=(--base-of main=7000000000 --base-of subsdk0=7005000000 --base-of sdk=7006000000)   # = swapin_drive.py's DM_SWAPIN_BASES
mkdir -p $OUT
if [ -z "$NXW_LINK_ONLY" ]; then
  $R/build/bin/nxlift $E --db $DB --out $OUT --swapin $BASES --jobs ${NXW_JOBS:-10} --priority normal "$@"
fi
L=${NXW_LLVM:-$HOME/toolchains/llvm-23.1.4}
$L/bin/clang -dynamiclib -o $OUT/swapin.dylib $OUT/*/p*.o $OUT/nxw_swapin_table.o \
  -Wl,-force_load,$R/build/lib/libnxw_swapin_rt.a -isysroot $(xcrun --show-sdk-path)
ls -la $OUT/swapin.dylib
