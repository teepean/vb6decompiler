"""ByRef arguments of a scalar type other than the parameter's: pass a copy.

The types come from evidence, and a program's own width can only be guessed where a
Byte field goes to a parameter read as a word.  VB refuses the call ("ByRef argument
type mismatch") and the whole procedure would be lost (a target Proc_7367B0, which
fills the main form).  `Proc (x)` passes a temporary of the right type: the same value,
when the callee does not store to its parameter, which is checked.  Works on the
written source with tools/vbcheck.py."""
import os
import re
import sys

NUMERIC = {"byte", "integer", "long", "single", "double", "boolean", "currency", "variant"}
PAT = re.compile(r"ByRef argument type mismatch: (.+) \((\w+)\) passed to (\w+) As (\w+) of (\w+)$")


def type_fields(lines_of, name):
    """[(field, type)] of Type name (any case), and where it is declared."""
    for fn, ls in lines_of():
        for i, ln in enumerate(ls):
            if re.match(r"(?i)(Public |Private )?Type %s\s*$" % re.escape(name), ln):
                out = []
                for x in ls[i + 1:]:
                    if x.startswith("End Type"):
                        return out
                    m = re.match(r"\s*(\w+)(\([^)]*\))? As ([\w ]+?)(?:\s*\*\s*\d+)?\s*$", x)
                    if m:
                        out.append((m.group(1), m.group(3), bool(m.group(2))))
                return out
    return None


def record_case(arg, have, param, want, proc, fn, i, lines, callee_body, changed):
    """A record for a scalar parameter is its first field (VB passes the address either
    way: a target Proc_8653B0 Proc_8B3660 a1.f0, 8).  A record for another Type's
    parameter (a synthetic one, from what the callee reads) is fine when every field
    the callee reads is in the record: the parameter takes the record's Type."""
    outdir_files = LINES.get("files")
    fields = type_fields(outdir_files, have)
    if fields is None:
        return False
    ls = lines(fn)
    ln = ls[i - 1] if i - 1 < len(ls) else ""
    if want.lower() in NUMERIC:
        if fields and not fields[0][2] and fields[0][1].lower() == want.lower():
            new = re.sub(r"(?<![\w.])%s(?=\s*(,|\)|$))" % re.escape(arg),
                         "%s.%s" % (arg, fields[0][0]), ln, count=1)
            if new != ln:
                ls[i - 1] = new
                changed.add(fn)
                return True
        return False
    if fields and not fields[0][2] and fields[0][1].lower() == want.lower():
        # (a record whose first field is of the parameter's Type: With Killer(i): ClearMove .Killer1)
        new = re.sub(r"(?<![\w.])%s(?=\s*(,|\)|$))" % re.escape(arg),
                     "%s.%s" % (arg, fields[0][0]), ln, count=1)
        if new != ln:
            ls[i - 1] = new
            changed.add(fn)
            return True
    names = {f.lower() for f, _, _ in fields}
    used = {u.lower() for x in callee_body(proc)
            for u in re.findall(r"\b%s\.(\w+)" % re.escape(param), x, re.I)}
    if not used or used - names:
        return False
    for f2, ls2 in outdir_files():
        for k, x in enumerate(ls2):
            if re.match(r"(?i)(Public |Private |Friend )?(Sub|Function) %s\(" % re.escape(proc), x):
                new = re.sub(r"(?i)\b%s As %s\b" % (re.escape(param), re.escape(want)),
                             "%s As %s" % (param, have), x, count=1)
                if new != x:
                    ls2[k] = new
                    changed.add(f2)
                    return True
    return False


LINES = {}


def fix(outdir):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    import vbcheck
    problems = vbcheck.Project(outdir).check()
    texts = {}

    def lines(fn):
        if fn not in texts:
            texts[fn] = open(os.path.join(outdir, fn), encoding="cp1252", newline="").read().split("\r\n")
        return texts[fn]

    def all_files():
        return [(fn, lines(fn)) for fn in os.listdir(outdir)
                if fn.lower().endswith((".bas", ".frm", ".cls", ".ctl"))]
    LINES["files"] = all_files
    stores = {}

    def callee_stores(proc, param):
        """Does the procedure assign to the parameter, or hand it on?"""
        k = proc.lower()
        if k not in stores:
            body = []
            for fn in os.listdir(outdir):
                if not fn.lower().endswith((".bas", ".frm", ".cls", ".ctl")):
                    continue
                inside = False
                for ln in lines(fn):
                    if re.match(r"(?i)(Public |Private |Friend )?(Sub|Function) %s\(" % re.escape(proc), ln):
                        inside = True
                        continue
                    if inside:
                        if re.match(r"End (Sub|Function)", ln):
                            break
                        body.append(ln)
            stores[k] = body
        pat = re.compile(r"^\s*(Get #.*, |Input #.*, |Line Input #.*, |ReDim |LSet |RSet |Set )?%s\b(?=\s*=|\s*$)"
                         % re.escape(param), re.I)
        byref = re.compile(r"\bProc_\w+[ (].*\b%s\b" % re.escape(param), re.I)
        return any(pat.match(x) or byref.search(x) for x in stores[k])
    changed = set()

    def callee_body(proc):
        callee_stores(proc, "\0")
        return stores[proc.lower()]
    for fn, i, msg in problems:
        m = PAT.match(msg)
        if not m:
            continue
        arg, have, param, want, proc = m.groups()
        if record_case(arg, have, param, want, proc, fn, i, lines, callee_body, changed):
            continue
        # (a String one way is the same: VB converts the copy, and the type was only
        # evidence: a target m3C(i, j).f14 As String, a record number)
        if have.lower() not in NUMERIC | {"string"} or want.lower() not in NUMERIC | {"string"}:
            continue
        if callee_stores(proc, param):
            # it stores to the parameter, so a copy won't do.  One of the two types is
            # wrong (VB accepted the original); a Variant parameter takes the variable by
            # reference whatever its type, and the store reaches it.
            if have.lower() != "string" and want.lower() != "string" and \
                    not any(re.search(r"\b(Proc_\w+|Get #|Input #|Line Input #)\b.*\b%s\b" % re.escape(param), x, re.I)
                            for x in callee_body(proc)):
                for f2, ls2 in all_files():
                    for k, x in enumerate(ls2):
                        if re.match(r"(?i)(Public |Private |Friend )?(Sub|Function) %s\(" % re.escape(proc), x):
                            new = re.sub(r"(?i)\b%s As %s\b" % (re.escape(param), re.escape(want)),
                                         "%s As Variant" % param, x, count=1)
                            if new != x:
                                ls2[k] = new
                                changed.add(f2)
                continue
            if have.lower() == "string" or want.lower() == "string":
                continue
            # it hands the parameter on as well: a copy, marked, rather than a procedure
            # that doesn't compile and is lost whole (a target Proc_9B2B70, Proc_75F470)
            note = True
        else:
            note = False
        ls = lines(fn)
        ln = ls[i - 1] if i - 1 < len(ls) else ""
        # (a whole argument first: Proc v * (a + b), c, b wrapped the b of the sum)
        new = re.sub(r"(?:(?<=, )|(?<=\()|(?<=\b%s ))%s(?=\s*(,|\)|$))" % (re.escape(proc), re.escape(arg)),
                     "(%s)" % arg, ln, count=1)
        if new == ln:
            new = re.sub(r"(?<![\w.(])%s(?=\s*(,|\)|$))|(?<=\()%s(?=\s*(,|\)))" % (re.escape(arg), re.escape(arg)),
                         "(%s)" % arg, ln, count=1)
        if new != ln:
            ls[i - 1] = new + ("  ' vb6dec: a copy, %s may store to it" % proc if note else "")
            changed.add(fn)
    for fn in changed:
        open(os.path.join(outdir, fn), "w", encoding="cp1252", newline="").write("\r\n".join(texts[fn]))
    return len(changed)
