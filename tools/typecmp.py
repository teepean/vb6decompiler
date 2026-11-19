#!/usr/bin/env python3
"""Compare a program's Types in its source with the decompiled ones, field by field.

  typecmp.py SRCDIR DECDIR SrcType=DecType [SrcType=DecType ...]   the pairs
  typecmp.py SRCDIR DECDIR --list                                  both lists, with sizes

Layout by VB's rules (natural alignment up to 4 bytes).  Prints only the lines that differ: an
offset one side has a field at and the other not, or two types.  Consts in array bounds are read
from the source's modules."""
import glob
import os
import re
import sys

SIZES = {"byte": 1, "boolean": 2, "integer": 2, "long": 4, "single": 4, "double": 8, "currency": 8,
         "date": 8, "string": 4, "variant": 16, "object": 4}
ALIGN = {"byte": 1, "boolean": 2, "integer": 2, "long": 4, "single": 4, "double": 4, "currency": 4,
         "date": 4, "string": 4, "variant": 4, "object": 4}


def read_dir(d):
    types, consts = {}, {}
    files = [f for e in ("bas", "cls", "frm", "ctl") for f in glob.glob(os.path.join(d, "*." + e))]
    for f in sorted(files):
        with open(f, "rb") as fh:
            text = fh.read().decode("cp1252", "replace")
        cur = None
        for ln in text.splitlines():
            ln = ln.split("'")[0].strip()
            m = re.match(r"(?:(?:Public|Private|Global)\s+)?Const\s+(\w+)(?:\s+As\s+\w+)?\s*=\s*(.+)", ln, re.I)
            if m and cur is None:
                consts[m.group(1).lower()] = m.group(2).strip()
                continue
            m = re.match(r"(?:(?:Public|Private)\s+)?Type\s+(\w+)$", ln, re.I)
            if m:
                cur = []
                types[m.group(1)] = cur
                continue
            if re.match(r"End\s+Type", ln, re.I):
                cur = None
                continue
            if cur is not None and ln:
                cur.append(ln)
    return types, consts


def value(s, consts, depth=0):
    s = s.strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    k = s.lower()
    if k in consts and depth < 9:
        return value(consts[k], consts, depth + 1)
    m = re.fullmatch(r"(.+)\s*([-+*])\s*(.+)", s)
    if m:
        a, b = value(m.group(1), consts, depth + 1), value(m.group(3), consts, depth + 1)
        return {"+": a + b, "-": a - b, "*": a * b}[m.group(2)]
    raise ValueError(s)


class Layouts:
    def __init__(self, types, consts):
        self.types, self.consts = types, consts
        self.lower = {n.lower(): n for n in types}
        self.done = {}

    def size(self, tname):
        t = tname.strip()
        k = t.lower()
        m = re.fullmatch(r"string\s*\*\s*(.+)", k)
        if m:
            n = 2 * value(m.group(1), self.consts)
            return n, 2
        if k in SIZES:
            return SIZES[k], ALIGN[k]
        if k in self.lower:
            lay = self.layout(self.lower[k])
            return lay[1], lay[2]
        return 4, 4         # a class, an Image

    def layout(self, name):
        if name in self.done:
            return self.done[name]
        fields, off, maxal = [], 0, 1
        for ln in self.types[name]:
            m = re.match(r"(\w+)\s*(\(([^)]*)\))?\s+As\s+(?:New\s+)?(.+)", ln, re.I)
            if not m:
                continue
            fname, arr, bounds, tname = m.group(1), m.group(2), m.group(3), m.group(4).strip()
            size, al = self.size(tname)
            total = size
            dims = None
            if arr and not bounds.strip():
                total, al = 4, 4
                dims = "()"
            elif arr:
                n = 1
                ds = []
                for b in bounds.split(","):
                    if re.search(r"\bTo\b", b, re.I):
                        lo, hi = re.split(r"\bTo\b", b, flags=re.I)
                        lo, hi = value(lo, self.consts), value(hi, self.consts)
                    else:
                        lo, hi = 0, value(b, self.consts)
                    n *= hi - lo + 1
                    ds.append("%d To %d" % (lo, hi))
                total = size * n
                dims = "(%s)" % ", ".join(ds)
            off = (off + al - 1) // al * al
            fields.append((off, fname, tname, dims, size, total))
            off += total
            maxal = max(maxal, al)
        total = (off + maxal - 1) // maxal * maxal
        self.done[name] = (fields, total, maxal)
        return self.done[name]

    def flat(self, name, base=0, prefix=""):
        """[(offset, path, type text)] down to the scalars; arrays of numbers stay one line."""
        out = []
        for off, fname, tname, dims, size, total in self.layout(name)[0]:
            path = prefix + fname
            k = tname.lower()
            if k in self.lower and dims != "()":
                n = total // size if size else 1
                if dims:
                    out.append((base + off, path + dims, "[%d of %s]" % (n, self.lower[k])))
                else:
                    out += self.flat(self.lower[k], base + off, path + ".")
                continue
            out.append((base + off, path + (dims or ""), tname))
        return out


def norm(t):
    t = t.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def compare(s, d, a, b):
    fa = s.flat(a)
    fb = d.flat(b)
    print("== %s (%d bytes) against %s (%d bytes)" % (a, s.layout(a)[1], b, d.layout(b)[1]))
    ma = {}
    for o, p, t in fa:
        ma.setdefault(o, (p, t))
    mb = {}
    for o, p, t in fb:
        if not re.match(r"pad\d", p.split(".")[-1]):
            mb.setdefault(o, (p, t))
    same = diff = 0
    pads = [(o, p, t) for o, p, t in fb if re.match(r"pad\d", p.split(".")[-1])]
    for o in sorted(set(ma) | set(mb)):
        x, y = ma.get(o), mb.get(o)
        if x and y and norm(x[1]) == norm(y[1]):
            same += 1
            continue
        if x and y and "[" in x[1] and "[" in y[1] and x[1].split(" of ")[0] == y[1].split(" of ")[0]:
            same += 1
            continue
        diff += 1
        print("  %5X  %-34s | %s" % (o, ("%s %s" % x)[:34] if x else "", ("%s %s" % y) if y else ""))
    print("  same %d, differ %d, padding %d" % (same, diff, len(pads)))


def main():
    sd, dd = sys.argv[1:3]
    s = Layouts(*read_dir(sd))
    d = Layouts(*read_dir(dd))
    if sys.argv[3:] == ["--list"]:
        for lab, x in (("source", s), ("decompiled", d)):
            print(lab)
            for n in sorted(x.types, key=lambda n: -x.layout(n)[1]):
                print("  %-24s %6d" % (n, x.layout(n)[1]))
        return
    for pair in sys.argv[3:]:
        a, b = pair.split("=")
        compare(s, d, a, b)


if __name__ == "__main__":
    main()
