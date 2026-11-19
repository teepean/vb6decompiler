"""MSVBVM60 runtime functions: calling convention and VB meaning.

Arguments are listed in C order (a[0] is the first parameter, i.e. the
last value pushed).  Register arguments come from ecx/edx.  A spec's
handler returns the VB expression for the call's value; "stmt" handlers
return statements instead.  Entries marked "verify" were inferred and
still need an oracle test.
"""
from .ir import (Const, Str, Var, AddrOf, Deref, BinOp, UnOp, Call, Assign,
                 ExprStmt, Free, OnError, Raw)


class Spec:
    def __init__(self, nargs=0, regs=(), ret=None, f=None, kind="expr", cdecl=False,
                 fpu_in=0, fpu_out=False, varargs=False, pure=True, plain=()):
        self.nargs, self.regs, self.ret, self.f = nargs, regs, ret, f
        self.plain = plain          # argument positions that are not Variants
        self.kind, self.cdecl, self.fpu_in, self.fpu_out = kind, cdecl, fpu_in, fpu_out
        self.varargs, self.pure = varargs, pure


def line_input_str(a):
    """Line Input #f, s: s is a String (a Function's own name too: mapkey came out As Long)."""
    e = stmt("Line Input #{1}, {0}", [deref(a[0]), a[1]])
    e.string_target = True
    return e


def arr(p):
    """An array argument: the variable itself, however it was passed."""
    if isinstance(p, AddrOf):
        return p.e
    return p


def dim(d):
    """The dimension argument, dropped when it is the default 1."""
    return [] if isinstance(d, Const) and d.value == 1 else [d]


def deref(p):
    """Value behind a pointer argument (&x -> x)."""
    if isinstance(p, AddrOf):
        return p.e
    return Deref(p)


def of_variant(e):
    """A Variant's value read as an object (__vbaUnkVar): typing leaves e a Variant."""
    import copy
    e = copy.copy(e)
    e.of_variant = True
    return e


def paramarray_call(name, args):
    """A runtime function whose last argument is a ParamArray (built like a
    user procedure's: simplify.fold_paramarrays puts the items back)."""
    c = Call(name, args)
    c.paramarray_last = True
    return c


def single_bits(x):
    """A Single passed as its dword: the literal for a constant."""
    import struct
    if isinstance(x, Const) and isinstance(x.value, int):
        return Const(struct.unpack("<f", struct.pack("<I", x.value & 0xffffffff))[0], "Single")
    return x


def r4_for_check(counter, end, step):
    """The loop condition of a Single For: counter <= end going up, >= going
    down (one of the two when the step is a constant)."""
    end, step = single_bits(end), single_bits(step)
    up = BinOp("<=", counter, end, "Boolean")
    down = BinOp(">=", counter, end, "Boolean")
    if isinstance(step, Const) and isinstance(step.value, (int, float)):
        return up if step.value >= 0 else down
    return Call("IIf", [BinOp(">=", step, Const(0), "Boolean"), up, down], vbtype="Boolean")


def format_call(name, a):
    return Call(name, trailing_defaults([deref(a[0])] + list(a[1:5]),
                                        {1: -1, 2: -2, 3: -2, 4: -2}))


def double_bits(lo, hi):
    """A Double passed as two dwords: the literal when both are constants."""
    import struct
    if isinstance(lo, Const) and isinstance(hi, Const) and isinstance(lo.value, int) and \
            isinstance(hi.value, int):
        raw = struct.pack("<II", lo.value & 0xffffffff, hi.value & 0xffffffff)
        return Const(struct.unpack("<d", raw)[0], "Double")
    return lo


def stmt(fmt, args):
    """A statement with its own syntax; fmt places the rendered arguments."""
    c = Call(fmt.split()[0], args)
    c.stmt_fmt = fmt
    return c


OPEN_MODES = {1: "Input", 2: "Output", 4: "Random", 8: "Append", 0x20: "Binary"}


OPEN_ACCESS = {0x100: " Access Read", 0x200: " Access Write", 0x300: " Access Read Write"}


# the DOS share modes, deny read+write / write / read / none
OPEN_LOCK = {0x1000: " Lock Read Write", 0x2000: " Lock Write", 0x3000: " Lock Read",
             0x4000: " Shared"}


def resume_call(arg):
    # __vbaResume(0) + a jump: Resume label; -1: Resume Next; -2: Resume
    # (the failing statement again) - a target's handlers printed "Resume -1"
    if isinstance(arg, Const) and arg.value in (-1, 0xFFFFFFFF):
        return stmt("Resume Next", [])
    if isinstance(arg, Const) and arg.value in (-2, 0xFFFFFFFE):
        return stmt("Resume", [])
    c = Call("Resume", [arg])
    c.resume = True                 # (structure: with the jump after it, Resume label)
    c.pure = False
    return c


def put_get(kw, var, num, rec=None, size=None):
    """Put / Get #num, [rec], var for the binary file helpers.  var stays as
    passed (&v: the variable's address, which the passes must still see as
    taken - Get writes it); it prints as the variable.  A record number of
    -1 is the one left out.  size: the variable's byte count __vbaPut3/4 and
    __vbaGet3/4 take (a UDT's Len, or a scalar's width), kept as put_size."""
    if rec is None or isinstance(rec, Const) and rec.value in (-1, 0xffffffff):
        c = stmt("%s #{0}, , {1}" % kw, [num, var])
    else:
        c = stmt("%s #{0}, {1}, {2}" % kw, [num, rec, var])
    if isinstance(size, Const) and isinstance(size.value, int) and size.value > 0:
        c.put_size = size.value
    return c


def file_open(a):
    mode, reclen, num, name = a
    m = mode.value & 0xff if isinstance(mode, Const) else None
    # the access in the next nibble (VBDoom: For Binary Access Read is 120h),
    # the lock in the one after it (ChessBrainVB: For Append Lock Write is 2008h)
    acc = mode.value & 0x0f00 if isinstance(mode, Const) else 0
    lock = mode.value & 0xf000 if isinstance(mode, Const) else 0
    if m not in OPEN_MODES or (isinstance(mode, Const) and mode.value & ~0xffff) or \
            (acc and acc not in OPEN_ACCESS) or (lock and lock not in OPEN_LOCK):
        return Call("__vbaFileOpen", list(a))
    fmt = "Open {0} For %s%s%s As #{1}" % (OPEN_MODES[m], OPEN_ACCESS.get(acc, ""),
                                          OPEN_LOCK.get(lock, ""))
    args = [name, num]
    if not (isinstance(reclen, Const) and reclen.value in (-1, 0xffffffff)):
        fmt += " Len = {2}"
        args.append(reclen)
    return stmt(fmt, args)


def instr_args(a):
    from .ir import Missing
    # (&result, &string1, &string2, &start, compare): gh_json VBA.InStr(s, Mid$(...))
    s1, s2, start, cmp = deref(a[1]), deref(a[2]), deref(a[3]), a[4]
    out = ([] if isinstance(start, Missing) else [start]) + [s1, s2]
    if not (isinstance(cmp, Const) and cmp.value == 0):
        if isinstance(start, Missing):
            out = [Const(1)] + out          # InStr(1, s1, s2, compare)
        out.append(cmp)
    return out


def trailing_defaults(args, defaults):
    """Drop trailing arguments equal to their documented default."""
    while args and len(args) - 1 in defaults and isinstance(args[-1], Const) and \
            args[-1].value == defaults[len(args) - 1]:
        args.pop()
    return args


def opt_args(args):
    """Drop trailing omitted Optional arguments."""
    from .ir import Missing
    out = list(args)
    while out and isinstance(out[-1], Missing):
        out.pop()
    return out


def dword_pair(lo, hi, t):
    """A Double or Currency passed as two dwords: a literal, the 8-byte field
    or variable whose halves they are, or (unresolved) __r8(lo, hi)."""
    import struct
    from .machine import Hi
    if isinstance(hi, Hi) and (hi.of is lo or hasattr(lo, "key") and hi.of.key() == lo.key()):
        return lo                           # stored as one 8-byte value (fstp qword)
    if isinstance(lo, Const) and isinstance(hi, Const) and isinstance(lo.value, int) and \
            isinstance(hi.value, int):
        raw = struct.pack("<II", lo.value & 0xffffffff, hi.value & 0xffffffff)
        if t == "Currency":
            return Const(struct.unpack("<q", raw)[0] / 10000.0, "Currency")
        return Const(struct.unpack("<d", raw)[0])
    if isinstance(lo, Deref) and isinstance(hi, Deref) and lo.e == hi.e and \
            hi.off == lo.off + 4 and lo.size == 4 == hi.size:
        d = Deref(lo.e, 8, lo.off)          # both halves of one field
        d.vbtype = t
        return d
    if isinstance(lo, Var) and isinstance(hi, Var) and lo.space == hi.space and \
            lo.space in ("local", "global", "arg") and isinstance(lo.off, int) and \
            hi.off == lo.off + 4:
        return Var(lo.space, lo.off, vbtype=t)
    return Call("__r8" if t == "Double" else "__cy", [lo, hi])


def fn(name, *idx, ret=None, deref_args=()):
    """Handler printing name(a[i] ...)."""
    def h(a):
        args = []
        for i in idx:
            v = a[i]
            args.append(deref(v) if i in deref_args else v)
        return Call(name, args, vbtype=ret, pure=True)
    return h


def cat(a):
    return BinOp("&", a[1], a[0], "String")


def varop(op):
    # __vbaVarAdd(&result, &right, &left)  (verify operand order)
    return lambda a: BinOp(op, deref(a[2]), deref(a[1]), "Variant")


def vartst(op):
    # __vbaVarTstGt(&right, &left) = left > right: VB pushes the left operand
    # first, like __vbaVarAdd (t03 VarMath "If r > 10")
    return lambda a: BinOp(op, deref(a[1]), deref(a[0]), "Boolean")


S = Spec
SPECS = {
    # ---- strings: copies / moves / frees (register based)
    "__vbaStrCopy": S(regs=("ecx", "edx"), kind="store", ret="String"),
    "__vbaStrMove": S(regs=("ecx", "edx"), kind="store", ret="String"),
    "__vbaFreeStr": S(regs=("ecx",), kind="free"),
    "__vbaFreeObj": S(regs=("ecx",), kind="free"),
    "__vbaFreeVar": S(regs=("ecx",), kind="free"),
    "__vbaFreeStrList": S(cdecl=True, varargs=True, kind="free"),
    "__vbaFreeObjList": S(cdecl=True, varargs=True, kind="free"),
    "__vbaFreeVarList": S(cdecl=True, varargs=True, kind="free"),
    "__vbaVarMove": S(regs=("ecx", "edx"), kind="varstore"),
    "__vbaVarCopy": S(regs=("ecx", "edx"), kind="varstore"),
    "__vbaVarDup": S(regs=("ecx", "edx"), kind="varstore"),
    # (ecx = a ByRef Variant parameter, edx = source): vDest = vSrc
    "__vbaVargVarCopy": S(regs=("ecx", "edx"), kind="varstore"),
    "__vbaVargVarMove": S(regs=("ecx", "edx"), kind="varstore"),
    "__vbaVargVarRef": S(regs=("ecx", "edx"), kind="varstore"),     # VT_BYREF|VT_VARIANT to *edx
    # copies *edx into the not yet initialised Variant at ecx (Array(...) items)
    "__vbaVarZero": S(regs=("ecx", "edx"), kind="varstore"),
    # __vbaVarSet*(&dst Variant, src): Set dst = src (an object in a Variant)
    "__vbaVarSetVar": S(2, kind="varset"),
    "__vbaVarSetVarAddref": S(2, kind="varset"),
    "__vbaVarSetObj": S(2, kind="varset"),
    "__vbaVarSetObjAddref": S(2, kind="varset"),
    "__vbaVarSetUnk": S(2, kind="varset"),
    "__vbaVarSetUnkAddref": S(2, kind="varset"),
    "__vbaObjSet": S(2, kind="objset"),
    "__vbaObjSetAddref": S(2, kind="objset"),
    "__vbaCastObj": S(2, kind="castobj", f=lambda a: a[0]),
    # (t07c) the cast to another interface is implicit in VB: Set sh = v
    "__vbaCastObjVar": S(2, kind="castobj", plain=(1,), f=lambda a: deref(a[0])),
    "__vbaObjIs": S(2, ret="Boolean", f=lambda a: BinOp("Is", a[0], a[1], "Boolean")),
    # __vbaCheckType(obj, &IID) / __vbaCheckTypeVar(&var, &IID): TypeOf x Is C;
    # the class name is filled in by calls.do_runtime (kind "checktype")
    "__vbaCheckType": S(2, kind="checktype", ret="Boolean"),
    "__vbaCheckTypeVar": S(2, kind="checktype", ret="Boolean"),
    "rtcTypeName": S(1, ret="String", f=lambda a: Call("TypeName", [deref(a[0])])),
    "rtcVarType": S(1, ret="Integer", f=lambda a: Call("VarType", [deref(a[0])])),
    # VBA.CStr(v) spelled with the library: the typelib entry, not __vbaStrVarVal
    "rtBstrFromErrVar": S(1, ret="String", f=lambda a: Call("CStr", [deref(a[0])])),
    "__vbaStrCat": S(2, ret="String", f=cat),
    "__vbaVarCat": S(3, kind="varres", f=lambda a: BinOp("&", deref(a[2]), deref(a[1]), "Variant")),
    "__vbaLenBstr": S(1, ret="Long", f=fn("Len", 0)),
    "__vbaLenBstrB": S(1, ret="Long", f=fn("LenB", 0)),
    "__vbaLenVar": S(2, kind="varres", f=lambda a: Call("Len", [deref(a[1])])),
    "__vbaInStr": S(4, ret="Long", f=lambda a: Call("InStr", [a[3], a[2], a[1]] + ([] if isinstance(a[0], Const) and a[0].value == 0 else [a[0]]))),
    "__vbaInStrVar": S(5, kind="varres", f=lambda a: Call("InStr", [deref(a[4]), deref(a[3]), deref(a[2])])),   # verify
    "__vbaStrCmp": S(2, ret="Long", f=lambda a: Call("__strcmp", [a[1], a[0]], pure=True)),                         # verify
    "__vbaStrComp": S(3, ret="Long", f=lambda a: Call("StrComp", [a[2], a[1], a[0]])),    # t02 verified
    # __vbaStrLike(pattern, string): string Like pattern (t02 verified)
    "__vbaStrLike": S(2, ret="Boolean", f=lambda a: BinOp("Like", a[1], a[0], "Boolean")),
    # rtcImmediateIf(&result, &condition, &truepart, &falsepart)
    "rtcImmediateIf": S(4, kind="varres",
                        f=lambda a: Call("IIf", [deref(a[1]), deref(a[2]), deref(a[3])],
                                         vbtype="Variant")),
    "__vbaStrToAnsi": S(2, kind="marshal"),
    "__vbaStrToUnicode": S(2, kind="unmarshal"),
    "__vbaFixstrConstruct": S(2, kind="fixconstruct"),
    "__vbaStrErrVarCopy": S(1, ret="String", f=fn("CStr", 0, deref_args=(0,))),
    "__vbaStrVarMove": S(1, ret="String", f=fn("CStr", 0, deref_args=(0,))),
    "__vbaStrVarCopy": S(1, ret="String", f=fn("CStr", 0, deref_args=(0,))),
    "__vbaStrVarVal": S(2, ret="String", f=fn("CStr", 1, deref_args=(1,))),
    # ---- conversions
    "__vbaStrI2": S(1, ret="String", f=fn("CStr", 0)),
    "__vbaStrI4": S(1, ret="String", f=fn("CStr", 0)),
    "__vbaStrUI1": S(1, ret="String", f=fn("CStr", 0)),
    # (a constant argument is True / False: CStr(True) is "True", not "-1")
    "__vbaStrBool": S(1, ret="String", f=lambda a: Call("CStr", [
        Const(a[0].value, "Boolean") if isinstance(a[0], Const) and
        isinstance(a[0].value, int) else a[0]], vbtype="String", pure=True)),
    "__vbaStrR4": S(1, ret="String", f=fn("CStr", 0)),
    "__vbaStrR8": S(2, ret="String", f=lambda a: Call("CStr", [dword_pair(a[0], a[1], "Double")])),
    "__vbaStrCy": S(2, ret="String", f=lambda a: Call("CStr", [dword_pair(a[0], a[1], "Currency")])),
    "__vbaI4Str": S(1, ret="Long", f=fn("CLng", 0)),
    "__vbaI2Str": S(1, ret="Integer", f=fn("CInt", 0)),
    "__vbaUI1Str": S(1, ret="Byte", f=fn("CByte", 0)),
    "__vbaBoolStr": S(1, ret="Boolean", f=fn("CBool", 0)),
    "__vbaR8Str": S(1, fpu_out=True, ret="Double", f=fn("CDbl", 0)),
    "__vbaR4Str": S(1, fpu_out=True, ret="Single", f=fn("CSng", 0)),
    "__vbaI4Var": S(1, ret="Long", f=fn("CLng", 0, deref_args=(0,))),
    "__vbaI2Var": S(1, ret="Integer", f=fn("CInt", 0, deref_args=(0,))),
    "__vbaUI1Var": S(1, ret="Byte", f=fn("CByte", 0, deref_args=(0,))),
    "__vbaBoolVar": S(1, ret="Boolean", f=fn("CBool", 0, deref_args=(0,))),
    "__vbaBoolVarNull": S(1, ret="Boolean", f=fn("CBool", 0, deref_args=(0,))),
    "__vbaR8Var": S(1, fpu_out=True, ret="Double", f=fn("CDbl", 0, deref_args=(0,))),
    "__vbaR4Var": S(1, fpu_out=True, ret="Single", f=fn("CSng", 0, deref_args=(0,))),
    "__vbaI2I4": S(regs=("ecx",), ret="Integer", f=lambda a: Call("CInt", [a[0]])),
    "__vbaUI1I2": S(regs=("ecx",), ret="Byte", f=lambda a: Call("CByte", [a[0]])),
    "__vbaUI1I4": S(regs=("ecx",), ret="Byte", f=lambda a: Call("CByte", [a[0]])),
    # Currency results come back in edx:eax (calls.py sets edx to the high half)
    # Currency arithmetic: each Currency argument is two dwords (lo, hi)
    "__vbaCyMul": S(4, ret="Currency", kind="cy64", f=lambda a: BinOp(
        "*", dword_pair(a[0], a[1], "Currency"), dword_pair(a[2], a[3], "Currency"), "Currency")),
    "__vbaCyAdd": S(4, ret="Currency", kind="cy64", f=lambda a: BinOp(
        "+", dword_pair(a[0], a[1], "Currency"), dword_pair(a[2], a[3], "Currency"), "Currency")),
    "__vbaCySub": S(4, ret="Currency", kind="cy64", f=lambda a: BinOp(
        "-", dword_pair(a[0], a[1], "Currency"), dword_pair(a[2], a[3], "Currency"), "Currency")),
    # (Integer, Currency): gh_zip "push hi; push lo; push 2"
    "__vbaCyMulI2": S(3, ret="Currency", kind="cy64", f=lambda a: BinOp(
        "*", a[0], dword_pair(a[1], a[2], "Currency"), "Currency")),
    "__vbaCyI4": S(1, ret="Currency", kind="cy64", f=lambda a: Call("CCur", [a[0]])),
    "__vbaCyI2": S(1, ret="Currency", kind="cy64", f=lambda a: Call("CCur", [a[0]])),
    "__vbaCyVar": S(1, ret="Currency", kind="cy64", f=fn("CCur", 0, deref_args=(0,))),
    "__vbaR8Cy": S(2, fpu_out=True, ret="Double",
                   f=lambda a: Call("CDbl", [dword_pair(a[0], a[1], "Currency")])),
    "__vbaI4Cy": S(2, ret="Long", f=lambda a: Call("CLng", [dword_pair(a[0], a[1], "Currency")])),
    # a Currency against st(0): Sgn(cy - st0) (gh_zip OpenArchive: "If cOffset >=
    # LNG_LEN_END" and IIf(cOffset - 22 < MAX_END_SEEK, ...) read the right way round)
    "__vbaFpCmpCy": S(2, fpu_in=1, ret="Long", f=lambda a: Call(
        "Sgn", [BinOp("-", dword_pair(a[1], a[2], "Currency"), a[0])])),
    "__vbaFpCy": S(fpu_in=1, ret="Currency", kind="cy64", f=lambda a: Call("CCur", [a[0]])),
    "__vbaFpI4": S(fpu_in=1, ret="Long", f=lambda a: Call("CLng", [a[0]])),
    "__vbaFpI2": S(fpu_in=1, ret="Integer", f=lambda a: Call("CInt", [a[0]])),
    "__vbaFpUI1": S(fpu_in=1, ret="Byte", f=lambda a: Call("CByte", [a[0]])),
    "__vbaFpR4": S(fpu_in=1, fpu_out=True, ret="Single", f=lambda a: a[0]),
    "__vbaFpR8": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: a[0]),
    "__vbaFPInt": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Int", [a[0]])),
    # Int(st0) as a Long (unoptimised code: x = Int(d) into a Long, VBDoom I_GetTime)
    "__vbaR8IntI4": S(fpu_in=1, ret="Long", f=lambda a: Call("Int", [a[0]], vbtype="Long")),
    # Int / Fix of st0 into an Integer / a Long (the generic call had no
    # argument at all: `R8IntI2()`)
    "__vbaR8IntI2": S(fpu_in=1, ret="Integer", f=lambda a: Call("Int", [a[0]], vbtype="Integer")),
    "__vbaR8FixI4": S(fpu_in=1, ret="Long", f=lambda a: Call("Fix", [a[0]], vbtype="Long")),
    "__vbaR8FixI2": S(fpu_in=1, ret="Integer", f=lambda a: Call("Fix", [a[0]], vbtype="Integer")),
    "__vbaFPFix": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Fix", [a[0]])),
    # Doubles passed as two dwords; results in st(0) or eax (t06m MathFuncs)
    "__vbaI4Abs": S(regs=("ecx",), ret="Long", f=lambda a: Call("Abs", [a[0]])),
    "__vbaI2Abs": S(regs=("ecx",), ret="Integer", f=lambda a: Call("Abs", [a[0]])),
    "__vbaR8Sgn": S(2, ret="Integer", f=lambda a: Call("Sgn", [dword_pair(a[0], a[1], "Double")])),
    "__vbaR4Sgn": S(1, ret="Integer", f=lambda a: Call("Sgn", [a[0]])),
    # Stop.  Like and ^ of Variants: pattern and exponent pushed first, as in __vbaVarTst*
    "__vbaStopExe": S(0, f=lambda a: Call("Stop", []), pure=False),
    "__vbaVarLikeVar": S(3, kind="varres", f=lambda a: BinOp("Like", deref(a[2]), deref(a[1]), "Variant")),
    "__vbaVarLike": S(2, ret="Boolean", f=lambda a: BinOp("Like", deref(a[1]), deref(a[0]), "Boolean")),
    "__vbaLenVarB": S(2, kind="varres", f=lambda a: Call("LenB", [deref(a[1])])),
    "__vbaR8Abs": S(2, fpu_out=True, ret="Double",
                    f=lambda a: Call("Abs", [dword_pair(a[0], a[1], "Double")])),
    "rtcSqr": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Sqr", [dword_pair(a[0], a[1], "Double")])),
    "rtcExp": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Exp", [dword_pair(a[0], a[1], "Double")])),
    "rtcLog": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Log", [dword_pair(a[0], a[1], "Double")])),
    "rtcSin": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Sin", [dword_pair(a[0], a[1], "Double")])),
    "rtcCos": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Cos", [dword_pair(a[0], a[1], "Double")])),
    "rtcTan": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Tan", [dword_pair(a[0], a[1], "Double")])),
    "rtcAtn": S(2, fpu_out=True, ret="Double", f=lambda a: Call("Atn", [dword_pair(a[0], a[1], "Double")])),
    "__vbaPowerR8": S(4, fpu_out=True, ret="Double", f=lambda a: BinOp(
        "^", dword_pair(a[0], a[1], "Double"), dword_pair(a[2], a[3], "Double"), "Double")),
    # rtcRound(&result, &value, digits)
    "rtcRound": S(3, kind="varres", f=lambda a: Call("Round", [deref(a[1])] + (
        [] if isinstance(a[2], Const) and a[2].value == 0 else [a[2]]))),
    "rtcHexBstrFromVar": S(1, ret="String", f=lambda a: Call("Hex$", [deref(a[0])])),
    "rtcOctBstrFromVar": S(1, ret="String", f=lambda a: Call("Oct$", [deref(a[0])])),
    "rtcHexVarFromVar": S(2, kind="varres", f=lambda a: Call("Hex", [deref(a[1])])),
    # __vbaMidStmtBstr(0, src, length, start, &target): Mid$(target, start, length) = src
    # (verified with Mid$(t, 3, 2); "length omitted = -1" is a guess: verify)
    "__vbaAryVar": S(2, kind="aryvar"),
    # fixed-length strings (t06m MakeRec): String * n, stored as n UTF-16 chars
    "__vbaLsetFixstr": S(3, kind="lsetfix"),
    "__vbaLsetFixstrFree": S(3, kind="lsetfix"),
    # RSet s = x: as __vbaLsetFixstr, n = 0 for a variable-length String
    "__vbaRsetFixstr": S(3, kind="rsetfix"),
    "__vbaRsetFixstrFree": S(3, kind="rsetfix"),
    "__vbaStrFixstr": S(2, kind="strfix", ret="String"),
    # ---- dates (t06m Dates); results are Variants
    "rtcGetPresentDate": S(1, kind="varres", f=lambda a: Var("global", "Now", "Now", "Date")),
    "rtcPackDate": S(4, kind="varres", f=lambda a: Call("DateSerial", [a[1], a[2], a[3]])),
    "rtcPackTime": S(4, kind="varres", f=lambda a: Call("TimeSerial", [a[1], a[2], a[3]])),
    # rtcDateFromVar(&v): CDate(v), in st(0)
    "rtcDateFromVar": S(1, fpu_out=True, ret="Date", f=lambda a: Call("CDate", [deref(a[0])])),
    # rtcDateDiff(&result, interval, &date1, &date2, first day of week, first week of year): 1 and
    # 1 are the defaults; rtcDatePart the same with one date
    "rtcDateDiff": S(6, kind="varres", f=lambda a: Call("DateDiff", [a[1], deref(a[2]), deref(a[3])] + (
        [] if isinstance(a[4], Const) and a[4].value == 1 and isinstance(a[5], Const) and a[5].value == 1
        else [a[4], a[5]]))),
    "rtcDatePart": S(5, kind="varres", f=lambda a: Call("DatePart", [a[1], deref(a[2])] + (
        [] if isinstance(a[3], Const) and a[3].value == 1 and isinstance(a[4], Const) and a[4].value == 1
        else [a[3], a[4]]))),
    "rtcDateAdd": S(5, kind="varres", f=lambda a: Call(
        "DateAdd", [a[1], dword_pair(a[2], a[3], "Double"), deref(a[4])])),
    "rtcGetYear": S(2, kind="varres", f=lambda a: Call("Year", [deref(a[1])])),
    "rtcGetMonthOfYear": S(2, kind="varres", f=lambda a: Call("Month", [deref(a[1])])),
    "rtcGetDayOfMonth": S(2, kind="varres", f=lambda a: Call("Day", [deref(a[1])])),
    "rtcGetHourOfDay": S(2, kind="varres", f=lambda a: Call("Hour", [deref(a[1])])),
    "rtcGetMinuteOfHour": S(2, kind="varres", f=lambda a: Call("Minute", [deref(a[1])])),
    "rtcGetSecondOfMinute": S(2, kind="varres", f=lambda a: Call("Second", [deref(a[1])])),
    "rtcGetDayOfWeek": S(3, kind="varres", f=lambda a: Call("Weekday", [deref(a[1])] + (
        [] if isinstance(a[2], Const) and a[2].value == 1 else [a[2]]))),
    # __vbaDateVar(&v): CDate(v), in st(0)
    "__vbaDateVar": S(1, fpu_out=True, ret="Date", f=lambda a: deref(a[0])),
    # rtcChoose(&result, index As Single, &ParamArray choices)
    "rtcCurrentDirBstr": S(1, ret="String", pure=False,
                           f=lambda a: Call("CurDir$", opt_args([deref(a[0])]))),
    "rtcCurrentDir": S(2, kind="varres", pure=False,
                       f=lambda a: Call("CurDir", opt_args([deref(a[1])]))),
    "rtcArray": S(2, kind="varres", plain=(1,), f=lambda a: paramarray_call("Array", [arr(a[1])])),
    "rtcChoose": S(3, kind="varres", plain=(2,),
                   f=lambda a: paramarray_call("Choose", [single_bits(a[1]), arr(a[2])])),
    # rtcSwitch(&result, &ParamArray pairs)
    "rtcSwitch": S(2, kind="varres", plain=(1,), f=lambda a: paramarray_call("Switch", [arr(a[1])])),
    # rtcFormat*(&expr, digits, leading zero, parentheses, group): -1 and -2 are
    # the defaults; the expression is a Variant, a typed variable's VT_BYREF
    "rtcFormatCurrency": S(5, ret="String", f=lambda a: format_call("FormatCurrency", a)),
    "rtcFormatNumber": S(5, ret="String", f=lambda a: format_call("FormatNumber", a)),
    "rtcFormatPercent": S(5, ret="String", f=lambda a: format_call("FormatPercent", a)),
    "rtcFormatDateTime": S(2, ret="String", f=lambda a: Call("FormatDateTime", trailing_defaults(
        [deref(a[0]), a[1]], {1: 0}))),
    # rtcSplit(&result, expr, &delimiter, limit, compare)
    "rtcSplit": S(5, kind="varres", f=lambda a: Call("Split", trailing_defaults(
        [a[1], deref(a[2]), a[3], a[4]], {2: -1, 3: 0}))),
    # rtcJoin(&Variant (VT_BYREF | VT_ARRAY | vt -> the array), &delimiter)
    "rtcJoin": S(2, ret="String", f=lambda a: Call("Join", [deref(a[0]), deref(a[1])])),
    "__vbaMidStmtBstr": S(5, kind="stmtcall", f=lambda a: stmt(
        "Mid$({0}, {1}) = {2}" if isinstance(a[2], Const) and a[2].value in (-1, 0xffffffff)
        else "Mid$({0}, {1}, {3}) = {2}", [deref(a[4]), a[3], a[1], a[2]])),
    "rtcStrReverse": S(1, ret="String", f=fn("StrReverse", 0)),
    # rtcInStrRev(s, find, start, compare): -1 and 0 are the defaults
    # InStr on Variants: (&result, &s1, &s2, &start, compare) - start may be Missing
    "rtcInStrChar": S(5, kind="varres", f=lambda a: Call("InStr", instr_args(a))),
    "rtcInStrRev": S(4, ret="Long", f=lambda a: Call("InStrRev", trailing_defaults(
        list(a), {2: -1, 3: 0}))),
    # ---- variants
    "__vbaVarAdd": S(3, kind="varres", f=varop("+")),
    # Variant comparisons with a Variant result (Null-propagating): (&res, b, a)
    # like the arithmetic ones                                              # verify order
    "__vbaVarCmpEq": S(3, kind="varres", f=varop("=")),
    "__vbaVarCmpNe": S(3, kind="varres", f=varop("<>")),
    "__vbaVarCmpLt": S(3, kind="varres", f=varop("<")),
    "__vbaVarCmpLe": S(3, kind="varres", f=varop("<=")),
    "__vbaVarCmpGt": S(3, kind="varres", f=varop(">")),
    "__vbaVarCmpGe": S(3, kind="varres", f=varop(">=")),
    "__vbaVarSub": S(3, kind="varres", f=varop("-")),
    "__vbaVarMul": S(3, kind="varres", f=varop("*")),
    "__vbaVarDiv": S(3, kind="varres", f=varop("/")),
    "__vbaVarIdiv": S(3, kind="varres", f=varop("\\")),
    "__vbaVarMod": S(3, kind="varres", f=varop("Mod")),
    "__vbaVarAnd": S(3, kind="varres", f=varop("And")),
    "__vbaVarOr": S(3, kind="varres", f=varop("Or")),
    "__vbaVarXor": S(3, kind="varres", f=varop("Xor")),
    "__vbaVarNot": S(2, kind="varres", f=lambda a: UnOp("Not", deref(a[1]))),
    "__vbaVarNeg": S(2, kind="varres", f=lambda a: UnOp("-", deref(a[1]))),
    "__vbaVarTstEq": S(2, ret="Boolean", f=vartst("=")),
    "__vbaVarTstNe": S(2, ret="Boolean", f=vartst("<>")),
    "__vbaVarTstLt": S(2, ret="Boolean", f=vartst("<")),
    "__vbaVarTstLe": S(2, ret="Boolean", f=vartst("<=")),
    "__vbaVarTstGt": S(2, ret="Boolean", f=vartst(">")),
    "__vbaVarTstGe": S(2, ret="Boolean", f=vartst(">=")),
    # ---- errors / procedure control
    "__vbaOnError": S(1, kind="onerror"),
    "__vbaExitProc": S(kind="exitproc"),
    "__vbaHresultCheckObj": S(4, kind="ignore"),
    "__vbaHresultCheck": S(1, kind="ignore"),
    "__vbaSetSystemError": S(kind="ignore"),
    "__vbaChkstk": S(regs=("eax",), kind="ignore"),
    # __vbaDerefAry1(psa, i): the address of element i of a one-dimensional
    # array, bounds checked (unoptimised code indexes arrays only this way)
    "__vbaDerefAry1": S(2, kind="derefary"),
    # __vbaDerefAry(dims, psa, last subscript ... first), cdecl: Buckets(x, y)
    "__vbaDerefAry": S(cdecl=True, kind="derefarym"),
    "__vbaExceptHandler": S(kind="ignore"),
    # __vbaRaiseEvent(Me, event number, argument count, Variant by value...), cdecl
    "__vbaRaiseEvent": S(3, kind="raiseevent", cdecl=True, pure=False),
    "__vbaLateMemCall": S(3, kind="latecall", cdecl=True, pure=False),
    # the same on an object held in a Variant: (&res,) &variant, &name, argc, args
    # by DISPID: (&res,) obj, dispid, argc, args...: dispid 0 is the default member, o(k)
    "__vbaLateIdCallLd": S(4, kind="latecallld", cdecl=True, pure=False),
    "__vbaLateIdCall": S(3, kind="latecall", cdecl=True, pure=False),
    # obj.<DISPID> = value: (obj, dispid, Variant by value), stdcall
    "__vbaLateIdSt": S(6, kind="lateidst", pure=False),
    # Input$(count, filenumber) (rtcInputCharCount: 2 arguments, a String)
    "rtcInputCharCount": S(2, ret="String", pure=False, f=lambda a: Call("Input$", [a[0], a[1]])),
    "__vbaLateIdStAd": S(6, kind="lateidst", pure=False),
    "__vbaVarLateMemCallLd": S(4, kind="latecallld", cdecl=True, pure=False),
    "__vbaVarLateMemCallLdRf": S(4, kind="latecallld", cdecl=True, pure=False),
    "__vbaVarLateMemCall": S(3, kind="latecall", cdecl=True, pure=False),
    # obj.name(args...) = value: obj, &name, argc, the arguments, the value, each Variant by value
    "__vbaVarLateMemCallSt": S(3, kind="latecallst", cdecl=True, pure=False),
    "__vbaLateMemCallSt": S(3, kind="latecallst", cdecl=True, pure=False),
    # __vbaLateMemSt(obj, &name, Variant by value): obj.name = value (stdcall)
    "__vbaLateMemSt": S(6, kind="latememst", pure=False),
    "__vbaLateMemStAd": S(cdecl=True, kind="latememst", pure=False),
    "__vbaVarLateMemSt": S(6, kind="latememst", pure=False),     # (on a Variant)
    "__vbaVarLateMemStAd": S(cdecl=True, kind="latememst", pure=False),
    # rtcCreateObject2(&result, class, server): CreateObject(class[, server])
    "rtcCreateObject2": S(3, kind="varres", pure=False,
                          f=lambda a: Call("CreateObject", [a[1]] + (
                              # (0: a null BSTR, no server.  Printed, it asked for a server
                              # named "0": error 429, a target's backup forms)
                              [] if isinstance(a[2], Str) and a[2].text == "" or
                              isinstance(a[2], Const) and a[2].value == 0 else [a[2]]),
                              vbtype="Variant")),
    "__vbaObjVar": S(1, ret="Object", f=lambda a: deref(a[0])),
    # IUnknown of a Variant (v Is o): no ret type, it would land on the Variant itself
    "__vbaUnkVar": S(1, f=lambda a: of_variant(deref(a[0]))),
    "__vbaLateMemCallLd": S(4, kind="latecallld", cdecl=True, pure=False),
    "__vbaErrorOverflow": S(kind="raise"),
    "__vbaGenerateBoundsError": S(kind="raise"),
    "__vbaFPException": S(kind="raise"),
    "rtcErrObj": S(0, ret="Object", f=lambda a: err_object()),
    # ---- objects
    "__vbaNew2": S(2, kind="new"),
    "__vbaNew": S(1, kind="newobj"),
    # ---- arrays / records
    "__vbaUbound": S(2, ret="Long", f=lambda a: Call("UBound", [arr(a[1])] + dim(a[0]))),
    "__vbaLbound": S(2, ret="Long", f=lambda a: Call("LBound", [arr(a[1])] + dim(a[0]))),
    "__vbaUbound3": S(3, ret="Long", f=lambda a: Call("UBound", [arr(a[1])] + dim(a[0]))),
    "__vbaErase": S(2, kind="erase"),
    "__vbaEraseKeepData": S(2, kind="erase"),
    "__vbaRedim": S(cdecl=True, kind="redim"),
    "__vbaRedimPreserve": S(cdecl=True, kind="redim"),
    "__vbaRedimVar2": S(cdecl=True, kind="redim"),
    # (vt, &variant, nDims, ub1, lb1, ...) cdecl: ReDim v(lb To ub) As <vt>
    "__vbaRedimVar": S(cdecl=True, kind="redimvar"),
    "__vbaRedimPreserveVar": S(cdecl=True, kind="redimvar"),
    "__vbaAryCopy": S(2, kind="arycopy"),
    "__vbaAryMove": S(2, kind="arycopy"),
    # (record descriptor, &dst, &src): dst = src for arrays of UDTs with Strings
    "__vbaAryRecCopy": S(3, kind="arycopy"),
    "__vbaAryRecMove": S(3, kind="arycopy"),
    # __vbaVarVargNofree(ecx = &temp, edx = &v): &v, or the Variant v refers
    # to (VT_BYREF); used to read a ByRef Variant parameter (gh_qr)
    "__vbaVarVargNofree": S(regs=("ecx", "edx"), kind="vararg"),
    # __vbaVarIndexLoad(&result, &v, count, index Variants by value...), cdecl:
    # result = v(i, ...) for a Variant holding an array (gh_qr vSplit(lIdx))
    "__vbaVarIndexLoad": S(3, kind="varindex", cdecl=True),
    # (&res, lock state, &array, n, index Variants): a(i) as a reference
    "__vbaVarIndexLoadRefLock": S(4, kind="varindex", cdecl=True),
    "__vbaVarIndexLoadRef": S(3, kind="varindex", cdecl=True),
    "rtcLenCharVar": S(2, kind="varres", f=lambda a: Call("Len", [deref(a[1])])),
    "rtcCharValueBstr": S(1, ret="Integer", f=lambda a: Call("AscW", [a[0]])),
    # (&array, nIdx, value Variant by value, nIdx index Variants by value): a(i) = v
    "__vbaVarIndexStore": S(cdecl=True, kind="varindexstore", pure=False),
    # __vbaRefVarAry(&v): &(the SAFEARRAY* inside v) -> UBound(v)
    "__vbaRefVarAry": S(1, kind="refvarary"),
    # __vbaVar2Vec(&sa, &v): the array a Variant holds (ba = TextOrByteArray)
    "__vbaVar2Vec": S(2, kind="var2vec"),
    # __vbaRecAssign(&record descriptor, &dst, &src): dst = src for a UDT with Strings
    # __vbaVerifyVarObj(&v): checks v holds an object, returns &v
    "__vbaVerifyVarObj": S(1, kind="identity"),
    "__vbaRecAssign": S(3, kind="recassign"),
    "__vbaRecDestruct": S(2, kind="recdestruct"),
    "__vbaCopyBytes": S(3, kind="copybytes"),
    # __vbaAryConstruct2(&SAFEARRAY, &template, VARTYPE): a fixed-size local array
    "__vbaAryConstruct2": S(3, kind="aryconstruct"),
    "__vbaAryDestruct": S(2, kind="ignore"),
    "__vbaAryUnlock": S(1, kind="ignore"),
    # __vbaAryLock(&tmp, sa): tmp is the array while locked (baRetVal(0) passed ByRef)
    "__vbaAryLock": S(2, kind="arylock"),
    # __vbaStr2Vec(&sa, s): a Byte array from a String (ba = vbNullString)
    "__vbaStr2Vec": S(2, kind="var2vec"),
    # ---- VBA library (rtc*)
    "rtcLeftCharBstr": S(2, ret="String", f=fn("Left$", 0, 1)),
    "rtcRightCharBstr": S(2, ret="String", f=fn("Right$", 0, 1)),
    "rtcMidCharBstr": S(3, ret="String", f=lambda a: Call("Mid$", [a[0], a[1]] + [deref(a[2])])),
    "rtcUpperCaseBstr": S(1, ret="String", f=fn("UCase$", 0)),
    "rtcLowerCaseBstr": S(1, ret="String", f=fn("LCase$", 0)),
    "rtcTrimBstr": S(1, ret="String", f=fn("Trim$", 0)),
    "rtcLeftTrimBstr": S(1, ret="String", f=fn("LTrim$", 0)),
    "rtcRightTrimBstr": S(1, ret="String", f=fn("RTrim$", 0)),
    "rtcBstrFromAnsi": S(1, ret="String", f=fn("Chr$", 0)),
    "rtcAnsiValueBstr": S(1, ret="Integer", f=fn("Asc", 0)),
    "rtcR8ValFromBstr": S(1, fpu_out=True, ret="Double", f=fn("Val", 0)),
    "rtcSpaceBstr": S(1, ret="String", f=fn("Space$", 0)),
    "rtcMsgBox": S(5, ret="Integer", pure=False,
                   f=lambda a: Call("MsgBox", [deref(a[0]), a[1], deref(a[2]), deref(a[3]), deref(a[4])])),
    "rtcDoEvents": S(0, ret="Integer", pure=False, f=lambda a: Call("DoEvents", [])),
    "rtcRgb": S(3, ret="Long", f=fn("RGB", 0, 1, 2)),
    "rtcRandomize": S(1, kind="stmtcall", f=lambda a: Call("Randomize", [deref(a[0])])),
    "rtcRandomNext": S(1, fpu_out=True, ret="Single", pure=False, f=lambda a: Call("Rnd", [deref(a[0])])),
    # Resume: __vbaResume(0), then a jump to the label (gh_zip CompressArchive
    # "Resume QH": push 0, call, jmp QH): structure.Structurer makes the
    # statement and the jump "Resume L<target>"
    "__vbaResume": S(1, kind="stmtcall", pure=False, f=lambda a: resume_call(a[0])),
    # GoSub L / Return (cfg.gosubs: the jump after it is gone); the cleanup's Free is the compiler's
    "__vbaGosub": S(1, kind="gosub", pure=False),
    "__vbaGosubReturn": S(1, kind="stmtcall", pure=False, f=lambda a: stmt("Return", [])),
    "__vbaGosubFree": S(1, kind="ignore"),
    # On n GoSub / GoTo: the number checked, then a test per target
    "__vbaOnGoCheck": S(1, kind="ongocheck"),
    # a UDT with Strings given to a Declare: VB passes an ANSI copy
    # (__vbaRecUniToAnsi(desc, &copy, &udt) returns &copy), copies it back and
    # frees it after the call (ChessBrainVB CreateProcess ..., udtStart, udtProc)
    "__vbaRecUniToAnsi": S(3, kind="recuni"),
    "__vbaRecAnsiToUni": S(3, kind="ignore"),
    "__vbaRecDestructAnsi": S(2, kind="ignore"),
    # (seen in ChessBrainVB, compiled with optimisation)
    "rtcGetTimer": S(0, fpu_out=True, ret="Single", pure=False,
                     f=lambda a: Var("global", "Timer", "Timer", "Single")),
    "rtcGetDateVar": S(1, kind="varres", pure=False, f=lambda a: Var("global", "Date", "Date", "Date")),
    "rtcSpaceVar": S(2, kind="varres", f=lambda a: Call("Space", [a[1]])),
    "rtcFileDateTime": S(2, kind="varres", pure=False, f=lambda a: Call("FileDateTime", [a[1]])),
    "rtcIsNumeric": S(1, ret="Boolean", f=lambda a: Call("IsNumeric", [deref(a[0])])),
    "__vbaBoolErrVar": S(1, ret="Boolean", f=lambda a: Call("CBool", [deref(a[0])])),
    # (a target a4 = I2ErrVar(), its Variant lost: no spec took the argument)
    # CDate of a Double (its two dwords), a Single, a String
    "__vbaDateR8": S(2, ret="Date", fpu_out=True, f=lambda a: Call("CDate", [double_bits(a[0], a[1])])),
    "__vbaDateR4": S(1, ret="Date", fpu_out=True, f=lambda a: Call("CDate", [a[0]])),
    "__vbaDateStr": S(1, ret="Date", fpu_out=True, f=lambda a: Call("CDate", [a[0]])),
    "__vbaI2ErrVar": S(1, ret="Integer", f=lambda a: Call("CInt", [deref(a[0])])),
    "__vbaI4ErrVar": S(1, ret="Long", f=lambda a: Call("CLng", [deref(a[0])])),
    "__vbaR4ErrVar": S(1, ret="Single", fpu_out=True, f=lambda a: Call("CSng", [deref(a[0])])),
    "__vbaR8ErrVar": S(1, ret="Double", fpu_out=True, f=lambda a: Call("CDbl", [deref(a[0])])),
    "__vbaUI1ErrVar": S(1, ret="Byte", f=lambda a: Call("CByte", [deref(a[0])])),
    "__vbaI4Sgn": S(regs=("ecx",), ret="Long", f=lambda a: Call("Sgn", [a[0]])),
    "__vbaI2Sgn": S(regs=("ecx",), ret="Integer", f=lambda a: Call("Sgn", [a[0]])),
    # st0 -> st0 conversions (an explicit CSng / CDbl of an FPU value)
    "__vbaFpCSngR4": S(fpu_in=1, fpu_out=True, ret="Single", f=lambda a: Call("CSng", [a[0]])),
    "__vbaFpCSngR8": S(fpu_in=1, fpu_out=True, ret="Single", f=lambda a: Call("CSng", [a[0]])),
    "__vbaFpCDblR8": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("CDbl", [a[0]])),
    "__vbaFpCDblR4": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("CDbl", [a[0]])),
    # the math intrinsics optimised code calls on st0
    "_CIsqrt": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Sqr", [a[0]])),
    "_CIatan": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Atn", [a[0]])),
    "_CIcos": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Cos", [a[0]])),
    "_CIsin": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Sin", [a[0]])),
    "_CItan": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Tan", [a[0]])),
    "_CIexp": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Exp", [a[0]])),
    "_CIlog": S(fpu_in=1, fpu_out=True, ret="Double", f=lambda a: Call("Log", [a[0]])),
    "rtcStringVar": S(3, kind="varres", f=lambda a: Call("String", [a[1], deref(a[2])])),
    "rtcVarBstrFromAnsi": S(2, kind="varres", f=lambda a: Call("Chr", [a[1]])),
    "rtcVarBstrFromChar": S(2, kind="varres", f=lambda a: Call("ChrW", [a[1]])),   # ChrW(code)
    "rtcBstrFromChar": S(1, ret="String", f=lambda a: Call("ChrW$", [a[0]])),
    "rtcLeftCharVar": S(3, kind="varres", f=lambda a: Call("Left", [deref(a[1]), a[2]])),
    # rtcDir(&PathName As Variant, Attributes): Dir$(path, attr) - VB passes the
    # path as a VT_BYREF Variant of the String (VBDoom If Dir$(file) <> "")
    # (no pure function: each call moves on, and a register kept over a join printed the call twice;
    # what they say depends on what happened to the file since: FileLen, LOF)
    "rtcDir": S(2, ret="String", pure=False, f=lambda a: Call("Dir$", [deref(a[0]), a[1]], vbtype="String")),
    "rtcRightCharVar": S(3, kind="varres", f=lambda a: Call("Right", [deref(a[1]), a[2]])),
    "rtcMidCharVar": S(4, kind="varres", f=lambda a: Call("Mid", [deref(a[1]), a[2], deref(a[3])])),
    "rtcLowerCaseVar": S(2, kind="varres", f=lambda a: Call("LCase", [deref(a[1])])),
    "rtcUpperCaseVar": S(2, kind="varres", f=lambda a: Call("UCase", [deref(a[1])])),
    "rtcTrimVar": S(2, kind="varres", f=lambda a: Call("Trim", [deref(a[1])])),
    # more Variant forms: the result through the first argument (stack sizes
    # from the runtime: rtcAbsVar ret 8 = result + one Variant*); a target
    # printed AbsVar / IntVar / LeftTrimVar... for them
    "rtcAbsVar": S(2, kind="varres", f=lambda a: Call("Abs", [deref(a[1])])),
    # the operator forms of Abs / Int / Fix on a Variant (as __vbaVarNeg)
    "__vbaVarAbs": S(2, kind="varres", f=lambda a: Call("Abs", [deref(a[1])])),
    "__vbaVarInt": S(2, kind="varres", f=lambda a: Call("Int", [deref(a[1])])),
    "__vbaVarFix": S(2, kind="varres", f=lambda a: Call("Fix", [deref(a[1])])),
    # a ByRef Variant parameter's Variant: __vbaVargVar(ecx = the parameter, &temp) returns a
    # pointer to it; what it pushes is only the temporary (a target PrintBox:
    # .Line (Empty, Empty)-...; t77 o.Kaksi a copies the 16 bytes through that pointer)
    "__vbaVargVar": S(1, regs=("ecx",), f=lambda a: a[0]),
    "rtcFixVar": S(2, kind="varres", f=lambda a: Call("Fix", [deref(a[1])])),
    "rtcIntVar": S(2, kind="varres", f=lambda a: Call("Int", [deref(a[1])])),
    "rtcLeftTrimVar": S(2, kind="varres", f=lambda a: Call("LTrim", [deref(a[1])])),
    "rtcRightTrimVar": S(2, kind="varres", f=lambda a: Call("RTrim", [deref(a[1])])),
    "rtcOctVarFromVar": S(2, kind="varres", f=lambda a: Call("Oct", [deref(a[1])])),
    "rtcVarDateFromVar": S(2, kind="varres", f=lambda a: Call("CVDate", [deref(a[1])])),
    "rtcVarStrFromVar": S(2, kind="varres", f=lambda a: Call("Str", [deref(a[1])])),
    "rtcLenVar": S(2, kind="varres", f=lambda a: Call("LenB", [deref(a[1])])),
    "rtcCVErrFromVar": S(2, kind="varres", f=lambda a: Call("CVErr", [deref(a[1])])),
    "rtcEnvironVar": S(2, kind="varres", f=lambda a: Call("Environ", [deref(a[1])])),
    "rtcVarFromError": S(2, kind="varres", f=lambda a: Call("Error", [deref(a[1])])),
    "rtcVarBstrFromByte": S(2, kind="varres", f=lambda a: Call("ChrB", [a[1]])),
    "rtcLeftVar": S(3, kind="varres", f=lambda a: Call("LeftB", [deref(a[1]), a[2]])),
    "rtcRightVar": S(3, kind="varres", f=lambda a: Call("RightB", [deref(a[1]), a[2]])),
    "rtcMidVar": S(4, kind="varres", f=lambda a: Call("MidB", [deref(a[1]), a[2], deref(a[3])])),
    "rtcCommandVar": S(1, kind="varres", f=lambda a: Call("Command", [])),
    "rtcGetTimeVar": S(1, kind="varres", f=lambda a: Call("Time", [])),
    # StrPtr and ObjPtr compile to the same VarPtr export: the argument tells
    "VarPtr": S(1, ret="Long", kind="varptr"),
    # obj.Print items: (&descriptor, obj, item...) cdecl; descriptor = u16 count,
    # then one type byte per item (VT | 0x80 on the item that ends the line)
    "__vbaPrintObj": S(cdecl=True, kind="print"),
    "__vbaInputFile": S(0, kind="inputfile", cdecl=True, pure=False),
    "__vbaPrintFile": S(cdecl=True, kind="printfile"),
    "__vbaWriteFile": S(cdecl=True, kind="printfile"),
    # ---- file I/O (t05m FileTest)
    # __vbaFileOpen(mode, reclen, filenum, filename); mode 1 Input, 2 Output,
    # 4 Random, 8 Append, 0x20 Binary; reclen -1 when not given
    "__vbaFileOpen": S(4, kind="stmtcall", f=lambda a: file_open(a)),
    # __vbaFileSeek(position, file number): Seek #n, position (VBDoom WriteWavHeader)
    "__vbaFileSeek": S(2, kind="stmtcall", f=lambda a: stmt("Seek #{0}, {1}", [a[1], a[0]])),
    "__vbaEnd": S(0, kind="stmtcall", f=lambda a: stmt("End", [])),
    "__vbaError": S(1, kind="stmtcall", f=lambda a: stmt("Error {0}", [a[0]])),
    "rtcCommandBstr": S(0, ret="String", f=lambda a: Var("global", "Command$", "Command$")),
    "rtcGetErl": S(0, ret="Long", f=lambda a: Var("global", "Erl", "Erl")),
    # binary file I/O (VBDoom): (bytes or a type descriptor, &var, [record,]
    # file number); 3: no record number, Owner: arrays, UDTs, Variants
    # (cb, &v, file[, rec]): cb the variable's byte count
    "__vbaPut3": S(3, kind="stmtcall", f=lambda a: put_get("Put", a[1], a[2], size=a[0])),
    "__vbaGet3": S(3, kind="stmtcall", f=lambda a: put_get("Get", a[1], a[2], size=a[0])),
    "__vbaPut4": S(4, kind="stmtcall", f=lambda a: put_get("Put", a[1], a[3], a[2], a[0])),
    "__vbaGet4": S(4, kind="stmtcall", f=lambda a: put_get("Get", a[1], a[3], a[2], a[0])),
    "__vbaPutOwner3": S(3, kind="stmtcall", f=lambda a: put_get("Put", a[1], a[2])),
    "__vbaGetOwner3": S(3, kind="stmtcall", f=lambda a: put_get("Get", a[1], a[2])),
    "__vbaPutOwner4": S(4, kind="stmtcall", f=lambda a: put_get("Put", a[1], a[3], a[2])),
    "__vbaGetOwner4": S(4, kind="stmtcall", f=lambda a: put_get("Get", a[1], a[3], a[2])),
    # a String * n (n first, as the byte count of __vbaPut3/4; calls.py
    # declares the variable String * n: a Random file holds no length then)
    "__vbaGetFxStr3": S(3, kind="stmtcall", f=lambda a: put_get("Get", a[1], a[2])),
    "__vbaGetFxStr4": S(4, kind="stmtcall", f=lambda a: put_get("Get", a[1], a[3], a[2])),
    "__vbaPutFxStr3": S(3, kind="stmtcall", f=lambda a: put_get("Put", a[1], a[2])),
    "__vbaPutFxStr4": S(4, kind="stmtcall", f=lambda a: put_get("Put", a[1], a[3], a[2])),
    "__vbaFileClose": S(1, kind="stmtcall", f=lambda a: stmt("Close #{0}", [a[0]])),
    "__vbaFileCloseAll": S(0, kind="stmtcall", f=lambda a: stmt("Close", [])),
    "__vbaLineInputStr": S(2, kind="stmtcall", f=lambda a: line_input_str(a)),
    "__vbaLineInputVar": S(2, kind="stmtcall", f=lambda a: stmt("Line Input #{1}, {0}",
                                                               [deref(a[0]), a[1]])),
    # not pure: the next free number changes once a file is open
    "rtcFreeFile": S(1, ret="Integer", pure=False,
                     f=lambda a: Call("FreeFile", opt_args([deref(a[0])]))),
    "rtcEndOfFile": S(1, ret="Boolean", f=fn("EOF", 0)),
    "rtcFileLength": S(1, ret="Long", pure=False, f=fn("LOF", 0)),
    # (the generic arity from MSVBVM60's ret N gave FileLen and Str$ a second
    # argument: a target "Wrong number of arguments")
    "rtcFileLen": S(1, ret="Long", pure=False, f=fn("FileLen", 0)),
    # Name oldpath As newpath: __vbaNameFile(new, old) (tests/t20misc)
    "__vbaNameFile": S(2, kind="stmtcall", pure=False,
                       f=lambda a: stmt("Name {0} As {1}", [a[1], a[0]])),
    # InputBox(prompt, title, default, xpos, ypos, helpfile, context): seven
    # Variants by address (the generic arity left them undecoded temporaries)
    "rtcInputBox": S(7, ret="String", pure=False,
                     f=lambda a: Call("InputBox", [deref(x) for x in a], vbtype="String")),
    # Shell(pathname, windowstyle): the task ID comes back on the FPU stack
    # (a Double; a target `v30 = '<st0>` after the call)
    "rtcShell": S(2, fpu_out=True, ret="Double", plain=(1,), pure=False,
                  f=lambda a: Call("Shell", [deref(a[0]), a[1]], vbtype="Double")),
    # For v = start To end Step step with a Variant counter (tests/t18varfor):
    # __vbaVarForInit(&v, &state1, &state2, &start, &end, &step) and
    # __vbaVarForNext(&v, &state1, &state2) return 0 once the loop is done;
    # structure.as_varfor makes the For of them
    "__vbaVarForInit": S(6, plain=(0, 1, 2), ret="Long", pure=False,
                         f=lambda a: Call("VarForInit", list(a))),
    "__vbaVarForNext": S(3, plain=(0, 1, 2), ret="Long", pure=False,
                         f=lambda a: Call("VarForNext", list(a))),
    # For v = start To end Step step with a Single counter: the check after
    # each step, __vbaR4ForNextCheck(counter, end, step) (each a Single's
    # dword) - nonzero while the loop goes on (a target Proc_789740)
    "__vbaR4ForNextCheck": S(3, ret="Boolean", f=lambda a: r4_for_check(*a[:3])),
    # (a Double's two dwords each: counter, end, step)
    "__vbaR8ForNextCheck": S(6, ret="Boolean", f=lambda a: r4_for_check(
        a[0], double_bits(a[2], a[3]), double_bits(a[4], a[5]))),
    "rtcStrFromVar": S(1, ret="String", f=lambda a: Call("Str$", [deref(a[0])])),
    "rtcKillFiles": S(1, kind="stmtcall", f=lambda a: stmt("Kill {0}", [deref(a[0])])),
    "rtcEnvironBstr": S(1, ret="String", f=lambda a: Call("Environ$", [deref(a[0])])),
    # rtcReplace(expr, find, replace, start, count, compare): trailing defaults dropped
    "rtcReplace": S(6, ret="String", f=lambda a: Call("Replace", trailing_defaults(
        list(a), {3: 1, 4: -1, 5: 0}))),
    "rtcStringBstr": S(2, ret="String", f=lambda a: Call("String$", [a[0], deref(a[1])])),
    # rtcBstrFromFormatVar(&expr, &format, firstdayofweek, firstweekofyear)
    "rtcBstrFromFormatVar": S(4, ret="String", f=lambda a: Call(
        "Format$", [deref(a[0]), deref(a[1])] + ([] if all(
            isinstance(x, Const) and x.value == 1 for x in a[2:4]) else list(a[2:4])))),
    # (&result, &expr, &format, firstday, firstweek): Format(...) as a Variant
    "rtcVarFromFormatVar": S(5, kind="varres", f=lambda a: Call(
        "Format", [deref(a[1]), deref(a[2])] + ([] if all(
            isinstance(x, Const) and x.value == 1 for x in a[3:5]) else list(a[3:5])))),
    # (&result, &v, conversion, LCID)
    "rtcStrConvVar2": S(4, kind="varres", f=lambda a: Call(
        "StrConv", [deref(a[1]), a[2]] + ([] if isinstance(a[3], Const) and a[3].value == 0
                                          else [a[3]]))),
    "DllFunctionCall": S(1, kind="ignore"),
    "_allmul": S(4, ret="Long", f=lambda a: BinOp("*", a[0], a[2])),
}


def err_object():
    """The Err object, typed with the VBA library's ErrObject interface."""
    from .typelib import Registry
    v = Var("global", "Err", "Err")
    for lib in Registry.libs:
        t = lib.by_name.get("errobject")
        if t is not None and t.kind == 5 and t.impls:
            v.iface = lib.resolve_href(t.impls[0][0])
            break
        t = lib.by_name.get("_errobject")
        if t is not None:
            v.iface = t
            break
    return v


def byval_variant(a, i):
    """A Variant passed by value: four dwords, its tag first.  The tag reads as the variable or the
    literal (variant_arg); an omitted argument is VT_ERROR with DISP_E_PARAMNOTFOUND two dwords on."""
    from .ir import Missing
    v = a[i + 2]
    if isinstance(a[i], Missing) or isinstance(v, Const) and isinstance(v.value, int) and \
            v.value & 0xffffffff == 0x80020004:
        return Missing()
    if isinstance(a[i], Const) and a[i].value == 8:
        return v                # (a String's tag: t94rtc GetSetting(a, b, c, "none"))
    return a[i]


def opt_ref(p):
    """&Variant for an Optional argument: Missing when it was left out."""
    from .ir import Missing
    return p if isinstance(p, Missing) else deref(p)


def finance(name, doubles, optional):
    """rtcPMT and the like: ByVal Doubles (two dwords each), then Optional Variants by reference;
    the result on the FPU."""
    def h(a):
        return Call(name, opt_args([dword_pair(a[2 * i], a[2 * i + 1], "Double") for i in range(doubles)] +
                                   [opt_ref(x) for x in a[2 * doubles:2 * doubles + optional]]))
    return S(2 * doubles + optional, fpu_out=True, ret="Double", plain=tuple(range(2 * doubles)), f=h)


def mid_stmt_var(name):
    """__vbaMidStmtVar(&value, count, start, &target); count &H3FFFFFFF: left out."""
    def h(a):
        all_of = isinstance(a[1], Const) and a[1].value == 0x3fffffff
        return stmt(name + ("({0}, {1}) = {2}" if all_of else "({0}, {1}, {3}) = {2}"),
                    [deref(a[3]), a[2], deref(a[0]), a[1]])
    return h


def file_lock(name):
    """__vbaFileLock(mode, last, first, file): mode 0 Lock whole, 1 Unlock whole, 2 and 3 a range."""
    def h(a):
        m = a[0].value if isinstance(a[0], Const) else 0
        word = "Unlock" if m & 1 else "Lock"
        if not m & 2:
            return stmt(word + " #{0}", [a[3]])
        if repr(a[1]) == repr(a[2]):
            return stmt(word + " #{0}, {1}", [a[3], a[1]])
        return stmt(word + " #{0}, {2} To {1}", [a[3], a[1], a[2]])
    return h


def cy_arg(a):
    return dword_pair(a[0], a[1], "Currency")


SPECS.update({
    # ---- the registry: Variants by value
    "rtcGetSetting": S(7, ret="String", pure=False, f=lambda a: Call(
        "GetSetting", opt_args([a[0], a[1], a[2], byval_variant(a, 3)]))),
    "rtcDeleteSetting": S(9, pure=False, f=lambda a: Call(
        "DeleteSetting", opt_args([a[0], byval_variant(a, 1), byval_variant(a, 5)]))),
    "rtcGetAllSettings": S(3, kind="varres", pure=False, f=lambda a: Call("GetAllSettings", [a[1], a[2]])),
    # ---- arrays and lists
    "rtcFilter": S(5, kind="varres", f=lambda a: Call("Filter", trailing_defaults(
        [deref(a[1]), a[2], a[3], a[4]], {2: -1, 3: 0}))),
    "rtcPartition": S(5, kind="varres", f=lambda a: Call(
        "Partition", [deref(a[1]), deref(a[2]), deref(a[3]), deref(a[4])])),
    "rtcQBColor": S(1, ret="Long", f=fn("QBColor", 0)),
    "rtcMonthName": S(2, ret="String", f=lambda a: Call("MonthName", trailing_defaults(list(a), {1: 0}))),
    "rtcWeekdayName": S(3, ret="String", f=lambda a: Call("WeekdayName", trailing_defaults(
        list(a), {1: 0, 2: 0}))),
    "rtcGetDateValue": S(2, kind="varres", f=lambda a: Call("DateValue", [a[1]])),
    "rtcGetTimeValue": S(2, kind="varres", f=lambda a: Call("TimeValue", [a[1]])),
    # ---- objects
    "rtcCallByName": S(6, kind="varres", plain=(1, 2, 3, 4, 5), pure=False, f=lambda a: paramarray_call(
        "CallByName", [a[1], a[2], a[3], arr(a[4])])),
    "rtcGetObject": S(3, kind="varres", pure=False, f=lambda a: Call(
        "GetObject", opt_args([opt_ref(a[1]), opt_ref(a[2])]))),
    # For Each on an array: (VARTYPE, &state, &state, &element, array): structure.as_foreach
    "__vbaForEachAry": S(5, ret="Long", plain=(0, 1, 2, 3, 4), pure=False, f=lambda a: Call("ForEachAry", list(a))),
    "__vbaNextEachAry": S(4, ret="Long", plain=(0, 1, 2, 3), pure=False, f=lambda a: Call("NextEachAry", list(a))),
    # ---- files
    "rtcInputCharCountVar": S(3, kind="varres", pure=False, f=lambda a: Call("Input", [a[1], a[2]])),
    "rtcInputCount": S(2, ret="String", pure=False, f=lambda a: Call("InputB$", [a[0], a[1]])),
    "rtcInputCountVar": S(3, kind="varres", pure=False, f=lambda a: Call("InputB", [a[1], a[2]])),
    "__vbaFileLock": S(4, kind="stmtcall", f=lambda a: file_lock("")(a)),
    # ---- strings
    "__vbaInStrB": S(4, ret="Long", f=lambda a: Call("InStrB", [a[3], a[2], a[1]] + (
        [] if isinstance(a[0], Const) and a[0].value == 0 else [a[0]]))),
    # (&result, compare, &find, &s, start)
    "__vbaInStrVarB": S(5, kind="varres", plain=(1, 4), f=lambda a: Call("InStrB", [a[4], deref(a[3]), deref(a[2])] + (
        [] if isinstance(a[1], Const) and a[1].value == 0 else [a[1]]))),
    "rtcLeftBstr": S(2, ret="String", f=fn("LeftB$", 0, 1)),
    "rtcRightBstr": S(2, ret="String", f=fn("RightB$", 0, 1)),
    "rtcMidBstr": S(3, ret="String", f=lambda a: Call("MidB$", [a[0], a[1]] + opt_args([opt_ref(a[2])]))),
    "__vbaStrCompVar": S(4, kind="varres", f=lambda a: Call("StrComp", [deref(a[3]), deref(a[2]), a[1]])),
    "__vbaMidStmtVar": S(4, kind="stmtcall", f=mid_stmt_var("Mid")),
    "__vbaMidStmtVarB": S(4, kind="stmtcall", f=mid_stmt_var("MidB")),
    "__vbaMidStmtBstrB": S(5, kind="stmtcall", f=lambda a: stmt(
        "MidB$({0}, {1}) = {2}" if isinstance(a[2], Const) and a[2].value in (-1, 0xffffffff)
        else "MidB$({0}, {1}, {3}) = {2}", [deref(a[4]), a[3], a[1], a[2]])),
    # ---- Variant operators
    "__vbaVarPow": S(3, kind="varres", f=varop("^")),
    "__vbaVarEqv": S(3, kind="varres", f=varop("Eqv")),
    "__vbaVarImp": S(3, kind="varres", f=varop("Imp")),
    # ---- Decimal and Currency
    "rtDecFromVar": S(2, kind="varres", f=lambda a: Call("CDec", [deref(a[1])])),
    "__vbaCyUI1": S(1, ret="Currency", kind="cy64", f=lambda a: Call("CCur", [a[0]])),
    "__vbaCyStr": S(1, ret="Currency", kind="cy64", f=lambda a: Call("CCur", [a[0]])),
    "__vbaCyAbs": S(2, ret="Currency", kind="cy64", f=lambda a: Call("Abs", [cy_arg(a)])),
    "__vbaCyFix": S(2, ret="Currency", kind="cy64", f=lambda a: Call("Fix", [cy_arg(a)])),
    "__vbaCyInt": S(2, ret="Currency", kind="cy64", f=lambda a: Call("Int", [cy_arg(a)])),
    "__vbaCySgn": S(2, ret="Integer", f=lambda a: Call("Sgn", [cy_arg(a)])),
    "__vbaI2Cy": S(2, ret="Integer", f=lambda a: Call("CInt", [cy_arg(a)])),
    "__vbaUI1Cy": S(2, ret="Byte", f=lambda a: Call("CByte", [cy_arg(a)])),
    "__vbaR4Cy": S(2, fpu_out=True, ret="Single", f=lambda a: Call("CSng", [cy_arg(a)])),
    "__vbaStrDate": S(2, ret="String", f=lambda a: Call("CStr", [dword_pair(a[0], a[1], "Date")])),
    "__vbaCyErrVar": S(1, ret="Currency", kind="cy64", f=lambda a: Call("CCur", [deref(a[0])])),
    # For on a Currency: 0 once the loop is done.  Laid out like __vbaVarForInit for structure.as_varfor:
    # (&counter, end, step, start, end, step) / (&counter, end, step)
    "__vbaCyForInit": S(7, plain=(0, 1, 2, 3, 4, 5, 6), ret="Long", pure=False, f=lambda a: Call(
        "CyForInit", [a[0], dword_pair(a[3], a[4], "Currency"), dword_pair(a[5], a[6], "Currency"),
                      dword_pair(a[1], a[2], "Currency"), dword_pair(a[3], a[4], "Currency"),
                      dword_pair(a[5], a[6], "Currency")])),
    "__vbaCyForNext": S(5, plain=(0, 1, 2, 3, 4), ret="Long", pure=False, f=lambda a: Call(
        "CyForNext", [a[0], dword_pair(a[1], a[2], "Currency"), dword_pair(a[3], a[4], "Currency")])),
    "__vbaI4ForNextCheck": S(3, ret="Boolean", f=lambda a: r4_for_check(*a[:3])),
    "__vbaI2ForNextCheck": S(3, ret="Boolean", f=lambda a: r4_for_check(*a[:3])),
    # ---- finance
    "rtcPMT": finance("Pmt", 3, 2),
    "rtcIPMT": finance("IPmt", 4, 2),
    "rtcPPMT": finance("PPmt", 4, 2),
    "rtcFV": finance("FV", 3, 2),
    "rtcPV": finance("PV", 3, 2),
    "rtcNPer": finance("NPer", 3, 2),
    "rtcRate": finance("Rate", 3, 3),
    "rtcDDB": finance("DDB", 4, 1),
    "rtcSLN": finance("SLN", 3, 0),
    "rtcSYD": finance("SYD", 4, 0),
    "rtcNPV": S(3, fpu_out=True, ret="Double", plain=(0, 1, 2), f=lambda a: Call(
        "NPV", [dword_pair(a[0], a[1], "Double"), arr(a[2])])),
    "rtcIRR": S(2, fpu_out=True, ret="Double", plain=(0,), f=lambda a: Call(
        "IRR", opt_args([arr(a[0]), opt_ref(a[1])]))),
    "rtcMIRR": S(5, fpu_out=True, ret="Double", plain=(0, 1, 2, 3, 4), f=lambda a: Call(
        "MIRR", [arr(a[0]), dword_pair(a[1], a[2], "Double"), dword_pair(a[3], a[4], "Double")])),
})

# Option Compare Text: the same operations in another function; the module says Option Compare
# Text (writer), so they read the same (SETUP1 printed StrTextCmp("\\", Right$(v2C, 1)))
for _t, _n in (("__vbaStrTextCmp", "__vbaStrCmp"), ("__vbaStrTextLike", "__vbaStrLike"),
               ("__vbaVarTextLike", "__vbaVarLike"), ("__vbaVarTextLikeVar", "__vbaVarLikeVar")):
    SPECS[_t] = SPECS[_n]
for _o in ("Eq", "Ne", "Lt", "Le", "Gt", "Ge"):
    SPECS["__vbaVarTextTst" + _o] = SPECS["__vbaVarTst" + _o]
    SPECS["__vbaVarTextCmp" + _o] = SPECS["__vbaVarCmp" + _o]


def get(name):
    return SPECS.get(name)
