"""Census part 4: independent decoder check. Wraps a module's text in a minimal AArch64 ELF relocatable (written to the scratch dir only), disassembles
it with LLVM's llvm-objdump (one process, output streamed, nothing stored) and compares per-mnemonic counts with capstone's (insns_<module>.json).
Usage: py -3 -I xcheck_llvm.py <ExeFS dir> <scratch dir> <module>"""
import collections, json, os, struct, subprocess, sys

sys.dont_write_bytecode = True   # keep this folder free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nsofmt as F

OBJDUMP = r'C:\Program Files\LLVM\bin\llvm-objdump.exe'


def elf_with_text(code):
    shstr = b'\0.text\0.shstrtab\0'
    text_off = 0x40
    shstr_off = text_off + len(code)
    sh_off = (shstr_off + len(shstr) + 7) & ~7
    eh = b'\x7fELF' + bytes([2, 1, 1, 0]) + bytes(8)
    eh += struct.pack('<HHIQQQIHHHHHH', 1, 183, 1, 0, 0, sh_off, 0, 64, 0, 0, 64, 3, 2)
    sh = bytes(64)
    sh += struct.pack('<IIQQQQIIQQ', 1, 1, 6, 0, text_off, len(code), 0, 0, 4, 0)
    sh += struct.pack('<IIQQQQIIQQ', 7, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0)
    body = eh + code + shstr
    body += bytes(sh_off - len(body))
    return body + sh


def main():
    exefs, scratch, name = sys.argv[1:4]
    mod = F.load_nso(os.path.join(exefs, name), name)
    text_end = mod.segs['text'][1] & ~3
    path = os.path.join(scratch, f'{name}.text.o')
    with open(path, 'wb') as f:
        f.write(elf_with_text(bytes(mod.image[:text_end])))
    p = subprocess.Popen([OBJDUMP, '-d', '-z', '--no-show-raw-insn', '--no-leading-addr', path], stdout=subprocess.PIPE, text=True, encoding='latin1', bufsize=1 << 20)
    llvm = collections.Counter()
    unknown = 0
    sysregs = collections.Counter()
    for line in p.stdout:
        line = line.lstrip(' ')
        if not line.startswith('\t'):
            continue
        parts = line.strip().split('\t')
        mn = parts[0].strip()
        if mn == '<unknown>':
            unknown += 1; continue
        llvm[mn] += 1
        if mn in ('mrs', 'msr', 'svc') and len(parts) > 1:
            sysregs[(mn, parts[1].strip().split(', ')[1 if mn == 'mrs' else 0].lower() if mn != 'svc' else parts[1].strip())] += 1
    p.wait()
    os.remove(path)
    ins = json.load(open(os.path.join(scratch, f'insns_{name}.json')))
    cap = collections.Counter()
    for mn, g, s, n in ins['keys']:
        cap[mn] += n
    diff = {k: (cap.get(k, 0), llvm.get(k, 0)) for k in set(cap) | set(llvm) if cap.get(k, 0) != llvm.get(k, 0)}
    res = {'module': name, 'words': text_end // 4, 'llvm_decoded': sum(llvm.values()), 'llvm_unknown': unknown,
           'capstone_decoded': sum(cap.values()), 'capstone_undecodable': ins['undecodable_total'],
           'mnemonics_capstone': len(cap), 'mnemonics_llvm': len(llvm),
           'same_count_mnemonics': sum(1 for k in set(cap) & set(llvm) if cap[k] == llvm[k]),
           'differences': sorted(([k, a, b] for k, (a, b) in diff.items()), key=lambda x: (-abs(x[1] - x[2]), x[0])),
           'llvm_sysregs': [[a, b, n] for (a, b), n in sysregs.most_common()]}
    with open(os.path.join(scratch, f'xcheck_{name}.json'), 'w') as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ('differences', 'llvm_sysregs')}))
    print('differences (mnemonic, capstone, llvm):', res['differences'][:40])
    print('llvm sysregs/svc:', res['llvm_sysregs'][:80])


if __name__ == '__main__':
    main()
