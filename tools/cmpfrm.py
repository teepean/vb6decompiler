"""Compare decoded form designer text with an original .frm (properties
per control, order-insensitive, comments ignored)."""
import re
import sys


def parse(text):
    ctl = {}
    stack = []
    for line in text.replace("\r", "").split("\n"):
        t = line.strip()
        if t.startswith("Attribute ") or not t:
            continue
        m = re.match(r"Begin (\S+) (\S+)", t)
        if m:
            stack.append(m.group(2))
            ctl.setdefault(m.group(2), {"class": m.group(1)})
            continue
        if t.startswith("BeginProperty"):
            stack.append(stack[-1] + "." + t.split()[1])
            ctl.setdefault(stack[-1], {})
            continue
        if t in ("End", "EndProperty"):
            if stack:
                stack.pop()
            if not stack and t == "End":
                break
            continue
        m = re.match(r"(\w+(?:\(\d+\))?)\s*=\s*(.*)$", t)
        if m and stack:
            v = re.sub(r"\s*'.*$", "", m.group(2)).strip()
            if v.startswith('"') and v.count('"') >= 2:
                v = v[:v.rindex('"') + 1]
            ctl[stack[-1]][m.group(1)] = v
    return ctl


def main(orig, dec):
    a = parse(open(orig, encoding="latin-1").read().split("\nAttribute VB_Name")[0])
    b = parse(open(dec, encoding="latin-1").read())
    bad = 0
    for name in sorted(set(a) | set(b)):
        pa, pb = a.get(name), b.get(name)
        if pa is None or pb is None:
            print("%-22s only in %s" % (name, "original" if pb is None else "decoded"))
            bad += 1
            continue
        for k in sorted(set(pa) | set(pb)):
            if pa.get(k) != pb.get(k):
                print("%-22s %-16s orig=%-24s dec=%s" % (name, k, pa.get(k), pb.get(k)))
                bad += 1
    print("differences:", bad)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
