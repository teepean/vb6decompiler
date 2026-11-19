#!/usr/bin/env python3
"""Do two builds of one source hold the same code?  Procedure by procedure, addresses aside.

usage: execmp.py A.exe B.exe [--show N] [--name TEXT] [--loose]

Lists the sections of both, then walks the procedures each EXE's VB structures name, in order, and
compares their instructions with everything address-like normalised: a call to a procedure becomes
its place in the order, a call to the runtime its name, an address in the image becomes "image"
plus its section.  Prints the counts and the first procedures that differ, and with --show N the
first N differing instructions of each.

Written for DarwinBots built with VB6 on Windows against the Wine build of the same source: if the
code is the same, what Wine's compiler produces can stand in for the real one."""
import argparse
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pefile
from vb6dec.model import Model

HEX = re.compile(r"0x[0-9a-f]+")


def sections(path):
    pe = pefile.PE(path, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    out = []
    for s in pe.sections:
        out.append((s.Name.rstrip(b"\0").decode(), base + s.VirtualAddress, s.Misc_VirtualSize,
                    s.SizeOfRawData))
    return out, base, pe.OPTIONAL_HEADER.SizeOfImage


def norm_proc(m, p, order, secs, base, size):
    """The procedure's instructions as text with its addresses replaced."""
    def word(h):
        v = int(h, 16)
        if v in order:
            return "P%d" % order[v]
        if p.va <= v < p.va + 0x200000 and v in p.insns:
            return "L%d" % sorted(p.insns).index(v)
        if base <= v < base + size:
            for name, a, n, raw in secs:
                if a <= v < a + max(n, raw):
                    return "img" + name
            return "img"
        return h
    out = []
    for a in sorted(p.insns):
        ins = p.insns[a]
        t = ins.mnemonic + " " + ins.op_str
        out.append(HEX.sub(lambda g: word(g.group(0)), t))
    return out


def load(path):
    secs, base, size = sections(path)
    m = Model(path, None)
    procs = sorted(m.procs.values(), key=lambda p: p.va)
    order = {p.va: i for i, p in enumerate(procs)}
    return m, procs, order, secs, base, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--list", type=int, default=25)
    ap.add_argument("--name")
    ap.add_argument("--loose", action="store_true", help="a call to any of the program's procedures is the same call")
    o = ap.parse_args()
    A = load(o.a)
    B = load(o.b)
    for x, path in ((A, o.a), (B, o.b)):
        print(os.path.basename(path), os.path.getsize(path), "bytes;", len(x[1]), "procedures")
        for s in x[3]:
            print("   %-8s va %08x virt %8x raw %8x" % s)
    same = diff = instrs = 0
    bad = []
    for pa, pb in zip(A[1], B[1]):
        if o.name and o.name.lower() not in getattr(pa, "name", "").lower():
            continue
        na = norm_proc(A[0], pa, A[2], A[3], A[4], A[5])
        nb = norm_proc(B[0], pb, B[2], B[3], B[4], B[5])
        instrs += len(na)
        if o.loose:
            na = [re.sub(r"\bP\d+", "P", x) for x in na]
            nb = [re.sub(r"\bP\d+", "P", x) for x in nb]
        if na == nb:
            same += 1
        else:
            diff += 1
            bad.append((pa, pb, na, nb))
    print("same %d, differ %d, %d instructions in A" % (same, diff, instrs))
    for pa, pb, na, nb in bad[:o.list]:
        first = next((i for i, (x, y) in enumerate(zip(na, nb)) if x != y), min(len(na), len(nb)))
        print("%08x %08x %s: %d / %d instructions, first difference at %d" % (
            pa.va, pb.va, getattr(pa, "name", "?"), len(na), len(nb), first))
        shown = 0
        for i, (x, y) in enumerate(zip(na, nb)):
            if x != y and shown < o.show:
                print("    %5d  %-44s | %s" % (i, x[:44], y[:44]))
                shown += 1


if __name__ == "__main__":
    main()
