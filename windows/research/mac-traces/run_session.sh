#!/bin/zsh
# run_session.sh <tag> [mode] [tracedrive options]: the traced session of research/05-mac-traces.md on Pokopia 2.0.1 (build201-clean, read-only).
# mode full (default): probes + one-shot coverage + br/blr targets + 1 kHz sampler; light: probes + sampler only (no coverage traps);
# bare: DM_TRACE alone (gate FPCR / counter counts), the closest to an uninstrumented run.
# Boot, title screen and save load are tracedrive.py's fixed part; then a minute and a half of menus and play (SCRIPT=b: a second, different route).
TAG=$1; MODE=${2:-full}; shift; [ $# -gt 0 ] && shift
B=~/installer-test/build201-clean
FN="=setjmp,=longjmp,=__cxa_throw,=__cxa_rethrow,=__cxa_allocate_exception,=__cxa_begin_catch,=_Unwind_RaiseException,=_Unwind_Resume,=_Unwind_ForcedUnwind,=__gxx_personality_v0,=_ZN2nn2os22GetSystemTickFrequencyEv,=nnosGetSystemTickFrequency"
# every mrs/msr of fpcr and fpsr in sdk (census, research/04 §3): UserExceptionHandler, the fenv functions, one unexported helper
FP="sdk:152b54,sdk:152ca8,sdk:15305c,sdk:7040b0,sdk:7040bc,sdk:704114,sdk:791e50,sdk:152b78,sdk:152cc8,sdk:152d5c,sdk:7040c8,sdk:70413c"
FP="$FP,sdk:152b58,sdk:152cac,sdk:153060,sdk:7040d8,sdk:7040e8,sdk:704100,sdk:704118,sdk:791e84,sdk:152b74,sdk:152cc4,sdk:152d54,sdk:7040f0,sdk:704108,sdk:704140,sdk:791e8c"
case $MODE in
  full)  OPT=(--env DM_TRACE_PROBES=$FN,$FP --env DM_TRACE_COV=$HOME/setup/traces/input --env DM_TRACE_HZ=1000) ;;
  light) OPT=(--env DM_TRACE_PROBES=$FN,$FP --env DM_TRACE_HZ=1000) ;;
  bare)  OPT=() ;;
esac
if [ "${SCRIPT:-a}" = b ]; then
  STEPS=(phase:menus Plus wait:2 Down wait:1 Down wait:1 A wait:2 B wait:1.5 B wait:1.5 Minus wait:2 Right wait:1 B wait:2 X wait:1.5 Left Left wait:1 B wait:1.5 shot:menus
         phase:play LRight~3 B LUp~4 A wait:2 A wait:2 A wait:2 B wait:1 ZR wait:1 "ctl:look 180 0 1.0" wait:1.5 LUp~3 Y wait:1.5 B wait:1 LLeft~2 LDown~3
         ZL~1.5 ZR wait:1 "ctl:look -90 -10 0.5" wait:1 LUp~3 B wait:1 LRight~2 A wait:2 B wait:1 shot:play phase:end)
else
  STEPS=(phase:menus X wait:1 Right Right Down wait:1 B wait:1.5 Plus wait:2 Right wait:1 Right wait:1 Down wait:1 B wait:1 B wait:2
         Minus wait:2 B wait:2 shot:menus
         phase:play LUp~3 LLeft~2 LRight~2 LDown~2 B LUp~3 ZR wait:1 ZR wait:1 "ctl:look 90 0 0.5" wait:1 "ctl:look -45 10 0.3" wait:1
         A wait:2 A wait:2 B wait:1 LUp~2 ZL~1 LRight~3 Y wait:1 B wait:1 LLeft~3 X wait:1 B wait:1 LDown~2 ZR wait:1 shot:play phase:end)
fi
exec python3 ~/setup/traces/tracedrive.py $B $TAG --host ~/nxnative-trace/dm/host/dmhost --env DM_IMPORTS=1 $OPT "$@" $STEPS
