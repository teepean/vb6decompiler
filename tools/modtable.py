#!/usr/bin/env python3
"""The variable tables of each standard module: public and static block, entry by entry.

usage: modtable.py EXE MAP [MODULE]
Prints module name, the table's base address and size, then offset, address, kind and what the entry
says.  What the runtime initialises (records, arrays, Strings) is in these tables; plain numbers are not.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompile
from vb6dec import records


def main(argv):
    m, _ = decompile.first_pass_snapshot(argv[0], argv[1])
    want = argv[2] if len(argv) > 2 else None
    im = m.im
    for o in m.prj.objects:
        if o.kind != "Module" or (want and o.name != want):
            continue
        for label, tab, base in (("public", o.public_bytes, o.module_public),
                                 ("static", o.static_bytes, o.module_static)):
            if not tab or not base or not im.valid(tab):
                print("%s %s: none (tab %r base %r)" % (o.name, label, tab, base))
                continue
            print("%s %s: table %x base %x size %x" % (o.name, label, tab, base, im.u16(tab + 2)))
            ents, _ = records.table_entries(im, tab)
            for off, kind, info in ents:
                print("   +%-5x %x kind %02x %s" % (off, base + off, kind, info))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
