#!/usr/bin/env python3
"""Do two builds compute with the FPU in the same way?  Each procedure's floating point instructions, in
order, by mnemonic and operand width: where they differ, the rebuilt source has another shape (a value
kept in a Double where the original went straight to fstp dword, a statement split or joined), and a
simulation can part over the last bit even though every statement means the same.

usage: fpcmp.py A.exe A.map B.exe B.map [--show N] [--name TEXT]      (.venv/bin/python -I)

Procedures are paired by name, from the maps (`LinkSwitches=/MAP` in the .vbp), and only those whose name
occurs once on each side.  Prints the procedures that differ, the most different first."""
import argparse
import bisect
import difflib
import re
import capstone
import capstone.x86 as X
import pefile

SYM = re.compile(r"\?(\w+)@(\w+)@@\w+\s+([0-9a-f]{8})\s+f")


def symbols(path):
    out = {}
    with open(path, encoding="latin-1") as f:
        for ln in f:
            m = SYM.search(ln)
            if m:
                out.setdefault(m.group(1), []).append(int(m.group(3), 16))
    return out


def fp_ops(pe, md, va, end):
    base = pe.OPTIONAL_HEADER.ImageBase
    out = []
    for i in md.disasm(pe.get_data(va - base, end - va), va):
        m = i.mnemonic
        if m.startswith("f") and m not in ("fnstsw", "fwait", "fnclex", "fninit", "fldcw", "fnstcw"):
            w = ""
            for o in i.operands:
                if o.type == X.X86_OP_MEM:
                    w = str(o.size)
            out.append((m + w, i.address))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("amap")
    ap.add_argument("b")
    ap.add_argument("bmap")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--name")
    o = ap.parse_args()
    pa, pb = pefile.PE(o.a, fast_load=True), pefile.PE(o.b, fast_load=True)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    sa, sb = symbols(o.amap), symbols(o.bmap)
    all_a = sorted(v for vs in sa.values() for v in vs)
    all_b = sorted(v for vs in sb.values() for v in vs)

    def end(all_v, v):
        i = bisect.bisect_right(all_v, v)
        return all_v[i] if i < len(all_v) else v + 0x1000
    rows, same, none = [], 0, 0
    for n in sorted(set(sa) & set(sb)):
        if len(sa[n]) != 1 or len(sb[n]) != 1 or (o.name and o.name.lower() not in n.lower()):
            continue
        x = fp_ops(pa, md, sa[n][0], end(all_a, sa[n][0]))
        y = fp_ops(pb, md, sb[n][0], end(all_b, sb[n][0]))
        if not x and not y:
            none += 1
            continue
        xs, ys = [t[0] for t in x], [t[0] for t in y]
        if xs == ys:
            same += 1
            continue
        sm = difflib.SequenceMatcher(None, xs, ys, autojunk=False)
        d = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
        rows.append((d, n, x, y, sm))
    rows.sort(key=lambda r: (-r[0], r[1]))
    print("no FPU %d, the same %d, different %d" % (none, same, len(rows)))
    for d, n, x, y, sm in rows:
        print("%5d  %s  %08x %08x  (%d / %d)" % (d, n, x[0][1] if x else 0, y[0][1] if y else 0, len(x), len(y)))
        if o.show:
            k = 0
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                print("       %s  A %08x %s" % (tag, x[i1][1] if i1 < len(x) else 0, " ".join(t[0] for t in x[i1:i2])))
                print("       %s  B %08x %s" % (" " * len(tag), y[j1][1] if j1 < len(y) else 0,
                                                " ".join(t[0] for t in y[j1:j2])))
                k += 1
                if k >= o.show:
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
