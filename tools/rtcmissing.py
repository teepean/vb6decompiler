#!/usr/bin/env python3
"""MSVBVM60's exports that the decompiler has no entry for, and whether a compiled program can use them:
names only VBA's p-code engine calls (Ex*, Cdbl..., numbers) can't be told from here, so the list is for a
person to go through.

usage: rtcmissing.py MSVBVM60.DLL"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, ".venv", "lib"))


def main(argv):
    import pefile
    pe = pefile.PE(argv[0], fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    names = sorted(e.name.decode() for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name)
    text = ""
    for f in ("runtime.py", "calls.py", "lift.py", "simplify.py", "insns.py", "render.py"):
        with open(os.path.join(HERE, "vb6dec", f), encoding="latin-1") as h:
            text += h.read()
    known = set(re.findall(r"\b(?:__vba|rtc)\w+", text))
    missing = [n for n in names if re.match(r"(__vba|rtc)", n) and n not in known]
    print(len(names), "exports,", len([n for n in names if re.match(r"(__vba|rtc)", n)]), "__vba/rtc,",
          len(missing), "with no entry:")
    print(" ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
