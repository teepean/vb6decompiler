#!/usr/bin/env python3
"""Open every menu item in two builds of a GUI program, under Wine, each from the same
data, and compare what comes up: windows, error log, data files.

usage: menucrawl.py DIR_A DIR_B REPORT [--wait S] [--only TEXT] [--poke STEP ...] -- ITEM...

DIR_A, DIR_B: the program folders (one .exe each), data ready.  The data files are saved
in DIR/.tmpl once and put back before every item, so one item's work doesn't reach the
next.  --poke steps run after starting (a password, say), before the menu item.
Everything happens in the two folders."""
import glob
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guicmp       # noqa: E402


def save(d):
    t = os.path.join(d, ".tmpl")
    if os.path.isdir(t):
        return
    os.mkdir(t)
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p) and not f.lower().endswith(".exe"):
            shutil.copy2(p, t)


def restore(d):
    t = os.path.join(d, ".tmpl")
    for f in os.listdir(t):
        shutil.copy2(os.path.join(t, f), d)
    open(os.path.join(d, "errors.log"), "w").close()


def norm(lines, d):
    # the folder's own name, and the exe's: nothing the builds decide
    w = d.replace("/", "\\").lower()
    out = []
    for ln in lines:
        x = ln.replace(w, "<dir>").replace(w.upper(), "<dir>")
        x = re.sub(r'(?i)z:' + re.escape(w), "<dir>", x)
        out.append(x)
    return out


def data_diff(a, b):
    """Files that differ beyond the bytes a Now and a path take: name, sizes, differing bytes."""
    out = []
    for f in sorted(os.listdir(a)):
        pa, pb = os.path.join(a, f), os.path.join(b, f)
        if not f.lower().endswith((".idx", ".ini", ".txt", ".old")) or not os.path.isfile(pa):
            continue
        if not os.path.exists(pb):
            out.append("%s only in A" % f)
            continue
        da, db = open(pa, "rb").read(), open(pb, "rb").read()
        if da != db:
            n = sum(x != y for x, y in zip(da, db))
            if f.lower() == "wintree.txt":
                continue
            out.append("%s %d/%d, %d bytes" % (f, len(da), len(db), n))
    for f in sorted(os.listdir(b)):
        if f.lower().endswith((".idx", ".ini", ".txt", ".old")) and not os.path.exists(os.path.join(a, f)):
            out.append("%s only in B" % f)
    return out


def main(argv):
    a, b, report = argv[:3]
    rest = argv[3:]
    wait, pokes = 5.0, []
    while rest and rest[0] != "--":
        if rest[0] == "--wait":
            wait = float(rest[1])
            rest = rest[2:]
        elif rest[0] == "--poke":
            pokes.append(rest[1])
            rest = rest[2:]
        else:
            return 2
    items = rest[1:]
    ea, eb = glob.glob(a + "/*.exe")[0], glob.glob(b + "/*.exe")[0]
    save(a)
    save(b)
    with open(report, "a", encoding="utf-8") as out:
        for item in items:
            restore(a)
            restore(b)
            step = ["menu:" + item] if not item.startswith("=") else item[1:].split("|")
            # A difference gets a second try: anyone typing or moving the mouse reaches the
            # windows under test (stray letters in a text box, MouseMove errors), and timing
            # moves a form by a pixel.  The report says when the second try matched.
            again = False
            try:
                for attempt in (1, 2):
                    sa = guicmp.dump(ea, wait, [], pokes + step)
                    sb = guicmp.dump(eb, wait, [], pokes + step)
                    la, lb = norm(sa[-1], a), norm(sb[-1], b)
                    if la == lb:
                        again = attempt == 2
                        break
                    if attempt == 1:
                        restore(a)
                        restore(b)
            except Exception as e:
                print("%-44s FAILED %s" % (item, e), file=out, flush=True)
                continue
            tops = [x for x in lb if x.startswith("0 ")][:2]
            errs = open(os.path.join(b, "errors.log"), errors="replace").read().strip().split("\n")
            errs = [e for e in errs if e and "temp.zip" not in e]
            ea_log = os.path.join(a, "errors.log")
            orig = open(ea_log, errors="replace").read().strip().split("\n") \
                if os.path.exists(ea_log) else []
            orig = [e for e in orig if e and "temp.zip" not in e]
            dd = data_diff(a, b)
            verdict = "same" if la == lb else "WINDOWS DIFFER"
            if again:
                verdict += " (second try)"
            print("=== %s: %s, %d / %d windows" % (item, verdict, len(la), len(lb)), file=out)
            for t in tops:
                print("    top %s" % t[:90], file=out)
            if la != lb:
                import difflib
                diff = list(difflib.unified_diff(la, lb, "A", "B", lineterm="", n=0))[2:]
                plus = [d for d in diff if d.startswith("+")]   # (what B shows comes first)
                for d in (plus + [d for d in diff if not d.startswith("+")])[:40]:
                    print("    %s" % d[:150], file=out)
            for e in errs[:12]:
                print("    errors.log: %s" % e[:120], file=out)
            for e in orig[:6]:
                print("    errors.log A: %s" % e[:120], file=out)
            if errs or orig:
                print("    errors.log lines: %d A, %d B" % (len(orig), len(errs)), file=out)
            for d in dd:
                print("    data: %s" % d, file=out)
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
