"""Derived inputs for the Mac traces: the .eh_frame function ranges of main, sdk and subsdk0 as plain offset lists.
Reads the census JSON (_local/census/fdes_<module>.json, made by research/census/run_all.py) and writes
_local/mac-traces/fde_<module>.txt, one "start end" pair (hex module offsets) per FDE. Only offsets leave the PC; these
files are what hle_trace.c's DM_TRACE_COV reads on the Mac.
Usage: py -3 -I make_inputs.py [census dir] [out dir]"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
CENSUS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, '_local', 'census')
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, '_local', 'mac-traces')

os.makedirs(OUT, exist_ok=True)
for mod in ('main', 'sdk', 'subsdk0'):
    d = json.load(open(os.path.join(CENSUS, f'fdes_{mod}.json')))
    ranges = sorted(d['ranges'])
    with open(os.path.join(OUT, f'fde_{mod}.txt'), 'w', newline='\n') as f:
        f.write(f'# {mod} text_end {d["text_end"]:#x} fdes {len(ranges)}\n')
        for s, e in ranges:
            f.write(f'{s:x} {e:x}\n')
    print(f'{mod}: {len(ranges)} FDEs, text_end {d["text_end"]:#x}')
