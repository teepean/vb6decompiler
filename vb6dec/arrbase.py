"""A dynamic array's base left in a register variable: `t_ecx = global_F04644`.

(Fixed arrays too.)  Element accesses have used the base by the time the source is written; what is left
is a store VB refuses (Type mismatch), and the whole procedure with it (a target
Search1, which writes the burial date index).  Dropping it during simplify is too
early: the passes still need it to find elements (t_ecx(i) came out).  Works on the
written source: a read of the variable would not have compiled either.

Also an array field reached from the field before it: x(i).f4C(k) for x(i).f50(k - 1).
i * 4 + 4Ch is as good an address as (i - 1) * 4 + 50h, and with a scalar f4C and an
array of Longs at f50 only the second is VB (a target Proc_851B20: Expected array).

And a record's first field only ever reached as the element itself (a(i) read as a dword,
which the render writes a(i).pad1(1), the first thing the Type declares): a Byte holding
a person number, Overflow or a lost value (a target global_F050A8(0).pad1(1) = a1)."""
import os
import re


def fix(outdir, files):
    arrays = set()
    texts = {}
    for _, fn, _ in files:
        p = os.path.join(outdir, fn)
        texts[fn] = open(p, encoding="cp1252", newline="").read().split("\r\n")
        for ln in texts[fn]:
            m = re.match(r"(?:Public |Private |Global )?(global_\w+)\([^)]*\) As ", ln)
            if m:
                arrays.add(m.group(1))
    original = {fn: list(x) for fn, x in texts.items()}
    sigs = single_params(texts)
    fixed_strs = {}
    for lines in texts.values():
        for ln in lines:
            m = re.match(r"(?:Public |Private |Global )?(global_\w+)\((?:(-?\d+) To )?\d+\) As String \* \d+", ln)
            if m:
                fixed_strs[m.group(1)] = int(m.group(2) or 0)
    first_fields(texts)
    fields = array_fields(texts)
    pat = re.compile(r"\s*t_\w+ = (global_\w+)\s*$")
    for fn, lines in texts.items():
        lines = texts[fn] = object_slots(plain_scalars(lines)) if any(" = " in x for x in lines) else lines
        lines = array_params_in_registers(lines)
        kept = [single_literals(first_element(shift_field(ln, fields), fixed_strs), sigs) for ln in lines
                if not ((m := pat.match(ln)) and m.group(1) in arrays)]
        if kept != original[fn]:
            open(os.path.join(outdir, fn), "w", encoding="cp1252", newline="").write("\r\n".join(kept))


def array_params_in_registers(lines):
    """The same for an array parameter: t_ecx = a8 (a target Proc_7E1470, Type mismatch)."""
    head = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property \w+) \w+\((.*)\)")
    pat = re.compile(r"\s*t_\w+ = (\w+)\s*$")
    out, params = [], set()
    for ln in lines:
        m = head.match(ln)
        if m:
            params = set(re.findall(r"(\w+)\(\)", m.group(1)))
        m = pat.match(ln)
        if m and m.group(1) in params:
            continue
        out.append(ln)
    return out


SIZES = {"Long": 4, "Integer": 2, "Single": 4, "Double": 8, "Byte": 1, "Boolean": 2}


def array_fields(texts):
    """{(Type, scalar field): the array field right after it}, and the module
    arrays' Types."""
    after, gtypes, cur, prev = {}, {}, None, None
    for lines in texts.values():
        for ln in lines:
            t = re.match(r"(?:Public |Private )?Type (\w+)$", ln)
            if t:
                cur, prev = t.group(1), None
                continue
            if ln.startswith("End Type"):
                cur = None
                continue
            g = re.match(r"(?:Public |Private |Global )?(global_\w+)\([^)]*\) As (\w+)", ln)
            if g:
                gtypes[g.group(1)] = g.group(2)
            if cur:
                m = re.match(r"\s*(f[0-9A-F]+)(\([^)]*\))? As (\w+)", ln)
                if not m:
                    prev = None
                    continue
                name, dims, ty = m.groups()
                if dims and prev and "," not in dims and int(name[1:], 16) - int(prev[1:], 16) == SIZES.get(ty):
                    after[(cur, prev)] = name
                prev = None if dims else name
    return after, gtypes


def shift_field(ln, fields):
    after, gtypes = fields
    for m in list(re.finditer(r"\b(global_\w+)\([^()]*\)\.(f[0-9A-F]+)\(", ln))[::-1]:
        b = after.get((gtypes.get(m.group(1)), m.group(2)))
        if b is None:
            continue
        depth, j = 1, m.end()
        while j < len(ln) and depth:
            depth += {"(": 1, ")": -1}.get(ln[j], 0)
            j += 1
        if depth == 0:
            ln = ln[:m.start(2)] + b + "(" + ln[m.end():j - 1] + " - 1)" + ln[j:]
    return ln


def plain_scalars(lines):
    """Dim v() As T for a slot only ever used whole (v = 0: Can't assign to array): the
    frame slot looked like an array in some pass, nothing subscripts it here.  A number
    has to be assigned: v = obj.RetArr is an array function's result (tests/t04w)."""
    out, i, n = list(lines), 0, len(lines)
    head = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property)\b")
    while i < n:
        if not head.match(lines[i]):
            i += 1
            continue
        e = i + 1
        while e < n and not re.match(r"End (?:Sub|Function|Property)", lines[e]):
            e += 1
        for k in range(i, e):
            m = re.match(r"(\s*Dim (v[0-9A-F]+))\(\)( As \w+)\s*$", lines[k])
            if not m:
                continue
            v = m.group(2)
            rest = [x for j, x in enumerate(lines[i:e], i) if j != k]
            if not any(re.search(r"\b%s\s*\(" % v, x) or re.search(r"\b(?:ReDim|Erase)\b.*\b%s\b" % v, x) or
                       re.search(r"\b(?:UBound|LBound)\b.*\b%s\b" % v, x) for x in rest) and \
                    any(re.match(r"\s*%s = -?[\d.]" % v, x) for x in rest):
                # (a slot that takes Rnd() is a Single: an Integer makes every draw 0)
                single = any(re.match(r"\s*%s = Rnd\(" % v, x) for x in rest)
                out[k] = m.group(1) + (" As Single" if single else m.group(3))
                if single:
                    # (its integers are ByRef literals of the calls that follow, for
                    # Long parameters: v = 3, Proc v is Proc 3)
                    for j in range(i, e - 1):
                        q = re.match(r"\s*%s = (-?\d+)\s*$" % v, out[j])
                        if q and re.search(r"\b%s\b" % v, out[j + 1]):
                            out[j + 1] = re.sub(r"\b%s\b" % v, q.group(1), out[j + 1])
                            out[j] = "\0"
        i = e + 1
    return missing_values(var_for_helpers(double_halves(variant_pairs(object_tests(number_objects(
        [x for x in out if x != "\0"]))))))


def number_objects(lines):
    """Dim v As Object for a slot that also gets a number: v = 1117 / global_F045C4(v + 1).
    VB reuses a frame temporary for the subscript of an array and for an object, and the
    object won: it compiles (late bound), and fails with error 91 when it runs.  A slot
    that never is an object is a Long."""
    out, i, n = list(lines), 0, len(lines)
    head = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property)\b")
    while i < n:
        if not head.match(lines[i]):
            i += 1
            continue
        e = i + 1
        while e < n and not re.match(r"End (?:Sub|Function|Property)", lines[e]):
            e += 1
        for k in range(i, e):
            m = re.match(r"(\s*)Dim (v[0-9A-F]+) As Object\s*$", lines[k])
            if not m:
                continue
            v = m.group(2)
            num = re.compile(r"\s*(?:\d+ )?%s = -?\d+&?\s*$" % v)
            rest = range(i, e)
            if not any(num.match(lines[j]) for j in rest):
                continue
            # (a slot that also is an object would need a second variable: none yet)
            obj = re.compile(r"\bSet %s\b|\b%s\.|\b%s Is\b|\bIs %s\b" % (v, v, v, v))
            if not any(obj.search(lines[j]) for j in rest):
                out[k] = "%sDim %s As Long" % (m.group(1), v)
        i = e + 1
    return out


def var_for_helpers(lines):
    """A For with a Variant counter whose shape the structuring could not rebuild (a jump
    into it: a target Proc_D50680) stays calls of the runtime's two helpers, which VB source
    cannot name.  The same thing, as procedures of the module."""
    if any(re.search(r"\bCyFor(?:Init|Next)\(", x) for x in lines):
        lines = lines + [
            "",
            "Private Function CyForInit(c As Currency, ByVal e As Currency, ByVal s As Currency, _",
            "        ByVal first As Currency, ByVal last As Currency, ByVal stp As Currency) As Long",
            "    c = first",
            "    CyForInit = IIf(stp >= 0, c <= last, c >= last)",
            "End Function",
            "",
            "Private Function CyForNext(c As Currency, ByVal e As Currency, ByVal s As Currency) As Long",
            "    c = c + s",
            "    CyForNext = IIf(s >= 0, c <= e, c >= e)",
            "End Function"]
    if not any(re.search(r"\bVarFor(?:Init|Next)\(", x) for x in lines):
        return lines
    return lines + [
        "",
        "Private Function VarForInit(c, e, s, ByVal first, ByVal last, ByVal stp) As Long",
        "    c = first: e = last: s = stp",
        "    VarForInit = IIf(s >= 0, c <= e, c >= e)",
        "End Function",
        "",
        "Private Function VarForNext(c, e, s) As Long",
        "    c = c + s",
        "    VarForNext = IIf(s >= 0, c <= e, c >= e)",
        "End Function"]


def missing_values(lines):
    """A slot holding an omitted argument (VT_ERROR, DISP_E_PARAMNOTFOUND) for an Optional
    parameter passed by reference: the tag and the value were written in a block before the
    calls (a target Proc_7E1470 a_2 = ...).  VB source has no such literal, but an Optional
    Variant nobody passed is one, and copying it keeps it."""
    pat = re.compile(r"^(\s*\w+ = )\s*$")
    if not any(pat.match(x) for x in lines):
        return lines
    return [pat.sub(r"\1MissingArg()", x) for x in lines] + [
        "",
        "Private Function MissingArg(Optional v As Variant) As Variant",
        "    MissingArg = v",
        "End Function"]


NOT_CLASS = {"byte", "boolean", "integer", "long", "single", "double", "currency", "date",
             "string", "variant"}


def object_tests(lines):
    """cmp [m34], 0 before the New of a Dim As New prints If m34 = 0, and the register copy
    t_ebx = m34 has no Set: both read a default member, of a Nothing here, error 91 when it
    runs (a target Form8, the backup).  Declarations say which variables are objects:
    the module's, and the procedure's Dims (a local of the same name wins)."""
    head = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property)\b")
    decl = re.compile(r"\s*(?:Public |Private |Dim |Static )(?:WithEvents )?(\w+) As (?:New )?(\w+)\s*$")
    types = {}          # lower case type names of the module's records: no objects
    for ln in lines:
        m = re.match(r"(?:Public |Private )?Type (\w+)\s*$", ln)
        if m:
            types[m.group(1).lower()] = True

    def is_object(t):
        t = t.lower()
        return t not in NOT_CLASS and t not in types and not re.match(r"trec\d|t_\d", t)
    module = {}
    for ln in lines:
        if head.match(ln):
            break
        m = decl.match(ln)
        if m:
            module[m.group(1).lower()] = is_object(m.group(2))
    out, i, n = list(lines), 0, len(lines)
    test = re.compile(r"(\s*(?:\d+ )?(?:ElseIf|If) )(\w+) (=|<>) 0( Then\b.*)$")
    copy = re.compile(r"(\s*(?:\d+ )?)(t_\w+) = (\w+)\s*$")
    while i < n:
        if not head.match(lines[i]):
            i += 1
            continue
        e = i + 1
        local = {}
        while e < n and not re.match(r"End (?:Sub|Function|Property)", lines[e]):
            m = decl.match(lines[e])
            if m:
                local[m.group(1).lower()] = is_object(m.group(2))
            e += 1

        def obj(v):
            v = v.lower()
            return local[v] if v in local else module.get(v, False)
        for k in range(i, e):
            m = test.match(lines[k])
            if m and obj(m.group(2)):
                out[k] = "%s%s%s Is Nothing%s" % (m.group(1), "Not " if m.group(3) == "<>" else "",
                                                 m.group(2), m.group(4))
                continue
            m = copy.match(lines[k])
            if m and obj(m.group(3)):
                out[k] = "%sSet %s = %s" % m.groups()
        i = e + 1
    return out


def double_halves(lines):
    """Select Case on a Double compares its two dwords, the variable being assigned whole:
    v3C <> 0 Or v38 <> &H3FF00000 is v3C <> 1, and the second half (v38) never had a
    value, so every Case failed (a target Proc_8F2150, Select Case Val(a2)).  Last, in
    two levels: If v3C = 0 / If v38 = &H40100000 (their Else parts are the same)."""
    import struct

    def dbl(h):
        v = struct.unpack("<d", struct.pack("<II", 0, int(h, 16)))[0]
        return "%d" % v if v == int(v) else repr(v)

    def adjacent(lo, hi):
        return int(lo[1:], 16) - 4 == int(hi[1:], 16)
    pats = (re.compile(r"\b(v[0-9A-F]+) <> 0 Or (v[0-9A-F]+) <> &H([0-9A-F]+)\b"),
            re.compile(r"\b(v[0-9A-F]+) = 0 And (v[0-9A-F]+) = &H([0-9A-F]+)\b"))
    ops = ("<>", "=")
    # (the low half inlined: Val(x) <> 0 Or v7C <> &H3FF00000; a target has fourteen)
    inl = (re.compile(r"(\bVal\([^()]*\)|\bt_e[a-z]{2}\d*) <> 0 Or (?:v[0-9A-F]+|t_e[a-z]{2}\d*) <> &H(3FF00000|4[0-9A-F]{7})\b"),
           re.compile(r"(\bVal\([^()]*\)|\bt_e[a-z]{2}\d*) = 0 And (?:v[0-9A-F]+|t_e[a-z]{2}\d*) = &H(3FF00000|4[0-9A-F]{7})\b"))
    out = list(lines)
    for i, ln in enumerate(out):
        if "&H" not in ln:
            continue
        for pat, op in zip(pats, ops):
            def repl(m):
                if not adjacent(m.group(1), m.group(2)):
                    return m.group(0)
                return "%s %s %s" % (m.group(1), op, dbl(m.group(3)))
            ln = pat.sub(repl, ln)
        for pat, op in zip(inl, ops):
            ln = pat.sub(lambda m: "%s %s %s" % (m.group(1), op, dbl(m.group(2))), ln)
        out[i] = ln
        m = re.match(r"(\s*)If (v[0-9A-F]+) = &H([0-9A-F]+) Then\s*$", ln)
        if m and i:
            q = re.match(r"(\s*)If (v[0-9A-F]+) = 0 Then\s*$", out[i - 1])
            if q and adjacent(q.group(2), m.group(2)):
                out[i - 1] = "%sIf True Then" % q.group(1)
                out[i] = "%sIf %s = %s Then" % (m.group(1), q.group(2), dbl(m.group(3)))
    return out


def variant_pairs(lines):
    """A Variant temporary's halves written apart: v.f8 = x, v = 3 is v = x, and
    v.f8 = &H80020004, v = 10 an omitted argument for nothing (MsgBox's in an
    error handler).  v.f8 on a Variant compiles and fails at run time (Object required)."""
    out, i, n, variants = [], 0, len(lines), set()
    head = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property)\b")
    while i < n:
        ln = lines[i]
        if head.match(ln):
            variants = set()
        m = re.match(r"\s*Dim (\w+) As Variant\s*$", ln)
        if m:
            variants.add(m.group(1))
        m = re.match(r"(\s*)(\w+)\.f8 = (.+?)\s*$", ln)
        if m and m.group(2) in variants and i + 1 < n:
            t = re.match(r"\s*%s = (\d+)\s*$" % m.group(2), lines[i + 1])
            if t and t.group(1) == "10" and m.group(3).upper() == "&H80020004":
                i += 2
                continue
            if t and int(t.group(1)) in (2, 3, 4, 5, 7, 8, 11):
                out.append("%s%s = %s" % (m.group(1), m.group(2), m.group(3)))
                i += 2
                continue
        out.append(ln)
        i += 1
    return out


def receiver_end(ln, dot):
    """The start of the name of name(...) ending right before position dot, or None."""
    j = dot - 1
    if j < 0 or ln[j] != ")":
        return None
    depth = 0
    while j >= 0:
        depth += {")": 1, "(": -1}.get(ln[j], 0)
        if depth == 0:
            break
        j -= 1
    if j <= 0:
        return None
    k = j
    while k > 0 and (ln[k - 1].isalnum() or ln[k - 1] == "_"):
        k -= 1
    return k if k < j else None


def first_fields(texts):
    """Declare f0 for Types starting with pad1, whose pad1(1) is the first field."""
    types, owners = {}, {}
    for fn, lines in texts.items():
        for i, ln in enumerate(lines):
            m = re.match(r"(?:Public |Private )?Type (\w+)$", ln)
            if m:
                types[m.group(1)] = (fn, i + 1)
            m = re.match(r"(?:Public |Private |Global |Dim )?(global_\w+|m[0-9A-F]+)\([^)]*\) As (\w+)", ln)
            if m:
                owners.setdefault(m.group(1), set()).add(m.group(2))
    uses = {}                   # Type -> [(file, line)]
    words = {}                  # Type -> the receiver is given a CInt: an Integer
    for fn, lines in texts.items():
        for i, ln in enumerate(lines):
            at = ln.find(".pad1(1)")
            while at >= 0:
                k = receiver_end(ln, at)
                ts = owners.get(ln[k:ln.index("(", k)]) if k is not None else None
                if ts and len(ts) == 1:
                    t = next(iter(ts))
                    uses.setdefault(t, []).append((fn, i))
                    if re.search(r"\.pad1\(1\) = CInt\(", ln):
                        words[t] = True
                at = ln.find(".pad1(1)", at + 1)
    for t, where in uses.items():
        if t not in types:
            continue
        fn, i = types[t]
        lines = texts[fn]
        m = re.match(r"(\s*)pad1\(1 To (\d+)\) As Byte\s*$", lines[i])
        n = int(m.group(2)) if m else 0
        if n < 2:
            continue
        kind, w = ("Long", 4) if n >= 4 and not words.get(t) else ("Integer", 2)
        new = ["%sf0 As %s" % (m.group(1), kind)]
        if n > w:
            new.append("%spad0(1 To %d) As Byte" % (m.group(1), n - w))
        lines[i:i + 1] = new
        delta = len(new) - 1
        # (later lines of this file moved)
        where2 = [(f, j + delta if f == fn and j > i else j) for f, j in where]
        for f, j in sorted(set(where2)):
            ln = texts[f][j]
            out, at = "", 0
            while True:
                hit = ln.find(".pad1(1)", at)
                if hit < 0:
                    out += ln[at:]
                    break
                k = receiver_end(ln, hit)
                ts = owners.get(ln[k:ln.index("(", k)]) if k is not None else None
                out += ln[at:hit] + (".f0" if ts == {t} else ".pad1(1)")
                at = hit + len(".pad1(1)")
            texts[f][j] = out
        if delta:
            for t2, (f2, i2) in list(types.items()):
                if f2 == fn and i2 > i:
                    types[t2] = (f2, i2 + delta)
            for t2, w2 in uses.items():
                if t2 != t:
                    uses[t2] = [(f, j + delta if f == fn and j > i else j) for f, j in w2]


def first_element(ln, fixed_strs):
    """A fixed array of String * n joined as a string: its first element, which is what
    the code reads at pvData (a target Proc_7E1470 "..." & global_F04564 & "...")."""
    if "&" not in ln:
        return ln
    def repl(m):
        lb = fixed_strs.get(m.group(2))
        return m.group(0) if lb is None else "%s%s(%d)" % (m.group(1), m.group(2), lb)
    return re.sub(r"(& )(global_\w+)\b(?!\s*\()", repl, ln)


def object_slots(lines):
    """A frame slot that holds controls of different classes in turn, declared as one of
    them: Set v38 = Me.Line1(i) on a v38 As TextBox is a Type mismatch at run time
    (a target Walk Form_Resize, found by menucrawl).  As Object it holds any."""
    classes = {}
    for ln in lines:
        m = re.match(r"\s*Begin (\w+)\.(\w+) (\w+)", ln)
        if m:
            classes[m.group(3).lower()] = m.group(2)
    if not classes:
        return lines
    out = list(lines)
    head = re.compile(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function|Property)\b")
    i, n = 0, len(lines)
    while i < n:
        if not head.match(lines[i]):
            i += 1
            continue
        e = i + 1
        while e < n and not re.match(r"End (?:Sub|Function|Property)", lines[e]):
            e += 1
        sets = {}
        for x in lines[i:e]:
            m = re.match(r"\s*\d*\s*Set (\w+) = Me\.(\w+)", x)
            if m and m.group(2).lower() in classes:
                sets.setdefault(m.group(1), set()).add(classes[m.group(2).lower()])
        for v, cs in sets.items():
            if len(cs) > 1:
                for k in range(i, e):
                    m = re.match(r"(\s*Dim %s As )\w+\s*$" % re.escape(v), out[k])
                    if m:
                        out[k] = m.group(1) + "Object"
        i = e + 1
    return out


def bits_value(text):
    """The Single whose bits the literal text is, as VB source; None if it's no such thing:
    too small a number, or a value nobody writes."""
    import struct
    t = text.strip().rstrip("&")
    try:
        n = int(t[2:], 16) if t.upper().startswith("&H") else int(t)
    except ValueError:
        return None
    n &= 0xffffffff
    if n < 0x30000000 or 0x80000000 <= n < 0xB0000000 or n >= 0xD0000000:
        return None
    v = struct.unpack("<f", struct.pack("<I", n))[0]
    if v != v or abs(v) > 1e15:        # (a NaN or infinity, or more than anybody draws with)
        return None
    r = float("%.7g" % v)
    return ("%d!" % r) if r == int(r) and abs(r) < 1e7 else "%s!" % ("%.7g" % r)


def single_params(texts):
    """{procedure name: [is the parameter a Single]}"""
    out = {}
    for lines in texts.values():
        for ln in lines:
            m = re.match(r"(?:Public |Private |Friend )?(?:Static )?(?:Sub|Function) (\w+)\((.*?)\)(?: As \w+)?\s*$", ln)
            if m:
                ps = [p.strip() for p in m.group(2).split(",")]
                if any(p.endswith(" As Single") for p in ps):
                    out[m.group(1).lower()] = [p.endswith(" As Single") for p in ps]
    return out


LIT = r"(&H[0-9A-F]{8}&?|-?\d{9,10})"


def single_literals(ln, sigs):
    """A Single's bits printed as a number.  VB stores a Single literal with a plain mov,
    and where the slot's type got lost it stays an integer: For v = 32! To &H42240000 Step
    1065353216 runs to a billion, a(CLng(&H42800000)) for a(64) is out of range, and
    Proc r, &H3F19999A passes 1 billion for 0.6 (a target's report and tree drawing)."""
    if "&H" not in ln and not re.search(r"\d{9}", ln):
        return ln
    m = re.match(r"(\s*(?:\d+ )?For \w+ = )(\S+)( To )(\S+)(?:( Step )(\S+))?\s*$", ln)
    if m and (m.group(2).endswith("!") or bits_value(m.group(4))):
        a = bits_value(m.group(2)) or m.group(2)
        b = bits_value(m.group(4)) or m.group(4)
        c = (bits_value(m.group(6)) or m.group(6)) if m.group(6) else None
        ln = m.group(1) + a + m.group(3) + b + (m.group(5) + c if c else "")
    def conv(q):
        v = bits_value(q.group(2))
        return "%s(%s)" % (q.group(1), v) if v else q.group(0)
    ln = re.sub(r"\b(CLng|CInt|CSng|CDbl)\(" + LIT + r"\)", conv, ln)
    if re.search(r"\.(?:Line|Circle|PSet) \(", ln):
        # (graphics coordinates are Singles.  Not any procedure's hex argument: gh_zip's
        # table of machine code and Doom's angles are hex too, and came out as 4.18e+13!)
        ln = re.sub(r"&H[0-9A-F]{8}\b", lambda q: bits_value(q.group(0)) or q.group(0), ln)
    m = re.match(r"(\s*(?:\d+ )?)(\w+) (.+)$", ln)
    sig = sigs.get(m.group(2).lower()) if m else None
    if sig:
        parts = re.split(r",\s*(?![^()]*\))", m.group(3))
        for i, p in enumerate(parts):
            if i < len(sig) and sig[i]:
                v = bits_value(p)
                if v:
                    parts[i] = v
        ln = m.group(1) + m.group(2) + " " + ", ".join(parts)
    # and a Function's: c = VScale(a, &H40000000) for VScale(a, 2)
    for q in reversed(list(re.finditer(r"\b(\w+)\(", ln))):
        sig = sigs.get(q.group(1).lower())
        if not sig:
            continue
        depth, start, parts, i = 0, q.end(), [], q.end()
        while i < len(ln):
            c = ln[i]
            if c == '"':
                i = ln.find('"', i + 1)
                if i < 0:
                    break
            elif c == "(":
                depth += 1
            elif c == ")" and depth == 0:
                parts.append(ln[start:i])
                new = [(bits_value(x) or x) if k < len(sig) and sig[k] else x
                       for k, x in enumerate(parts)]
                new = [x if x == y else (" " if y.startswith(" ") else "") + x
                       for x, y in zip(new, parts)]
                ln = ln[:q.end()] + ",".join(new) + ln[i:]
                break
            elif c == ")":
                depth -= 1
            elif c == "," and depth == 0:
                parts.append(ln[start:i])
                start = i + 1
            i += 1
    return ln
