#!/usr/bin/env python3
"""Compare the designer part of decompiled forms with the real source, property by property.

usage: frmcmp.py SOURCE_DIR DECOMPILED_DIR [-v]

For every .frm/.ctl in both: controls missing on either side, then properties that differ or are
missing, counted by name.  Values the writer normalises are normalised here too (quotes, the
comments after booleans, .frx offsets).  -v prints each difference."""
import collections
import os
import re
import sys


def frx_text(path, v):
    """$"x.frx":offset: the text it stands for, or None."""
    m = re.match(r'\$"([^"]+)":([0-9A-Fa-f]+)', v)
    if not m:
        return None
    p = os.path.join(os.path.dirname(path), m.group(1))
    if not os.path.exists(p):
        return None
    d = open(p, "rb").read()
    o = int(m.group(2), 16)
    n = int.from_bytes(d[o:o + 4], "little")
    return '"' + d[o + 4:o + 4 + n].decode("latin-1").replace('"', '""') + '"'


def parse(path):
    """{control path: {property: value}} from the part of the file before the code."""
    lines = open(path, encoding="latin-1", newline="").read().replace("\r\n", "\n").split("\n")
    out, stack, sub = {}, [], None
    for ln in lines:
        s = ln.strip()
        if s.startswith("Attribute VB_Name"):
            break
        m = re.match(r"Begin\s+(\S+)\s+(\S+)", s)
        if m:
            name = m.group(2)
            key = name.lower()
            idx = len([k for k in out if k.split("/")[-1].split("#")[0] == key])
            stack.append(key + "#%d" % idx)
            out["/".join(stack)] = {"_class": m.group(1).lower()}
            continue
        if s.startswith("BeginProperty"):
            sub = s.split()[1].lower() if sub is None else sub + "." + s.split()[1].lower()
            continue
        if s.startswith("EndProperty"):
            sub = sub.rpartition(".")[0] or None
            continue
        if s == "End":
            if stack:
                stack.pop()
            continue
        m = re.match(r"([\w.()]+)\s*=\s*(.*)$", s)
        if m and stack:
            k = (sub + "." if sub else "") + m.group(1).lower()
            out["/".join(stack)][k] = frx_text(path, m.group(2).strip()) or norm(m.group(2))
    return out


def norm(v):
    v = v.strip()
    if v.startswith('"') and v.count('"') >= 2 and ":" in v.rpartition('"')[2]:
        return "<frx>"
    if v.startswith('$"'):
        return "<frx>"
    v = re.sub(r"\s*'.*$", "", v) if not v.startswith('"') else v
    if re.fullmatch(r"-?\d+\.\d*", v):
        v = "%g" % float(v)
    return v.lower() if v.startswith("&h") else v


# the writer adds these where VB would use the default; a default on one side is no difference
DEFAULTS = {"font.charset", "font.italic", "font.strikethrough", "font.underline", "font.weight",
            "font.size", "font.name", "scaleheight", "scalewidth", "_extentx", "_extenty", "_version",
            "clientleft", "clienttop", "clientheight", "clientwidth", "linktopic", "index"}


def main(argv):
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    src, dec = argv
    names = {f.lower(): f for f in os.listdir(dec) if f.lower().endswith((".frm", ".ctl"))}
    missing, changed, nctl, nprop = collections.Counter(), collections.Counter(), 0, 0
    lost = 0
    for f in sorted(os.listdir(src)):
        if not f.lower().endswith((".frm", ".ctl")) or f.lower() not in names:
            continue
        a, b = parse(os.path.join(src, f)), parse(os.path.join(dec, names[f.lower()]))
        for k in a:
            nctl += 1
            if k not in b:
                lost += 1
                if verbose:
                    print("%s: control %s not in the decompiled form" % (f, k))
                continue
            for p, v in a[k].items():
                nprop += 1
                if p not in b[k]:
                    if p not in DEFAULTS:
                        missing[p] += 1
                        if verbose:
                            print("%s %s: %s = %s missing" % (f, k.split("/")[-1], p, v[:50]))
                elif b[k][p] != v and not (p.endswith("font.name") and v == '"Arial"' and
                                           b[k][p] == '"Liberation Sans"'):      # (Wine's, at compile)
                    changed[p] += 1
                    if verbose:
                        print("%s %s: %s = %s, decompiled %s" % (f, k.split("/")[-1], p, v[:50], b[k][p][:50]))
            for p in b[k]:
                if p not in a[k] and p not in DEFAULTS and b[k][p] not in ("0", "0.0"):
                    changed["+" + p] += 1
                    if verbose:
                        print("%s %s: extra %s = %s" % (f, k.split("/")[-1], p, b[k][p][:50]))
        for k in b:
            if k not in a:
                lost += 1
                if verbose:
                    print("%s: extra control %s" % (f, k))
    print("%d controls, %d properties: %d controls differ in presence, %d missing, %d changed or extra" % (
        nctl, nprop, lost, sum(missing.values()), sum(changed.values())))
    print("missing:", missing.most_common(14))
    print("changed:", changed.most_common(14))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
