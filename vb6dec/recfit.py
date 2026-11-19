"""Records written to Random files: the tail padding of a Type, from Open's Len.

The Type's size in memory rounds up to its widest field, so its last bytes may be
data or just that rounding, and memory alone cannot tell.  On disk there is no
rounding: Open ... Len = 200 with Put #f, n, rec says.  a target's T_C1_13, T_B3_45
and TRec19 have every field where the program's own settings file has it, and were one
byte over ("Bad record length"): their pad held a rounding byte.  Works on the
written source, as the numbers need the final Types."""
import os
import re

SIZES = {"Byte": 1, "Integer": 2, "Boolean": 2, "Long": 4, "Single": 4, "Double": 8,
         "Currency": 8, "Date": 8, "Object": 4}
PROC = re.compile(r"(?:(?:Public|Private|Friend) )?(?:Static )?(?:Sub|Function|Property \w+) \w+\((.*)\)")
FIELD = re.compile(r"\s*(\w+)(?:\(([^)]*)\))?\s+As\s+(\w+)(?:\s*\*\s*(\d+))?")


def field_sizes(body, types):
    """[(name, count, disk size, alignment)], or None when not computable."""
    out = []
    for ln in body:
        m = FIELD.match(ln)
        if not m:
            return None
        name, bounds, t, fix = m.groups()
        n = 1
        if bounds is not None:
            if not bounds.strip():
                return None                 # a dynamic array: its own format on disk
            for b in bounds.split(","):
                q = re.match(r"\s*(?:(-?\d+) To )?(-?\d+)\s*$", b)
                if not q:
                    return None
                n *= int(q.group(2)) - int(q.group(1) or 0) + 1
        if t == "String":
            if not fix:
                return None                 # variable length
            size, al = int(fix), 1
        elif t in SIZES:
            size, al = SIZES[t], min(SIZES[t], 4)
        elif t in types:
            sub = field_sizes(types[t][1], types)
            if sub is None:
                return None
            size = sum(c * s for _, c, s, _ in sub)
            al = max((a for _, _, _, a in sub), default=1)
        else:
            return None
        out.append((name, n, size, al))
    return out


def fit(outdir, files):
    texts, types = {}, {}
    for _, fn, _ in files:
        p = os.path.join(outdir, fn)
        lines = open(p, encoding="cp1252", newline="").read().split("\r\n")
        texts[fn] = lines
        for i, ln in enumerate(lines):
            m = re.match(r"(?:Public |Private )?Type (\w+)$", ln)
            if m:
                j = next(k for k in range(i, len(lines)) if lines[k].startswith("End Type"))
                types[m.group(1)] = (fn, lines[i + 1:j], i + 1, j)
    lens, puts = {}, []
    decl = re.compile(r"\s*(?:Public |Private |Dim |Static )(\w+) As (\w+)\s*$")
    module_vars = {}
    for fn, lines in texts.items():
        for ln in lines:
            m = re.match(r"Public (\w+) As (\w+)\s*$", ln)
            if m:
                module_vars[m.group(1)] = m.group(2)
    for fn, lines in texts.items():
        local = {}
        for ln in lines:
            s = ln.strip()
            m = PROC.match(s)
            if m:
                local = {}
                for a in m.group(1).split(","):
                    q = re.search(r"(\w+) As (\w+)$", a.strip())
                    if q:
                        local[q.group(1)] = q.group(2)
                continue
            m = decl.match(ln)
            if m:
                local[m.group(1)] = m.group(2)
                continue
            m = re.search(r"For Random As #(\w+) Len = (\d+)$", s)
            if m:
                lens.setdefault(m.group(1), set()).add(int(m.group(2)))
                continue
            m = re.match(r"(?:Put|Get) #(\w+), [^,]*, (\w+)$", s)
            if m:
                t = local.get(m.group(2)) or module_vars.get(m.group(2))
                if t in types:
                    puts.append((m.group(1), t))
    want = {}
    for f, t in puts:
        ls = lens.get(f, ())
        if len(ls) == 1:
            want.setdefault(t, set()).update(ls)
    changed = set()
    for t, ls in want.items():
        if len(ls) != 1:
            continue
        fn, body, start, end = types[t]
        sizes = field_sizes(body, types)
        if not sizes:
            continue
        over = sum(c * s for _, c, s, _ in sizes) - next(iter(ls))
        last = re.match(r"(\s*pad\d+\(1 To )(\d+)(\) As Byte)$", body[-1])
        al = max(a for _, _, _, a in sizes)
        if over <= 0 or over >= al or not last or int(last.group(2)) <= over:
            continue                        # (more than rounding could explain)
        texts[fn][end - 1] = "%s%d%s" % (last.group(1), int(last.group(2)) - over, last.group(3))
        changed.add(fn)
    for fn in changed:
        open(os.path.join(outdir, fn), "w", encoding="cp1252", newline="").write("\r\n".join(texts[fn]))
    return sorted(want)
