"""Write a decompiled VB6 project: .vbp, .frm/.frx, .bas (CRLF, as VB expects)."""
import os
import struct
import sys
import re

from . import render, apidb, frm, apiarity
from . import ir
from .ir import Var, Call
from .types import Types


def crlf(lines):
    return "\r\n".join(lines) + "\r\n"


# VB6 reads source files in the ANSI code page (Western: cp1252); string
# literals with other characters are ChrW$() (render.vb_literal).  Designer
# text and names come out of the EXE as bytes decoded as Latin-1: their
# 80h-9Fh, which cp1252 maps elsewhere or not at all, go back as the same
# byte.  (a target: frm_äö.frm written as UTF-8 was "File not
# found", and every Finnish string would have changed.)
ANSI = "cp1252"


def _ansi_errors(e):
    bad = e.object[e.start:e.end]
    if all(0x80 <= ord(c) <= 0x9F for c in bad):
        return bytes(ord(c) for c in bad), e.end
    return "?" * len(bad), e.end


import codecs                       # noqa: E402
codecs.register_error("vb6ansi", _ansi_errors)


def write_source(path, lines):
    with open(path, "w", encoding=ANSI, errors="vb6ansi", newline="") as f:
        f.write(crlf(lines))


def wrap_long(line, limit=900):
    """A statement longer than VB6 takes (1023 characters a line) split
    after commas, and after & / And / Or, outside string literals, with " _"
    continuations (VB allows 24 of them): VBDoom P_InitSwitchList's
    Array(...) of 120 names, a target's 1031-character concatenation."""
    if len(line) <= limit:
        return [line]
    indent = line[:len(line) - len(line.lstrip())] + "    "
    cuts, inq = [], False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif inq:
            continue
        elif ch == ",":
            cuts.append(i + 1)
        elif ch == "'":
            break                       # a comment: no continuation after it
        else:
            for op in (" & ", " And ", " Or "):
                if line.startswith(op, i):
                    cuts.append(i + len(op) - 1)
    out, start, last = [], 0, None
    for c in cuts:
        if c - start > limit and last is not None:
            out.append(line[start:last])
            start = last
        last = c
    out.append(line[start:])
    if len(out) > 25:
        return [line]                   # (cannot be split enough: VB will say so)
    return [out[0] + " _"] + [indent + x.strip() + " _" for x in out[1:-1]] + \
        [indent + out[-1].strip()] if len(out) > 1 else [line]


def unme(line):
    """Drop "Me." from a line of code, outside its string literals."""
    parts = line.split('"')
    parts[::2] = [re.sub(r"(?<![\w.])Me\.", "", x) for x in parts[::2]]
    return '"'.join(parts)


class ProjectWriter:
    def __init__(self, model, bodies):
        self.m = model
        self.bodies = bodies            # proc VA -> (Lifter, body)
        self.prj = model.prj
        self._arrays = None             # global_arrays(), made once
        self._module_of = {}            # global VA -> its module (module_of_global)
        self._calls = None              # declare_calls(), made once
        self._guessed = None            # module procedure VA -> module (owners.guess)

    # ------------------------------------------------------------------ facts
    def owner(self, p):
        if p.owner is not None:
            return p.owner
        t = getattr(p, "truth", None)
        if t:
            for o in self.prj.objects:
                if o.name == t[0]:
                    return o
        if self._guessed is None:
            # (a target: 940 module procedures in one module made 95,000
            # lines, and VB6 quits without a word over 65,534)
            from . import owners
            self._guessed = owners.guess(self.m, self.bodies)
        if p.va in self._guessed:
            return self._guessed[p.va]
        mods = [o for o in self.prj.objects if o.kind == "Module"]
        return mods[0] if mods else self.prj.objects[-1]

    def module_of_global(self, va):
        if va in self._module_of:
            return self._module_of[va]
        im = self.m.im
        out = None
        if va >= 0x7F000000:            # (a module's Static local: decompile.static_slots)
            out = next((o for o in self.prj.objects if o.index == (va - 0x7F000000) >> 16), None)
            self._module_of[va] = out
            return out
        for o in self.prj.objects:
            if o.kind != "Module" or not o.module_public:
                continue
            size = im.u16(o.public_bytes + 2) if im.valid(o.public_bytes) else 0
            if o.module_public <= va < o.module_public + max(size, 4):
                out = o
                break
        self._module_of[va] = out
        return out

    def uses(self):
        """Per object: globals and Declares its procedures use."""
        g, d = {}, {}
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            o = self.owner(lf.p)
            for _, ss, br in body:
                for s in ss:
                    for e in getattr(s, "exprs", lambda: ())():
                        for x in e.walk():
                            if isinstance(x, Var) and x.space == "global" and isinstance(x.off, int) \
                               and x.name != "":
                                g.setdefault(o.index, set()).add(x.off)
                            if isinstance(x, Call) and isinstance(x.raw, str) and "!" in x.raw:
                                d.setdefault(o.index, set()).add(x.raw)
                if br is not None:
                    for x in br.cond.walk():
                        if isinstance(x, Var) and x.space == "global" and isinstance(x.off, int) \
                           and x.name != "":
                            g.setdefault(o.index, set()).add(x.off)
                        if isinstance(x, Call) and isinstance(x.raw, str) and "!" in x.raw:
                            d.setdefault(o.index, set()).add(x.raw)     # If API(...) Then
        return g, d

    def global_types(self):
        ev, widths, dword_read, fp_access, fp_widths, stored = {}, {}, set(), set(), {}, {}
        counters = set()
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            ty = Types(lf, body, self.m)
            for k, ts in ty.ev.items():
                if k[0] == "global":
                    ev.setdefault(k[1], set()).update(ts)
            for k in ty.__dict__.get("counters", ()):
                if k[0] == "global":
                    counters.add(k[1])
            for k, ls in getattr(lf, "load_sizes", {}).items():
                if k[0] == "global" and (4, False) in ls:
                    dword_read.add(k[1])        # no Variant (Types.var_type)
            for k, zs in getattr(lf, "store_sizes", {}).items():
                if k[0] == "global":
                    stored.setdefault(k[1], set()).update(zs)
            for k, sz in lf.sizes.items():
                if isinstance(k, tuple) and k and k[0] == "global":
                    for size, fp in sz:
                        if fp and size == 8:
                            ev.setdefault(k[1], set()).add("Double")
                        elif fp and size == 4:
                            ev.setdefault(k[1], set()).add("Single")
                        elif size == 2:
                            ev.setdefault(k[1], set()).add("Integer")
                        if not fp:
                            widths.setdefault(k[1], set()).add(size)
                        else:
                            fp_access.add(k[1])
                            fp_widths.setdefault(k[1], set()).add(size)
        from .types import pick, INTEGRAL, FLOATS
        out = {}
        dbg = {int(x, 16) for x in os.environ.get("VB6DEC_GDBG", "").split(",") if x}
        for va, ts in ev.items():
            if va in dbg:
                print("GDBG %X ev %s dword_read %s fp_access %s widths %s" % (
                    va, sorted(ts), va in dword_read, va in fp_access, widths.get(va)),
                    file=sys.stderr)
            if va in counters and "String" in ts:
                ts = ts - {"String"} or {"Long"}
            if len(ts) > 1 and va in dword_read:
                t2 = ts - {"Variant"}           # (Types.var_type)
                if t2 & INTEGRAL and va not in fp_access:
                    t2 = t2 - FLOATS
                ts = t2 or ts
            t = pick(ts)
            w = widths.get(va)
            if t == "Double" and fp_widths.get(va) == {4}:
                # (what the FPU reads and writes as a dword is a Single, whatever it is computed
                # from: DarwinBots LFOR = LFOR - x / y; x / y is a Double in the evidence)
                t = "Single"
            if stored.get(va) and max(stored[va]) < 4:
                w = stored[va]      # (a dword load of a word in .data looks at ax: fast code)
            if t in ("Long", "Integer", "Byte", "Boolean") and w:
                # the machine code's access width is the declared size
                if w == {1}:
                    t = "Byte"
                elif w == {2}:
                    t = "Boolean" if "Boolean" in ts else "Integer"
            out[va] = t
        # a module variable used through its fields is of its UDT, whatever
        # its first field looks like (VBDoom D_Display's Static block)
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            for k, t in lf.udt.items():
                if k[0] == "global" and isinstance(k[1], int) and t and \
                        k not in lf.arrays and apidb.layout(t) is not None:
                    cur = out.get(k[1])
                    # (or a bigger one: a record starting with what a parameter takes, Killer0's
                    # three TMOVEs seen as the TMOVE given to ClearMove)
                    if cur is None or apidb.layout(cur) is None or \
                            apidb.layout(t)[1] > apidb.layout(cur)[1]:
                        out[k[1]] = t
        # a module String * n (LSet / Get / Put of it through its own
        # descriptor: a target global_F06120 As String * 200)
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            for k, n in getattr(lf, "fixed_strings", {}).items():
                if k[0] == "global" and isinstance(k[1], int) and n and \
                        out.get(k[1]) in (None, "String", "Variant") and k not in lf.arrays:
                    out[k[1]] = "String * %d" % n
        return out

    def global_arrays(self):
        """Array globals and what is known about their bounds and elements.
        Program-wide, so made once (writing a module does not change it)."""
        if self._arrays is not None:
            return self._arrays
        from .udt import scalar_global_arrays
        scalar = scalar_global_arrays(self.m)
        out = {}
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            for key, info in lf.arrays.items():
                if key[0] != "global" or not isinstance(key[1], int):
                    continue
                cur = out.setdefault(key[1], dict(info))
                for k, v in info.items():
                    if cur.get(k) in (None, False) and v not in (None, False):
                        cur[k] = v
                u = lf.udt.get(key)
                if u and key in scalar:
                    u = None                # (its descriptor names a scalar element)
                if u and (not cur.get("udt") or cur["udt"].startswith("T_") and
                          not u.startswith("T_")):
                    cur["udt"] = u          # its elements' UDT (an element passed ByRef)
        for key, n in self.m.__dict__.get("fixed_elems", {}).items():
            if isinstance(key[1], int) and key[1] in out:
                out[key[1]]["fixed"] = n
        self._arrays = out
        return out

    def member_types(self, o):
        """Form/class-level variables (Me+offset) used by the object's code."""
        from .types import pick, ORDER
        ev, exact = {}, {}
        for va, (lf, body) in self.bodies.items():
            if lf is None or self.owner(lf.p) is not o:
                continue
            ty = Types(lf, body, self.m)
            for k, t in ty.exact.items():
                if k[0] == "member":
                    exact.setdefault(k[1], t)   # passed ByRef As t (RaiseEvent ... Cancel)
            for k, t in lf.udt.items():
                if k[0] == "member" and k not in lf.arrays and t:
                    exact.setdefault(k[1], t)   # a record (its descriptor said: With theTray)
            for _, ss, br in body:
                for st in list(ss) + ([ir.ExprStmt(br.cond)] if br is not None else []):
                    for e in getattr(st, "exprs", lambda: ())():
                        for x in e.walk():
                            if isinstance(x, Var) and x.space == "member":
                                ev.setdefault(x.off, set())
                                t = ty.var_type(x.key())
                                if t:
                                    ev[x.off].add(t)
                                # (what the procedure only has as Object may say a class: Set m78 = a1
                                # with a1 As Picture, which is no type of a procedure's own to pick)
                                ev[x.off].update(y for y in ty.ev.get(x.key(), ())
                                                 if t == "Object" and y not in ORDER and
                                                 y.isidentifier())
        offs = sorted(ev)

        def choose(off, ts):
            # the next member within 16 bytes: no Variant, whatever a ReadProperty gave it (m78 As
            # Variant, Set m78 = PropBag.ReadProperty(...), is Empty before that, and Empty Is
            # Nothing is the 424 VB6 stopped on loading a target's UserControl)
            nxt = next((x for x in offs if x > off), None)
            if "Variant" in ts and nxt is not None and nxt - off < 16:
                ts = ts - {"Variant"}
            # one class among the object's assignments says more than Object
            named = [t for t in ts if t not in ORDER and not t.startswith("_")]
            if "Object" in ts and "Variant" not in ts and len(named) == 1 and \
                    apidb.layout(named[0]) is None:
                return named[0]
            return pick(ts) if ts else None
        return {off: exact.get(off) or choose(off, ts) for off, ts in ev.items()}

    def member_arrays(self, o):
        """Arrays among an object's member variables: {offset: array info}
        (bounds from the code's checks, element type from what they hold)."""
        from .types import pick
        out = {}
        for va, (lf, body) in self.bodies.items():
            if lf is None or self.owner(lf.p) is not o:
                continue
            ty = Types(lf, body, self.m)
            for key, info in lf.arrays.items():
                if key[0] != "member" or not isinstance(key[1], int):
                    continue
                cur = out.setdefault(key[1], dict(info))
                for k, v in info.items():
                    if cur.get(k) in (None, False) and v not in (None, False):
                        cur[k] = v
                # the elements' UDT lives in lf.udt (udt.array_element_types:
                # Form2's ReDim m40(n) of 264-byte records was `As Long`)
                u = lf.udt.get(key)
                if u and apidb.layout(u) is not None and (
                        not cur.get("udt") or cur["udt"].startswith("T_") and
                        not u.startswith("T_")):
                    cur["udt"] = u
                ts = ty.elem.get(key)
                if ts:
                    cur.setdefault("etypes", set()).update(ts)
        for info in out.values():
            if info.get("etypes") and not info.get("udt"):  # (a record type from ReDim wins)
                info["udt"] = pick(info["etypes"])   # (array_decl's element type)
        return out

    def withevents_of(self, o):
        """WithEvents variables of a form or class: {offset: (name, class)}."""
        out = {}
        for va, (lf, body) in self.bodies.items():
            if lf is None or self.owner(lf.p) is not o:
                continue
            for key, cls in lf.withevents.items():
                for c in o.controls:
                    if c.slot == key[1] and c.index == -1:
                        if cls in (None, "Object"):
                            # the event source's class, from its events IID: As Object can't have
                            # WithEvents ("Expected: identifier"; t34withevents As VB.CommandButton)
                            try:
                                co = self.m.control_class(c)[0]
                            except Exception:
                                co = None
                            n = getattr(co, "name", None)
                            cls = (n[1:] if n.startswith("_") else n) if n else cls
                        out[key[1]] = (c.name, cls)
        return out

    # ------------------------------------------------------------------ text
    def declare_calls(self):
        """Every call by name in the program: {raw: [(lf, args), ...]}, from
        one walk (rendering does not change the bodies)."""
        if self._calls is None:
            self._calls = {}
            for va, (lf, body) in self.bodies.items():
                if lf is None:
                    continue
                for _, ss, br in body:
                    for st in list(ss) + ([ir.ExprStmt(br.cond)] if br is not None else []):
                        for e in getattr(st, "exprs", lambda: ())():
                            for x in e.walk():
                                if isinstance(x, Call) and isinstance(x.raw, str):
                                    self._calls.setdefault(x.raw, []).append((lf, x.args))
                                    if getattr(x, "addr_args", None):
                                        self.__dict__.setdefault("_addr_args", {}).setdefault(
                                            id(x.args), x.addr_args)
        return self._calls

    def unknown_api_strings(self):
        """The String parameters of APIs no list knows: {raw: {position: "string" or "any"}}.  Where
        other calls pass a number or a pointer there (RegQueryValueExA's lpData: VarPtr(v60) and a
        String buffer), the parameter is As Any and each call says ByVal."""
        sites, known = {}, {}
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            for _, ss, br in body:
                for st in list(ss) + ([ir.ExprStmt(br.cond)] if br is not None else []):
                    for e in getattr(st, "exprs", lambda: ())():
                        for x in e.walk():
                            if isinstance(x, Call) and isinstance(x.raw, str) and "!" in x.raw:
                                if apidb.lookup(x.raw.split("!", 1)[1]) is None:
                                    sites.setdefault(x.raw, []).append(x)
                                else:
                                    known.setdefault(x.raw, []).append(x)
        # (a listed API's ByVal As String given an element too is declared As Any, declare_fixes: its
        # Strings say ByVal then, or the API gets a pointer to a pointer. lstrcpynA ByVal s, ... and b(0))
        for raw, calls in known.items():
            api = apidb.lookup(raw.split("!", 1)[1])
            for i, (byval, _, ptype) in enumerate(api.params):
                if byval and ptype == "String" and any(
                        i < len(c.args) and getattr(c.args[i], "byref_elem", False) for c in calls):
                    for c in calls:
                        a = c.args[i] if i < len(c.args) else None
                        if a is None or getattr(a, "byref_elem", False) or \
                                isinstance(a, ir.UnOp) and a.op == "ByVal":
                            continue
                        c.args[i] = ir.UnOp("ByVal", a)
        self.api_strings = {}
        for raw, calls in sites.items():
            strs = set()
            for c in calls:
                strs |= set(getattr(c, "ansi_args", ()))
            kinds = {}
            for i in strs:
                mixed = any(i not in getattr(c, "ansi_args", ()) and i < len(c.args) for c in calls)
                kinds[i] = "any" if mixed else "string"
                if mixed:
                    for c in calls:
                        a = c.args[i] if i < len(c.args) else None
                        if a is None or isinstance(a, ir.AddrOf) or \
                                isinstance(a, ir.UnOp) and a.op == "ByVal":
                            continue
                        if isinstance(a, ir.Const) and isinstance(a.value, int):
                            a = ir.Const(a.value, "Long&")
                        c.args[i] = ir.UnOp("ByVal", a)
            self.api_strings[raw] = kinds

    def lifters(self):
        return [lf for lf, body in self.bodies.values() if lf is not None]

    def pointer_vars(self, lf):
        """Keys of variables the procedure only ever assigns VarPtr / StrPtr /
        ObjPtr values: pointers, whatever they are passed to."""
        cache = self.__dict__.setdefault("_ptrvars", {})
        if lf.p.va not in cache:
            ptr, other = set(), set()
            for _, ss, br in self.bodies[lf.p.va][1]:
                for st in ss:
                    if isinstance(st, ir.Assign) and isinstance(st.dst, Var):
                        k = st.dst.key()
                        if isinstance(st.src, Call) and st.src.name in ("VarPtr", "StrPtr",
                                                                        "ObjPtr"):
                            ptr.add(k)
                        else:
                            other.add(k)
            cache[lf.p.va] = ptr - other
        return cache[lf.p.va]

    def declare_fixes(self, raws):
        """For the Declares of one object: {raw: (byval, strings)}.
        byval: parameter positions where some call passes a value, not a
        variable (a constant, an expression, a function result): such a
        parameter must be ByVal ... As Long for the call to compile.
        strings: for ByVal String parameters, positions where a call passes a
        number (StrPtr(s), 0, ...) -> "long", or an array element by reference
        -> "any".  WIN32API.TXT declares buffers As String; VB would turn a
        number into its digits.
        (Made when the object is written: rendering a procedure can add a UDT
        parameter to lf.udt, which byval looks at.)"""
        calls = self.declare_calls()
        out = {}
        for raw in raws:
            fix, sfix = out[raw] = (set(), {})
            api = apidb.lookup(raw.split("!", 1)[1]) if "!" in raw else None
            for lf, args in calls.get(raw, ()):
                for i, a in enumerate(args):
                    if api is None and isinstance(a, ir.AddrOf):
                        sfix.setdefault("byref", set()).add(i)    # (declare_text: As Any)
                        continue
                    if getattr(a, "rec_arg", False):
                        sfix.setdefault("anyrec", set()).add(i)
                        continue
                    pt = api.params[i] if api is not None and i < len(api.params) else None
                    if pt is not None and not pt[0] and apidb.layout(pt[2]) is not None and \
                            isinstance(a, Var) and a.space in ("local", "tmp", "reg", "stk") and \
                            not lf.udt.get(a.key()):
                        fix.add(i)      # a pointer value for a UDT: ByVal As Long (waveOutWrite)
                        continue
                    if pt is not None and not pt[0] and pt[2] != "Any" and \
                            isinstance(a, Var) and a.key() in self.pointer_vars(lf):
                        fix.add(i)      # a VarPtr kept in a variable (midiStreamProperty
                        continue        # hms, VarPtr(prop(0)) in unoptimised code)
                    if pt is not None and not pt[0] and apidb.layout(pt[2]) is not None:
                        # a ByRef UDT parameter given an element of an array of
                        # another type: the program declared it otherwise - As Any
                        # (VBDoom SetDIBColorTable hdc, 0, 256, rgbq(0) with Longs)
                        arr = a.arr if isinstance(a, ir.Index) else None
                        if isinstance(arr, Var) and lf.udt.get(arr.key()) != pt[2]:
                            sfix.setdefault("anyref", set()).add(i)
                            continue
                    field = isinstance(a, ir.BinOp) and a.op == "+" and \
                        isinstance(a.a, Var) and isinstance(a.b, ir.Const) and \
                        a.a.key() in lf.udt
                    byval_call = isinstance(a, ir.UnOp) and a.op == "ByVal"
                    if not field and not byval_call and \
                            not isinstance(a, (Var, ir.Deref, ir.AddrOf, ir.Index)) and \
                            i not in self.__dict__.get("_addr_args", {}).get(id(args), ()):
                        fix.add(i)          # (an array element is a variable)
                    if getattr(a, "byref_elem", False):
                        sfix[i] = "any"
                    elif isinstance(a, ir.Const) or \
                            (isinstance(a, Call) and a.name in ("StrPtr", "VarPtr", "ObjPtr")) or \
                            (isinstance(a, ir.BinOp) and a.op in ("+", "-", "*")):
                        sfix.setdefault(i, "long")
        return out

    def declare_text(self, raw, scope, fixes):
        lib, fn = raw.split("!", 1)
        api = apidb.lookup(fn)
        if api is not None:
            fix, sfix = fixes
            parts = []
            byref = {i for lf, _ in [(l, 0) for l in self.lifters()]
                     for (fn2, i) in getattr(lf, "api_byref", ()) if fn2 == fn}
            for i, (byval, pname, ptype) in enumerate(api.params):
                if i in sfix.get("anyrec", ()):
                    parts.append("%s As Any" % pname)
                elif byval and i in byref:
                    parts.append("%s As %s" % (pname, ptype))     # a variable by reference
                elif not byval and i in sfix.get("anyref", ()) and i not in fix:
                    parts.append("%s As Any" % pname)
                elif byval and ptype == "String" and sfix.get(i) == "any":
                    parts.append("%s As Any" % pname)
                elif byval and ptype == "String" and sfix.get(i) == "long":
                    parts.append("ByVal %s As Long" % pname)
                elif not byval and i in fix and ptype != "Any":
                    # (As Any stays: calls passing a value say ByVal themselves,
                    # and the ones passing a variable or element need its address)
                    parts.append("ByVal %s As Long" % pname)
                else:
                    parts.append("%s%s As %s" % ("ByVal " if byval else "", pname, ptype))
            text = '%s Declare %s %s Lib "%s"%s (%s)%s' % (
                scope, api.kind, api.name, api.lib,
                ' Alias "%s"' % api.alias if api.alias and api.alias != api.name else "",
                ", ".join(parts), " As %s" % api.ret if api.ret else "")
            return text
        n = self.m.declare_argc.get(fn)
        if n is None:
            n = apiarity.arity(lib, fn) or 0
        wide = getattr(self.m, "declare_wide", {}).get(fn, {})
        byref = fixes[1].get("byref", set()) if fixes else set()
        strings = getattr(self, "api_strings", {}).get(raw, {})
        parts, i = [], 0
        while i < n:
            if i in wide:
                parts.append("ByVal a%d As %s" % (len(parts) + 1, wide[i]))
                i += 2                      # an 8-byte value: two dwords
                continue
            if strings.get(i) == "string" and i not in byref:
                parts.append("ByVal a%d As String" % (len(parts) + 1))
                i += 1
                continue
            if i in byref or i in strings:
                parts.append("a%d As Any" % (len(parts) + 1))   # a variable by address
                i += 1
                continue
            parts.append("ByVal a%d As Long" % (len(parts) + 1))
            i += 1
        args = ", ".join(parts)
        return '%s Declare Function %s Lib "%s" (%s) As Long' % (scope, fn, lib, args)

    def object_code(self, o, gtypes, guse, duse):
        out = []
        priv = "Private" if o.kind != "Module" else "Public"
        # Win32 Types: Public in the first module, else Private in each form
        home = getattr(self, "types_home", None)
        if getattr(self, "types_text", None) and (home is o or home is None):
            consts = []
            for body in self.types_text:
                consts += [c for c in apidb.bound_consts(body) if c not in consts]
            for name, value in consts:
                out.append("%s Const %s = %d" % ("Public" if o.kind == "Module" else "Private",
                                                 name, value))
            if consts:
                out.append("")
            for body in self.types_text:
                out.append(("Public " if o.kind == "Module" else "Private ") + body[0])
                out.extend("    " + ln.strip() for ln in body[1:-1])
                out.append("End Type")
                out.append("")
        raws = sorted(duse.get(o.index, ()))
        fixes = self.declare_fixes(raws)
        for raw in raws:
            out.append(self.declare_text(raw, "Private" if o.kind != "Module" else "Public",
                                         fixes[raw]))
        if o.kind == "Module":
            arrays = self.global_arrays()
            mine = sorted(va for va in set().union(*guse.values()) if self.module_of_global(va) is o) \
                if guse else []
            for va in sorted(set(mine) | {a for a in arrays if self.module_of_global(a) is o}):
                users = {k for k, s in guse.items() if va in s}
                scope = "Public" if users - {o.index} else "Private"
                if va in arrays:
                    from .types import array_decl
                    out.append("%s %s" % (scope, array_decl("global_%X" % va, arrays[va])))
                    continue
                t = gtypes.get(va) or "Long"
                if va in self.m.__dict__.get("mixed_globals", ()):
                    t = "Object"            # Set g = Printer and Set g = Screen
                if va in self.m.__dict__.get("variant_globals", ()):
                    t = "Variant"           # __vbaVarSetObj
                out.append("%s global_%X As %s" % (scope, va, t))
        if o.kind != "Module":
            mtypes = self.member_types(o)
            om = self.m.members.get(o.index)
            for e in (om.events if om is not None else []):
                ps = ", ".join("%s%s As %s" % ("" if prm.byref else "ByVal ", prm.name,
                                               render.member_type(self.m, prm.type, prm.cls))
                               for prm in e.params)
                out.append("Public Event %s(%s)" % (e.name, ps))
            impl = self.m.implements_of(o)
            for name in sorted(impl):
                out.insert(0, "Implements %s" % name)   # before any declaration
            public = {v.offset: v for v in om.vars if v.name and v.name not in impl} \
                if om is not None else {}
            for off, v in sorted(public.items()):
                t = render.member_type(self.m, v.type, v.cls) if v.type else mtypes.get(off)
                out.append("Public %s As %s" % (v.name, t or "Variant"))
            we = self.withevents_of(o)
            for off, (name, cls) in sorted(we.items()):
                out.append("Private WithEvents %s As %s" % (name, cls or "Object"))
            arrays = self.member_arrays(o)
            # a member UDT: the members inside its bytes are its fields
            structs = {}
            for off, t in sorted(mtypes.items()):
                lay = apidb.layout(t) if t and off not in arrays else None
                if lay is not None and lay[1] > 4:
                    structs[off] = (t, lay[1])
            self.m.__dict__.setdefault("member_structs", {})[o.index] = structs
            inside = {off for off in mtypes for b, (t, n) in structs.items() if b < off < b + n}
            for off, t in sorted(mtypes.items()):
                if off in public or off in we or off in inside:
                    continue
                if off == 0x10 and o.kind == "UserControl":
                    continue                # (the UserControl object, not a variable)
                if off in arrays:
                    from .types import array_decl
                    out.append("Private " + array_decl("m%X" % off, arrays[off]))
                    continue
                out.append("Private m%X As %s" % (off, t or "Long"))
        if out:
            out.append("")
        procs = [p for p in self.m.procs.values() if self.owner(p) is o]
        for p in procs:
            lf, body = self.bodies.get(p.va, (None, None))
            if lf is None:
                out.append("' vb6dec: %s could not be decompiled: %s" % (p.name, body))
                out.append("")
                continue
            try:
                for ln in render.proc_source(self.m, lf, body).split("\n"):
                    out.extend(wrap_long(ln))
            except Exception as e:      # keep going; report in the file
                out.append("' vb6dec: %s could not be rendered: %s" % (p.name, e))
            out.append("")
        return out

    def api_types(self, duse):
        """WIN32API.TXT Types the program uses (struct variables, Declare
        parameters), with the Types they contain, in dependency order."""
        want = []
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            # synthetic T_ types too: they can be shared (a struct passed ByRef
            # to another module's procedure), so they are declared once
            want += [t for t in lf.udt.values() if t]
        for raws in duse.values():
            # sorted: a set's order changes from run to run (string hashes),
            # and with it which Type is declared first (VBDoom: RGBQUAD from
            # SetDIBColorTable, or BITMAPINFOHEADER from CreateDIBSection)
            for raw in sorted(raws):
                api = apidb.lookup(raw.split("!", 1)[1])
                if api is not None:
                    want += [t for _, _, t in api.params if apidb.layout(t) is not None]
        out = []

        def add(t):
            body = apidb.udt(t)
            if body is None or any(b[0] == body[0] for b in out):
                return
            lay = apidb.layout(t)
            for _, _, ft in (lay[0] if lay else ()):
                if ft not in apidb.SIZES:
                    add(ft)
            out.append(body)
        for t in want:
            add(t)
        return out

    TYPE_NAME = re.compile(r"\bT_[0-9A-F]+_\d+\b")

    def merge_types(self, types):
        """Synthetic Types with the same body are one Type: VB wants the very
        type of a ByRef UDT argument, and VBDoom's T_10_1 (player_arrow's
        elements), T_10_34 (AM_drawLineCharacter's parameter) and T_10_3 (a
        local) are all mline_t.  Returns the bodies kept; self.type_alias maps
        the others to them (applied to every line written, by retype)."""
        alias = self.type_alias = {}
        while True:
            seen, changed = {}, False
            for body in types:
                name = body[0].split()[1]
                if name in alias or not self.TYPE_NAME.fullmatch(name):
                    continue
                key = tuple(self.retype(ln).strip() for ln in body[1:])
                if key in seen:
                    alias[name] = seen[key]
                    changed = True
                else:
                    seen[key] = name
            if not changed:
                break
        # one Type seen through fewer of its fields: every field of A is a
        # field of B, at the same offset, and both are as big - A is B
        # (VBDoom netcmds' elements, T_18_38: f10 and f14 between padding, and
        # G_BuildTiccmd's ticcmd_t parameter, T_18_37: all six Longs)
        live = [b for b in types if b[0].split()[1] not in alias and
                self.TYPE_NAME.fullmatch(b[0].split()[1])]
        info = {}
        for b in live:
            name = b[0].split()[1]
            lay = apidb.layout(name)
            if lay is None:
                continue
            lines = {self.retype(ln).strip() for ln in b[1:-1]}
            real = {ln for ln in lines if not ln.startswith("pad")}
            info[name] = (lay[1], lines, real)
        for a, (size, lines, real) in info.items():
            if not real:
                continue
            into = [b for b, (bs, bl, br) in info.items() if b != a and bs == size and
                    real < br and b not in alias]
            richest = [b for b in into if not any(info[b][2] < info[c][2] for c in into)]
            if len(richest) == 1 or len({frozenset(info[b][2]) for b in richest}) == 1:
                alias[a] = richest[0]
        # the same fields where one guessed a Long by the gap to the next field and the other
        # knows the narrower type: the narrower (Mutables.Mutations As Boolean, then 2 bytes of
        # padding: T_106_13.f0 As Long, T_106_12.f0 As Boolean, and ByRef parameters between them)
        def fields_of(real):
            out = {}
            for ln in real:
                w = ln.split()
                out[w[0]] = " ".join(w[1:])
            return out
        for a, (size, lines, real) in info.items():
            if a in alias:
                continue
            fa = fields_of(real)
            for b, (bs, bl, br) in info.items():
                if b == a or b in alias or bs != size:
                    continue
                fb = fields_of(br)
                diff = [k for k in fa if fa[k] != fb.get(k)]
                if diff and set(fa) == set(fb) and \
                        all(fa[k] == "As Long" and fb[k] in ("As Integer", "As Boolean", "As Byte")
                            for k in diff):
                    alias[a] = b
                    break
        # a synthetic Type with exactly the fields of a real record, whose types
        # came only from guesses: the record (a target T_30_4 = TRec13, f10 As
        # String where the record has String * 1: Put to List2.idx failed)
        def field_names(body):
            return {ln.split()[0] for ln in body[1:-1] if ln.strip() and
                    not ln.strip().startswith("pad")}
        reals = [b for b in types if not self.TYPE_NAME.fullmatch(b[0].split()[1]) and
                 apidb.layout(b[0].split()[1]) is not None]
        strs = self.m.__dict__.get("synth_strings", {})

        def string_offsets(name):
            return {o for o, n, ft in apidb.layout(name)[0] if ft == "String"}

        for b in live:
            a = b[0].split()[1]
            if a in alias or a not in info or len(field_names(b)) < 4:
                continue
            # (a record's String where nothing shows this Type holding one is another record:
            # VBDoom's five Longs have hu_textline_t's layout with fC As String; an array of them
            # filled with numbers fails at run time)
            into = [r[0].split()[1] for r in reals if field_names(r) == field_names(b) and
                    apidb.layout(r[0].split()[1])[1] == info[a][0] and
                    (not string_offsets(r[0].split()[1]) or
                     string_offsets(r[0].split()[1]) & set(strs.get(a, ())))]
            if len(into) == 1:
                alias[a] = into[0]
        # field paths are rendered after this: through an alias's own layout
        # an access could land in its padding (players(i).cmd.pad1(1), a
        # field T_18_10 had not seen), so every alias takes its Type's body
        for a in alias:
            b = self.retype(a)
            body = apidb.udt(b)
            if body is not None:
                apidb.add_type(a, ["Type %s" % a] + list(body[1:]))
        kept = [[self.retype(ln) for ln in body] for body in types
                if body[0].split()[1] not in alias]
        # each Type after the ones its fields use (a merge can point an early
        # Type at a later one: "Forward reference to user-defined type")
        names = {b[0].split()[1]: b for b in kept}
        order, done = [], set()

        def place(n, stack=()):
            if n in done or n in stack:
                return
            for ln in names[n][1:-1]:
                m = re.search(r"\bAs\s+(\w+)", ln)
                if m and m.group(1) in names:
                    place(m.group(1), stack + (n,))
            done.add(n)
            order.append(names[n])
        for b in kept:
            place(b[0].split()[1])
        return order

    def retype(self, line):
        alias = getattr(self, "type_alias", None)
        if not alias:
            return line

        def canon(m):
            n = m.group(0)
            while n in alias:
                n = alias[n]
            return n
        return self.TYPE_NAME.sub(canon, line)

    def private_procs(self):
        """Module procedures no other module calls or takes the address of: Private.  Those sharing
        their name with another module's must have been (VB finds an unqualified call to a name two
        modules make Public ambiguous: VBDoom InitTables in m_fixed, p_enemy and hu_stuff, of which
        only m_fixed's is Public), and the rest may be, and then no name outside clashes with theirs
        (a Public Sub Constants hides VBA's module of that name).  Not Sub Main."""
        def name(p):
            return (p.truth[1] if getattr(p, "truth", None) else p.name or "").lower()
        by_name = {}
        for p in self.m.procs.values():
            o = self.owner(p)
            if o.kind == "Module" and getattr(p, "member", None) is None:
                by_name.setdefault(name(p), []).append(p)
        clash = {p.va for ps in by_name.values() for p in ps if name(p) != "main"}
        if not clash:
            return set()
        users = {}                      # procedure VA -> objects that call or reference it
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            o = self.owner(lf.p).index
            for _, ss, br in body:
                es = [e for st in ss for e in getattr(st, "exprs", lambda: ())()]
                if br is not None:
                    es.append(br.cond)
                for e in es:
                    for x in e.walk():
                        if isinstance(x, Call) and isinstance(x.raw, int):
                            users.setdefault(x.raw, set()).add(o)
                        elif isinstance(x, ir.Const) and isinstance(x.value, int) and \
                                (x.value & 0xffffffff) in clash:
                            users.setdefault(x.value & 0xffffffff, set()).add(o)
        return {va for va in clash
                if users.get(va, set()) <= {self.owner(self.m.procs[va]).index}}

    def qualify_calls(self):
        """Module.Proc where the bare name means something else in the caller: a menu or control of
        the form (DarwinBots DrawSpiral_Click: Obstacles.DrawSpiral, "Invalid use of property"),
        the object's own procedure, or a Public one of another module."""
        def name(p):
            return (p.truth[1] if getattr(p, "truth", None) else p.name or "").lower()
        names = {}
        for p in self.m.procs.values():
            names.setdefault(self.owner(p).index, set()).add(name(p))
        modules = [o for o in self.prj.objects if o.kind == "Module"]
        controls = {}
        for va, (lf, body) in self.bodies.items():
            if lf is None:
                continue
            own = self.owner(lf.p)
            for _, ss, br in body:
                es = [e for st in ss for e in getattr(st, "exprs", lambda: ())()]
                if br is not None:
                    es.append(br.cond)
                for e in es:
                    for x in e.walk():
                        if not (isinstance(x, Call) and isinstance(x.raw, int)) or x.obj is not None:
                            continue
                        p = self.m.procs.get(x.raw)
                        if p is None or "." in x.name:
                            continue
                        to = self.owner(p)
                        low = x.name.lower()
                        if to is own or to.kind != "Module" or low != name(p):
                            continue
                        if own.index not in controls:
                            controls[own.index] = {(c.name or "").lower()
                                                   for c in self.m.all_controls(own)}
                        if low in names.get(own.index, ()) or low in controls[own.index] or \
                                sum(1 for o in modules if low in names.get(o.index, ())) > 1:
                            x.name = "%s.%s" % (to.name, x.name)

    def write(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        self.m.ocx_bags = frm.ocx_results(self.m)
        self.m.private_procs = self.private_procs()
        self.qualify_calls()
        self.unknown_api_strings()
        gtypes = self.global_types()
        guse, duse = self.uses()
        types = self.merge_types(self.api_types(duse))
        mods = [o for o in self.prj.objects if o.kind == "Module"]
        self.types_home = mods[0] if mods else None
        self.types_text = types
        files = []
        for o in self.prj.objects:
            code = [self.retype(ln) for ln in self.object_code(o, gtypes, guse, duse)]
            from .project import OBJ_COMPARE_TEXT
            if o.type & OBJ_COMPARE_TEXT:
                code.insert(0, "Option Compare Text")   # (the runtime's Text functions came from it)
            if o.kind == "Form":
                fn = o.name + ".frm"
                text, frx = frm.decode_form(self.m, o, o.name + ".frx")
                lines = ["VERSION 5.00"]
                lines += (text or "Begin VB.Form %s\nEnd" % o.name).split("\n")
                lines += ['Attribute VB_Name = "%s"' % o.name,
                          "Attribute VB_GlobalNameSpace = False",
                          "Attribute VB_Creatable = False",
                          "Attribute VB_PredeclaredId = True",
                          "Attribute VB_Exposed = False"]
                lines += code
                write_source(os.path.join(outdir, fn), lines)
                if frx:
                    open(os.path.join(outdir, o.name + ".frx"), "wb").write(frx)
                files.append(("Form", fn, o.name))
            elif o.kind in ("UserControl", "PropertyPage", "UserDocument"):
                ext, res = {"UserControl": (".ctl", ".ctx"), "PropertyPage": (".pag", ".pgx"),
                            "UserDocument": (".dob", ".dox")}[o.kind]
                fn = o.name + ext
                # Me is the control's public face; its controls and UserControl's members are
                # named bare (Me.lbl.Caption in a Property Let crashed VB6 at compile time)
                code = [unme(ln) for ln in code]
                text, frx = frm.decode_form(self.m, o, o.name + res)
                lines = ["VERSION 5.00"]
                lines += (text or "Begin VB.%s %s\nEnd" % (o.kind, o.name)).split("\n")
                lines += ['Attribute VB_Name = "%s"' % o.name,
                          "Attribute VB_GlobalNameSpace = False",
                          "Attribute VB_Creatable = True",
                          "Attribute VB_PredeclaredId = False",
                          "Attribute VB_Exposed = False"]
                lines += code
                write_source(os.path.join(outdir, fn), lines)
                if frx:
                    open(os.path.join(outdir, o.name + res), "wb").write(frx)
                files.append((o.kind, fn, o.name))
            elif o.kind == "Module":
                fn = o.name + ".bas"
                lines = ['Attribute VB_Name = "%s"' % o.name] + code
                write_source(os.path.join(outdir, fn), lines)
                files.append(("Module", fn, o.name))
            else:
                fn = o.name + ".cls"
                lines = ["VERSION 1.0 CLASS", "BEGIN", "  MultiUse = -1  'True", "END",
                         'Attribute VB_Name = "%s"' % o.name,
                         "Attribute VB_GlobalNameSpace = False",
                         "Attribute VB_Creatable = True",
                         "Attribute VB_PredeclaredId = False",
                         "Attribute VB_Exposed = False"] + code
                write_source(os.path.join(outdir, fn), lines)
                files.append(("Class", fn, o.name))
        from . import recfit
        recfit.fit(outdir, files)
        self.write_vbp(outdir, files)
        from . import arrbase
        arrbase.fix(outdir, files)
        from . import floatlit
        floatlit.fix(outdir, files)
        from . import byrefwrap
        byrefwrap.fix(outdir)
        return files

    def library_refs(self, outdir, files):
        """Reference= / Object= lines for the type libraries (beyond VB's own)
        whose types the written code uses: Scripting's Dictionary (gh_json),
        an OCX's controls."""
        import re
        from .typelib import Registry
        from . import msfiles
        text = ""
        for kind, fn, name in files:
            try:
                text += open(os.path.join(outdir, fn), encoding="latin-1").read() + "\n"
            except OSError:
                pass
        words = set(re.findall(r"(?:\bAs\s+(?:New\s+)?|\bNew\s+|\bBegin\s+)([\w.]+)", text))
        # a bare name the project or VB itself defines is not a library's
        # (a target: RECT is its own Type, not MMEFXE.OCX's; Frame and Label
        # are VB's, not MSCHRT20's)
        own = set(re.findall(r"^\s*(?:Public\s+|Private\s+)?(?:Type|Enum)\s+(\w+)", text, re.M))
        own |= {name for kind, fn, name in files}
        libs, builtin = [], set()
        for lib in Registry.libs:
            path = getattr(lib, "name", None) or ""
            names = {t.name for t in lib.types if t.name}
            if msfiles.is_vb_own(path):
                builtin |= names            # VB6.OLB, MSVBVM60, stdole: always there
            else:
                libs.append((lib, names, getattr(lib, "lib_name", None)))
        # a library named in a qualified name (Begin MSComctlLib.ListView) owns
        # the bare names it defines: COMCTL32.OCX (Windows Common Controls 5)
        # defines IListItem too, and listed first it would take it over
        named = [n for lib, n, ln in libs if ln and any(w.startswith(ln + ".") for w in words)]
        out = []
        claimed = set()         # (a bare name is the first library's: WSHOM.OCX has Scripting's IFile too)
        for lib, names, ln in libs:
            qualified = ln and any(w.startswith(ln + ".") for w in words)
            bare = {w for w in words & names if w not in own and w not in builtin and w not in claimed}
            if not qualified:
                bare = {w for w in bare if not any(w in n for n in named)}
            if not qualified and not bare:
                continue
            claimed |= bare
            fn = os.path.basename(lib.name)
            ver = "%d.%d" % (getattr(lib, "lib_major", 1), getattr(lib, "lib_minor", 0))
            # (Object= is for a library of controls; WSHOM.OCX is an .ocx with none)
            if fn.lower().endswith(".ocx") and any(t.kind == 5 and t.flags & 0x20 for t in lib.types):
                out.append("Object=%s#%s#0; %s" % (lib.libid, ver, fn))
            else:
                out.append("Reference=*\\G%s#%s#0#..\\..\\WINDOWS\\SYSTEM\\%s#%s" % (
                    lib.libid, ver, fn, getattr(lib, "lib_doc", None) or ln or fn))
        return out

    def write_vbp(self, outdir, files):
        prj = self.prj
        lines = ["Type=Exe"]
        # (a UserControl before the forms that hold it: the forms load in this order)
        for kind, fn, name in files:
            if kind in ("UserControl", "PropertyPage", "UserDocument"):
                lines.append("%s=%s" % (kind, fn))
        for kind, fn, name in files:
            if kind == "Form":
                lines.append("Form=%s" % fn)
        lines.append("Reference=*\\G{00020430-0000-0000-C000-000000000046}#2.0#0#"
                     "..\\..\\WINDOWS\\SYSTEM\\STDOLE2.TLB#OLE Automation")
        lines += self.library_refs(outdir, files)
        for kind, fn, name in files:
            if kind == "Module":
                lines.append("Module=%s; %s" % (name, fn))
            elif kind == "Class":
                lines.append("Class=%s; %s" % (name, fn))
        from . import resources
        res = resources.project_resources(self.m.im)
        if res:
            # the project's own .res (LoadResData / LoadResString), as RC wrote it
            rn = (prj.project_name or prj.name) + ".res"
            open(os.path.join(outdir, rn), "wb").write(resources.res_file(res))
            lines.append('ResFile32="%s"' % rn)
        opts = {"OverflowCheck": 0, "FlPointCheck": 0, "FDIVCheck": 0, "BoundsCheck": 0}
        opts.update(resources.compile_options(self.m.im))
        forms = [name for kind, fn, name in files if kind == "Form"]
        startup = None
        if prj.sub_main:
            lines.append('Startup="Sub Main"')
        elif forms:
            startup = self.startup_form(forms)
            lines.append('Startup="%s"' % startup)
        icon = self.icon_form(outdir, files, startup)
        if icon and icon != startup:
            lines.append('IconForm="%s"' % icon)
        if prj.help_file:
            lines.append('HelpFile="%s"' % prj.help_file)
        # (exe_base is VB's own word for it; the name the file has now may be anything)
        exe = prj.exe_base or prj.title or prj.name
        # App.Title, and the caption of the hidden ThunderRT6Main window
        if prj.title and prj.title != (prj.project_name or prj.name):
            lines.append('Title="%s"' % prj.title)
        lines += ['Command32=""', 'Name="%s"' % (prj.project_name or prj.name),
                  'HelpContextID="0"', 'CompatibleMode="0"']
        lines += self.version_lines()
        from . import detect
        speed = detect.options(self.m.im).get("optimisation")
        lines += ["CompilationType=0",
                  "OptimizationType=%d" % {"Small code": 1, "no optimisation": 2}.get(speed, 0),
                  "FavorPentiumPro(tm)=0", "CodeViewDebugInfo=0", "NoAliasing=0"]
        lines += ["%s=%d" % (k, opts[k])
                  for k in ("BoundsCheck", "OverflowCheck", "FlPointCheck", "FDIVCheck")]
        lines += ["UnroundedFP=0", "StartMode=0", "Unattended=%d" % bool(prj.thread_flags & 4),
                  "Retained=%d" % bool(prj.thread_flags & 0x10),
                  "ThreadPerObject=0", "MaxNumberOfThreads=1", 'ExeName32="%s.exe"' % exe]
        name = (prj.project_name or prj.name) + ".vbp"
        write_source(os.path.join(outdir, name), lines)

    def version_lines(self):
        """The Make tab: App.Major and the like, and what Explorer shows.  A key left out is not left empty: VB fills in
        the Company Name of the machine that builds it, so each is written, the way the IDE does."""
        from . import resources
        ver = resources.version_info(self.m.im) or {"major": 1, "minor": 0, "revision": 0, "strings": {}}
        # (VB takes up to 9999 of each, and a string as it stands between the outer quotes: no doubling)
        lines = ["MajorVer=%d" % min(ver["major"], 9999), "MinorVer=%d" % min(ver["minor"], 9999),
                 "RevisionVer=%d" % min(ver["revision"], 9999), "AutoIncrementVer=0", "ServerSupportFiles=0"]
        for key, name in (("VersionComments", "Comments"), ("VersionCompanyName", "CompanyName"),
                          ("VersionFileDescription", "FileDescription"), ("VersionLegalCopyright", "LegalCopyright"),
                          ("VersionLegalTrademarks", "LegalTrademarks"), ("VersionProductName", "ProductName")):
            lines.append('%s="%s"' % (key, re.sub(r"[\r\n\t]+", " ", ver["strings"].get(name, ""))))
        return lines

    def icon_form(self, outdir, files, startup):
        """The form the EXE's icon came from: Project Properties' Icon, which is the startup form's unless said."""
        from . import resources
        images = resources.icon_images(self.m.im)
        names = [(fn, name) for kind, fn, name in files if kind == "Form"]
        names.sort(key=lambda t: t[1] != startup)
        for fn, name in names if images else ():
            try:
                with open(os.path.join(outdir, fn), encoding="cp1252", newline="") as f:
                    text = f.read()
            except OSError:
                continue
            # (three spaces in: the form's own property, not a control's)
            m = re.search(r'^   Icon\s*=\s*"([^"]+)":([0-9A-Fa-f]+)', text, re.M)
            if not m:
                continue
            try:
                with open(os.path.join(outdir, m.group(1)), "rb") as f:
                    frx = f.read()
            except OSError:
                continue
            off = int(m.group(2), 16)
            if len(frx) >= off + 4:
                blob = frx[off + 4:off + 4 + struct.unpack_from("<I", frx, off)[0]]
                if all(i in blob for i in images):
                    return name
        return None

    def startup_form(self, forms):
        """The first form in the GUI table is the one VB loads at startup."""
        im, prj = self.m.im, self.prj
        from .project import GUI_STARTUP
        for o in prj.objects:
            # (the flag VB's own loader goes by; the first entry is often another: a UserControl
            # in t25usercontrol, a form in PTAsmblr)
            if prj.gui_flags().get(o.index, 0) & GUI_STARTUP and o.name in forms:
                return o.name
        if im.valid(prj.gui_table) and prj.nforms:
            idx = im.u32(prj.gui_table + 0x24)
            for o in prj.objects:
                if o.index == idx and o.name in forms:
                    return o.name
        # (t25usercontrol: a UserControl comes first, and Startup="uc1" crashes VB6 as it loads)
        if im.valid(prj.gui_table):
            for i in range(prj.nforms):
                idx = im.u32(prj.gui_table + 0x50 * i + 0x24)
                for o in prj.objects:
                    if o.index == idx and o.name in forms:
                        return o.name
        return forms[0]
