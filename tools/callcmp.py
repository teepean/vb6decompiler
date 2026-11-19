#!/usr/bin/env python3
"""Do two builds make the same calls?  Each procedure's calls, in order: the runtime's and the API's by
name, the program's own procedures by name, methods by vtable offset.  A statement the decompiler lost, or
one it made up, shows here even in code no run ever reaches, whatever the registers do.

usage: callcmp.py A.exe A.map B.exe B.map [--show N] [--name TEXT] [--all]   (.venv/bin/python -I)

Procedures are paired by name from the maps (`LinkSwitches=/MAP` in the .vbp), those whose name occurs once
on each side.  What only the compiler decides is left out (frees, error checks, the exception frame).
Prints the procedures whose calls differ, the most different first; --show N prints N lines of each diff."""
import argparse
import bisect
import difflib
import re
from collections import Counter
import capstone
import capstone.x86 as X
import pefile

SYM = re.compile(r"\?(\w+)@(\w+)@@\w+\s+([0-9a-f]{8})\s+f")
SKIP = re.compile(r"Free|HresultCheck|GenerateBounds|ErrorOverflow|FPException|^_adj_|^_C|ExitProc|ExceptHandler|"
                  r"Chkstk|OnError|StrCopy|StrMove|VarMove|VarDup|VarCopy|VarVargNofree|ObjSet|CastObj|AryUnlock|"
                  r"AryLock|AryConstruct|AryDestruct|__vbaI[24]|__vbaR[48]|__vbaFPInt|FpI[24]|FpR[48]|FpCmp|"
                  r"VarTstNe|VarTstEq|BoolVar|__vbaUI1|__vbaCy|StrCat|VarCat|vbaNew2")


def symbols(path):
    out = {}
    with open(path, encoding="latin-1") as f:
        for ln in f:
            m = SYM.search(ln)
            if m:
                out[int(m.group(3), 16)] = "%s" % m.group(1)
    return out


def imports(pe):
    out = {}
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        for i in d.imports:
            out[i.address] = i.name.decode() if i.name else "#%d" % i.ordinal
    return out


def thunks(pe, md, imp):
    """jmp [iat] stubs: their address -> the import."""
    out = {}
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if s.Name.startswith(b".text"):
            d = s.get_data()
            for m in re.finditer(rb"\xff\x25(....)", d, re.S):
                t = int.from_bytes(m.group(1), "little")
                if t in imp:
                    out[base + s.VirtualAddress + m.start()] = imp[t]
    return out


def calls(pe, md, va, end, syms, imp, stubs):
    base = pe.OPTIONAL_HEADER.ImageBase
    data = pe.get_data(va - base, end - va)
    out = []
    regs = {}
    for ins in md.disasm(data, va):
        ops = ins.operands
        if ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM and \
                ops[1].mem.base == 0 and ops[1].mem.index == 0 and ops[1].mem.disp in imp:
            regs[ops[0].reg] = imp[ops[1].mem.disp]         # (a cached import: mov esi, [iat])
        if ins.mnemonic not in ("call", "jmp") or not ops:
            continue
        o = ops[0]
        n = None
        if o.type == X.X86_OP_IMM:
            n = syms.get(o.imm) or stubs.get(o.imm)
            if n is None and ins.mnemonic == "call":
                n = "?"
        elif o.type == X.X86_OP_MEM and o.mem.base == 0 and o.mem.index == 0:
            n = imp.get(o.mem.disp)
        elif o.type == X.X86_OP_MEM and ins.mnemonic == "call":
            n = "vt+%X" % o.mem.disp
        elif o.type == X.X86_OP_REG and ins.mnemonic == "call":
            n = regs.get(o.reg)
        if n and n != "?" and not SKIP.search(n):
            out.append(n)
    return out


def main():
    ap = argparse.ArgumentParser()
    for a in ("a", "amap", "b", "bmap"):
        ap.add_argument(a)
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--name")
    ap.add_argument("--all", action="store_true")
    o = ap.parse_args()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    sides = []
    for exe, mp in ((o.a, o.amap), (o.b, o.bmap)):
        pe = pefile.PE(exe, fast_load=True)
        sy = symbols(mp)
        imp = imports(pe)
        st = thunks(pe, md, imp)
        addrs = sorted(sy)
        text_end = max(pe.OPTIONAL_HEADER.ImageBase + s.VirtualAddress + s.Misc_VirtualSize
                       for s in pe.sections if s.Name.startswith(b".text"))
        by = Counter(sy.values())
        procs = {}
        for i, va in enumerate(addrs):
            if by[sy[va]] != 1:
                continue
            end = addrs[i + 1] if i + 1 < len(addrs) else min(va + 0x40000, text_end)
            procs[sy[va]] = calls(pe, md, va, end, sy, imp, st)
        sides.append(procs)
    a, b = sides
    both = sorted(set(a) & set(b))
    out = []
    same = 0
    for n in both:
        if a[n] == b[n]:
            same += 1
            continue
        if Counter(a[n]) == Counter(b[n]):
            d = 0.5
        else:
            d = sum(((Counter(a[n]) - Counter(b[n])) + (Counter(b[n]) - Counter(a[n]))).values())
        out.append((d, n))
    print("paired %d, same calls %d, different %d; only in A %d, only in B %d" % (
        len(both), same, len(out), len(set(a) - set(b)), len(set(b) - set(a))))
    out.sort(reverse=True)
    for d, n in out:
        if o.name and o.name.lower() not in n.lower():
            continue
        print("%6.1f  %s  (%d, %d calls)" % (d, n, len(a[n]), len(b[n])))
        if o.show:
            lines = [x for x in difflib.ndiff(a[n], b[n]) if x[0] in "+-"]
            for x in lines[:o.show]:
                print("        " + x)
    return 0


if __name__ == "__main__":
    main()
