#!/usr/bin/env python3
"""One small program compiled with each of VB6's compile options, to see what each leaves in the EXE: is
vb6dec/detect.py right, and which options leave no mark.  Shows VB6 windows while it builds.

usage: optmatrix.py WORK [NAME ...]

WORK must be a new folder.  Each variant goes in WORK/NAME; prints what detect says of each, and which code
sections are byte for byte the same as the plain build's.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import vbmake                               # noqa: E402
from vb6dec import detect                   # noqa: E402
from vb6dec.pe import Image                 # noqa: E402

BAS = r'''Attribute VB_Name = "mM"
Option Explicit
Dim a() As Long, d() As Double

Function Work(ByVal n As Long) As Double
    Dim i As Long, x As Double, y As Single, k As Integer
    ReDim a(1 To n), d(1 To n)
    For i = 1 To n
        a(i) = i * 3 + 1
        d(i) = a(i) / 7#
        x = x + d(i) * 1.5
        y = y + CSng(i) / 3
        k = (k + i) Mod 100
    Next i
    Work = x + y - k
End Function

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    Print #1, Work(100)
    Close #1
End Sub
'''
PLAIN = {"CompilationType": 0, "OptimizationType": 0, "FavorPentiumPro(tm)": 0, "CodeViewDebugInfo": 0, "NoAliasing": 0,
         "BoundsCheck": 0, "OverflowCheck": 0, "FlPointCheck": 0, "FDIVCheck": 0, "UnroundedFP": 0}
VARIANTS = {
    "plain": {},
    "pcode": {"CompilationType": -1},
    "small": {"OptimizationType": 1},
    "noopt": {"OptimizationType": 2},
    "pentium": {"FavorPentiumPro(tm)": -1},
    "debug": {"CodeViewDebugInfo": -1},
    "noalias": {"NoAliasing": -1},
    "unround": {"UnroundedFP": -1},
    "bounds": {"BoundsCheck": -1},
    "overflow": {"OverflowCheck": -1},
    "flpoint": {"FlPointCheck": -1},
    "fdiv": {"FDIVCheck": -1},
    "allchecks": {"BoundsCheck": -1, "OverflowCheck": -1, "FlPointCheck": -1, "FDIVCheck": -1},
}


def write(folder, opts):
    os.makedirs(folder)
    with open(os.path.join(folder, "mM.bas"), "w", encoding="cp1252", newline="") as f:
        f.write(BAS.replace("\n", "\r\n"))
    lines = ["Type=Exe", "Module=mM; mM.bas", 'Startup="Sub Main"', 'Name="opt"', 'ExeName32="opt.exe"']
    lines += ["%s=%d" % kv for kv in dict(PLAIN, **opts).items()]
    with open(os.path.join(folder, "opt.vbp"), "w", encoding="cp1252", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return os.path.join(folder, "opt.vbp")


def text_of(path):
    im = Image(path)
    for va, size, name in im.sections:
        if name.startswith(".text"):
            return im.bytes(va, size)
    return b""


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    work, want = argv[0], argv[1:] or list(VARIANTS)
    os.makedirs(work)
    plain = None
    for name in want:
        vbp = write(os.path.join(work, name), VARIANTS[name])
        out = vbmake.make(vbp) if hasattr(vbmake, "make") else None
        exe = os.path.join(work, name, "opt.exe")
        if not os.path.exists(exe):
            print("== %s: no build: %s" % (name, out))
            continue
        info = detect.detect(exe)
        o = info.get("options") or {}
        text = text_of(exe)
        if name == "plain":
            plain = text
        print("== %-10s %-7s optimised=%-5s direct %3d thunk %3d subesp %2d pushecx %2d  bounds %2s over %2s fp %2s fdiv %2s  debug %s  "
              "text %5d %s  build %s engine %s" % (
                  name, "p-code" if info.get("pcode") else "native", o.get("optimised"), o.get("direct", 0), o.get("thunk", 0),
                  o.get("sub_esp", 0), o.get("push_ecx", 0), o.get("BoundsCheck"), o.get("OverflowCheck"), o.get("FlPointCheck"),
                  o.get("FDIVCheck"), info.get("debug"), len(text), "same" if text == plain else "differs",
                  info.get("runtime_build"), info.get("engine")), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
