#!/usr/bin/env python3
"""The same calls in the same procedures?  An original EXE against its decompiled rebuild, no maps needed.

usage: execalls.py ORIGINAL.exe REBUILT.exe [--cache DIR] [--show N] [--name TEXT] [--list N]

Procedures are told by the VB structures: each object's (form, class) procedures and then all the others, in address
order, which is source order on both sides; they pair by place, or where the counts differ by what they call.
For each pair the calls, in order: the runtime's and the Declares' by name, the program's own procedures by their
place in their object, methods by vtable offset.  What only the compiler decides is left out (frees, error checks,
conversions).  A statement lost or made up shows here even in code no run reaches, whatever the registers do.

Prints the procedures found on one side only, how many pairs make the same calls, and which calls are lost and new
over the whole program, then the pairs that differ, the most different first.  --cache keeps what each EXE gave
(Model takes minutes on a big program) in DIR, by the EXE's contents.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from capstone import x86      # noqa: E402
from vb6dec.model import Model      # noqa: E402
from vb6dec import cfg      # noqa: E402

SKIP = re.compile(r"Free|HresultCheck|GenerateBounds|ErrorOverflow|FPException|^_adj_|^_C|ExitProc|ExceptHandler|"
                  r"Chkstk|OnError|StrCopy|StrMove|VarMove|VarDup|VarCopy|VarVargNofree|ObjSet|CastObj|AryUnlock|"
                  r"AryLock|AryConstruct|AryDestruct|__vbaI[24]|__vbaR[48]|__vbaFPInt|FpI[24]|FpR[48]|FpCmp|"
                  r"VarTstNe|VarTstEq|BoolVar|__vbaUI1|__vbaCy|StrCat|VarCat|vbaNew2|__vbaStrI|__vbaStrR|__vbaStrVal|"
                  r"__vbaStrBool|__vbaStrCy|__vbaStrUI1|__vbaStrErrVarCopy|__vbaStrVarVal|__vbaStrVarCopy|__vbaStrVarMove|"
                  r"__vbaStrToAnsi|__vbaStrToUnicode|__vbaStrFixstr|__vbaLsetFixstr|__vbaLateIdCall|__vbaAryVar|"
                  r"__vbaRefVarAry|__vbaVar2Vec|__vbaVarAdd|__vbaVarSub|__vbaVarMul|__vbaVarDiv|__vbaVarAnd|__vbaVarOr|"
                  r"__vbaVarNot|__vbaVarNeg|__vbaVarTst|__vbaVarCmp|__vbaVarCat|__vbaVarInt|__vbaVarIdiv|__vbaVarMod|"
                  r"__vbaVarPow|__vbaVarFix|__vbaVarAbs|__vbaVarXor|__vbaVarEqv|__vbaVarImp|__vbaVarLike|"
                  r"__vbaStrCmp|__vbaStrComp|__vbaStrLike|__vbaLenBstr|__vbaLenVar|__vbaLenBstrB|__vbaLenVarB|"
                  r"__vbaFixstrConstruct|__vbaRecUniToAnsi|__vbaRecAnsiToUni|__vbaRecDestruct|__vbaCopyBytes|__vbaRedim|"
                  r"__vbaUbound|__vbaLbound|__vbaAryMove|__vbaAryCopy|__vbaAptOffset|__vbaAryRecCopy|__vbaRecAssign|"
                  r"__vbaStrFixstr|__vbaErase|__vbaFixstrConstruct|__vbaLateMemCall$a^")


def declares(m):
    """Declare thunks by address: `push descriptor` is at +11 of each."""
    out = {}
    pe = m.im.pe
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if s.Name.startswith(b".text"):
            text, tva = s.get_data(), base + s.VirtualAddress
            break
    else:
        return out
    for lib, fn, ptr in m.prj.externals:
        if not lib:
            continue
        i = text.find(b"\x68" + struct.pack("<I", ptr))
        if i >= 11:
            out[tva + i - 11] = fn
    return out


def proc_calls(m, p, names, thunks):
    out = []
    for a in sorted(p.insns):
        ins = p.insns[a]
        if ins.mnemonic not in ("call", "jmp") or not ins.operands:
            continue
        o = ins.operands[0]
        n = None
        if ins.mnemonic == "call" or o.type == x86.X86_OP_MEM:
            n = cfg.call_name(m, p, ins)
        if n is None and o.type == x86.X86_OP_IMM:
            if o.imm in thunks:
                n = "API " + thunks[o.imm]
            elif o.imm in names and ins.mnemonic == "call":
                n = names[o.imm]
        if n is None and ins.mnemonic == "call" and o.type == x86.X86_OP_MEM and o.mem.base != 0:
            n = "vt+%X" % o.mem.disp
        if n and not SKIP.search(n):
            out.append(n)
    return out


def load(path, cache):
    key = None
    if cache:
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()[:24]
        key = os.path.join(cache, "execalls_%s.json" % h)
        if os.path.exists(key):
            return json.load(open(key))
    m = Model(path, None)
    objs = {}
    for p in sorted(m.procs.values(), key=lambda p: p.va):
        objs.setdefault(p.owner.name if p.owner else "?", []).append(p)
    names = {}
    for on, ps in objs.items():
        for i, p in enumerate(ps):
            names[p.va] = "%s.%s" % (on, p.name) if p.name else "%s#%d" % (on, i)
    thunks = declares(m)
    out = {}
    for on, ps in objs.items():
        out[on] = [{"name": p.name, "va": p.va, "n": len(p.insns), "calls": proc_calls(m, p, names, thunks)} for p in ps]
    if key:
        os.makedirs(cache, exist_ok=True)
        json.dump(out, open(key, "w"))
    return out


def pair(xs, ys):
    """Pairs and leftovers of two objects' procedure lists, in source order."""
    if len(xs) == len(ys):
        return list(zip(xs, ys)), [], []

    def sig(p):
        # (what it calls, with the program's own procedures' addresses taken out: those move.  Not by name: Model's
        # first names of a form's public procedures can be one off, when its name list holds a Declare)
        return " ".join(strip_places(p["calls"]))[:400]
    a, b = [sig(p) for p in xs], [sig(p) for p in ys]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pairs, onlya, onlyb = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs += list(zip(xs[i1:i2], ys[j1:j2]))
        elif tag == "replace" and i2 - i1 == j2 - j1:
            pairs += list(zip(xs[i1:i2], ys[j1:j2]))
        else:
            n = min(i2 - i1, j2 - j1)
            # (the nearest in size where the counts differ)
            left = list(xs[i1:i2])
            right = list(ys[j1:j2])
            while left and right and n:
                best = min(((abs(p["n"] - q["n"]) / max(p["n"], q["n"], 1), i, j)
                            for i, p in enumerate(left) for j, q in enumerate(right)))
                pairs.append((left.pop(best[1]), right.pop(best[2])))
                n -= 1
            onlya += left
            onlyb += right
    return pairs, onlya, onlyb


def strip_places(calls):
    """The program's own unnamed procedures: their addresses say nothing, and which one is another question."""
    return ["own proc" if "#" in c or ".Proc_" in c else c for c in calls]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--cache")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--name")
    ap.add_argument("--list", type=int, default=40)
    ap.add_argument("--dump")
    o = ap.parse_args()
    A, B = load(o.a, o.cache), load(o.b, o.cache)
    print("procedures: %d, %d" % (sum(map(len, A.values())), sum(map(len, B.values()))))
    bylower = {k.lower(): k for k in B}
    same = samebag = 0
    diff = []
    lost, new = Counter(), Counter()
    for on in A:
        if on.lower() not in bylower:
            print("object only in A:", on, len(A[on]))
            continue
        pairs, onlya, onlyb = pair(A[on], B[bylower[on.lower()]])
        for p in onlya:
            print("only in A: %s %s %08X, %d instructions; calls: %s" % (on, p["name"] or "?", p["va"], p["n"],
                                                                        " ".join(p["calls"][:8])))
        for p in onlyb:
            print("only in B: %s %s %08X, %d instructions; calls: %s" % (on, p["name"] or "?", p["va"], p["n"],
                                                                        " ".join(p["calls"][:8])))
        for p, q in pairs:
            x, y = strip_places(p["calls"]), strip_places(q["calls"])
            if x == y:
                same += 1
                continue
            cx, cy = Counter(x), Counter(y)
            if cx == cy:
                samebag += 1
                d = 0
            else:
                d = sum(((cx - cy) + (cy - cx)).values())
            lost.update(cx - cy)
            new.update(cy - cx)
            diff.append((d, on, p, q, x, y))
    print("pairs: %d make the same calls in the same order, %d the same calls in another order, %d differ" % (
        same, samebag, sum(1 for t in diff if t[0])))
    print("lost over the program:", ", ".join("%s %d" % kv for kv in lost.most_common(40)))
    print("new over the program: ", ", ".join("%s %d" % kv for kv in new.most_common(40)))
    diff.sort(key=lambda t: -t[0])
    shown = 0
    for d, on, p, q, x, y in diff:
        if not d or o.name and o.name.lower() not in ("%s %s" % (on, p["name"])).lower():
            continue
        if shown >= o.list:
            break
        shown += 1
        print("%5d  %s %s  %08X / %08X  (%d, %d instructions; %d, %d calls)" % (
            d, on, p["name"] or "?", p["va"], q["va"], p["n"], q["n"], len(x), len(y)))
        if o.show:
            lines = [t for t in difflib.ndiff(x, y) if t[0] in "+-"]
            for t in lines[:o.show]:
                print("        " + t)
    if o.dump:
        json.dump([[d, on, p["name"], p["va"], q["va"], x, y] for d, on, p, q, x, y in diff], open(o.dump, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
