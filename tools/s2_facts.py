#!/usr/bin/env python3
"""Add texture / image types for NVN2 compute kernels to the answer table, keyed by handle (SWITCH2 bring-up).

  s2_facts.py <shader_facts.tsv> <chain cache b-dir> <key> <slot>=<type> [<slot>=<type> ...] [--img]

The translator numbers texture slots with the fixed Switch 1 layout, which does not hold for NVN2
compute kernels (packed per-program bank, s2_compute_layout_patch.py), so their answers are keyed
by the handle word instead: "texh": {"<handle hex>": "<type>"} and "imgh": {"<handle hex>":
{"type": ..., "fmt": ...}} (Program.cs, s2_xlate_handle_facts_patch.py). The handle of a slot comes
from the kernel's reflection JSON in the cache (the binding whose reg is the slot). Types:
1d 1darray buffer 2d 2darray 3d cube cubearray 2dms 2dmsarray. An image takes type:fmt
(e.g. 2=3d:R32Float). The row's other facts are kept.
"""
import json, pathlib, re, sys


def main():
    args = sys.argv[1:]
    img = "--img" in args; args = [a for a in args if a != "--img"]
    table, cache, key = pathlib.Path(args[0]), pathlib.Path(args[1]), args[2]
    j = json.load(open(cache / f"{key}_b.json"))
    by_slot = {}
    for b in j["bindings"]:
        m = re.match(r"cp_([ti])_tcb_([0-9A-F]+)_?$", b["msl_name"])
        if m and b.get("reg") is not None: by_slot[(m.group(1), b["reg"])] = m.group(2)
    rows = [l.rstrip("\n") for l in open(table, encoding="utf-8")]
    facts = {}
    for i, l in enumerate(rows):
        if l.startswith(key + "\t"): facts = json.loads(l.split("\t", 1)[1]); idx = i; break
    else: idx = None
    for a in args[3:]:
        slot, ty = a.split("="); kind = "i" if img else "t"
        h = by_slot.get((kind, int(slot)))
        if h is None: sys.exit(f"{key}: no {'image' if img else 'texture'} binding with slot {slot} in the reflection ({sorted(by_slot)})")
        if img:
            t, f = ty.split(":"); facts.setdefault("imgh", {})[h] = {"type": t, "fmt": f}
        else: facts.setdefault("texh", {})[h] = ty
        print(f"{key}: {'image' if img else 'texture'} slot {slot} = handle {h} -> {ty}")
    row = key + "\t" + json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if idx is None: rows.append(row)
    else: rows[idx] = row
    table.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(row)


if __name__ == "__main__":
    main()
