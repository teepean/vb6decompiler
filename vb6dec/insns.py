"""Per-instruction semantics for the symbolic executor.

Each handler mutates the machine State and may append statements.  The
executor in lift.py deals with calls and branches; everything else lands
here.
"""
from capstone import x86
import copy

from .ir import Const, Var, BinOp, UnOp, Unknown, Assign
from .machine import SP, Hi, FSW, REG32, HIGH8, add, sub, s32, mul_const
from . import ir


class SignOf(ir.Expr):
    """Pieces of the signed power-of-two division idiom."""

    def __init__(self, x, mask=None, biased=False):
        self.x, self.mask, self.biased = x, mask, biased

    def key(self):
        return ("sign", self.x.key() if hasattr(self.x, "key") else id(self.x), self.mask, self.biased)

    def children(self):
        return (self.x,)

    CORE = ("x", "mask", "biased")

    def _rebuild(self, g):
        # (without it substitution could not reach x, which walk() shows:
        # Lifter.parallel_copy then broke the same cycle forever - ChessBrainVB
        # Eval never finished lifting)
        return self._keep(SignOf(g(self.x), self.mask, self.biased))

    def __repr__(self):
        return "SignOf(%r,%s,%s)" % (self.x, self.mask, self.biased)

X = x86

ARITH = {"add": "+", "sub": "-", "imul": "*", "and": "And", "or": "Or", "xor": "Xor"}
FARITH = {"fadd": "+", "faddp": "+", "fsub": "-", "fsubp": "-", "fsubr": "-",
          "fsubrp": "-", "fmul": "*", "fmulp": "*", "fdiv": "/", "fdivp": "/",
          "fdivr": "/", "fdivrp": "/"}
# st(0) op= an integer in memory, converted (VBDoom FixedMul: CDbl(a And &HFFFF&) *
# CDbl(b) is fild of the one, fimul of the other)
FIARITH = {"fiadd": "+", "fisub": "-", "fisubr": "-", "fimul": "*", "fidiv": "/",
           "fidivr": "/"}
REVERSED_F = {"fsubr", "fsubrp", "fdivr", "fdivrp", "fisubr", "fidivr"}


def width(op):
    return op.size


FP_FUNCTIONS = {"fsqrt": "Sqr", "fsin": "Sin", "fcos": "Cos", "fptan": "Tan"}
FP_CONSTANTS = {"fldln2": 0.693147180559945309, "fldlg2": 0.301029995663981195,
                "fldpi": 3.14159265358979324, "fldl2e": 1.44269504088896341,
                "fldl2t": 3.32192809488736235}


class FpConst(Const):
    """A constant the FPU loads itself; what fyl2x makes of it depends on which one it is."""

    def __init__(self, name):
        Const.__init__(self, FP_CONSTANTS[name], "Double")
        self.name = name


def run(lf, st, ins, stmts):
    """Execute one non-call, non-branch instruction."""
    m = ins.mnemonic
    ops = ins.operands

    if m in ("nop", "fnclex", "fwait", "wait", "cld", "hlt", "int3"):
        return
    if m.startswith(("rep ", "repe ", "repne ")):
        m = m.split(None, 1)[1]
    if m in ("stosd", "stosw", "stosb", "movsd", "movsw", "movsb"):
        # VB zero-fills struct locals with "rep stosd"; the locals are zero
        # anyway, so only the registers it consumes need updating
        n = st.get("ecx")
        step = {"d": 4, "w": 2, "b": 1}[m[-1]]
        if ins.prefix and 0xf3 in ins.prefix and isinstance(n, Const):
            if m.startswith("movs") and n.value:
                # a block copy of a UDT (a Function's UDT result into the
                # caller's buffer, r = s between UDTs): LSet, like __vbaCopyBytes
                from .ir import ExprStmt, Call, Return, AddrOf, Var as V
                dst, src = lf.frame_ptr(st.get("edi")), lf.frame_ptr(st.get("esi"))
                mudt = getattr(lf, "mudt", None)
                if mudt is not None and dst == V("arg", 4 + lf.p.ret_pop):
                    pass                    # a method's UDT result: the result slot
                elif getattr(lf.p, "udt_ret", False) and dst == V("arg", 4):
                    cp = Return()           # Function F() As TRec: F = r
                    cp.value = src.e if isinstance(src, AddrOf) else src
                else:
                    cp = ExprStmt(Call("LSet", [dst, src, Const(step * n.value)],
                                       raw="__vbaCopyBytes"))
                if mudt is None or dst != V("arg", 4 + lf.p.ret_pop):
                    cp.va = ins.address
                    stmts.append(cp)
            st.set("edi", add(st.get("edi"), Const(step * n.value)))
            if m.startswith("movs"):
                st.set("esi", add(st.get("esi"), Const(step * n.value)))
            st.set("ecx", Const(0))
        else:
            d = st.get("edi")
            if m.startswith("movs") and isinstance(d, SP):
                # one unit copied onto the stack: an argument built in place
                # (unoptimised code passes a ByVal Variant to a cdecl helper as
                # "mov eax, 10h; call __vbaChkstk; mov edi, esp; 4 x movsd":
                # VBDoom InitPars v(i), __vbaVarIndexLoad's index)
                s = st.get("esi")
                if isinstance(s, SP):
                    src = ("stack", s.off)
                elif isinstance(s, BinOp) and s.op == "+" and isinstance(s.b, Const):
                    src = ("mem", s.a, s.b.value, None, 1)  # (the next dword of a parameter)
                else:
                    src = ("mem", s, 0, None, 1)
                lf.store(st, ("stack", d.off), lf.load(st, src, step), step, stmts)
            st.set("edi", add(st.get("edi"), Const(step)))
            if m.startswith("movs"):
                st.set("esi", add(st.get("esi"), Const(step)))
        return
    if m == "push":
        st.push(lf.rd(st, ops[0]))
        return
    if m == "leave":
        st.set("esp", st.get("ebp"))        # mov esp, ebp; pop ebp (unoptimised code)
        st.set("ebp", st.pop())
        return
    if m == "pop":
        v = st.pop()
        lf.wr(st, ops[0], v, stmts)
        return
    if m in ("mov", "movsx", "movzx"):
        v = lf.rd(st, ops[1])
        if m != "mov" and ops[1].size < 4:
            v = coerce(v, ops[1].size, signed=(m == "movsx"))
        lf.wr(st, ops[0], v, stmts)
        return
    if m == "lea":
        st.set(REG32[ops[0].reg], lf.addr_expr(st, ops[1].mem))
        return
    if m == "xchg":
        a, b = lf.rd(st, ops[0]), lf.rd(st, ops[1])
        lf.wr(st, ops[0], b, stmts)
        lf.wr(st, ops[1], a, stmts)
        return
    if m == "imul" and len(ops) == 1:
        # edx:eax = eax * op (an overflow-checked Long multiply; edx unused)
        a, b = st.get("eax"), lf.rd(st, ops[0])
        r = Const(fold(m, a.value, b.value)) if isinstance(a, Const) and isinstance(b, Const) \
            else BinOp("*", a, b)
        st.flags = ("arith", r, m)
        st.set("eax", r)
        st.set("edx", Hi(r))
        return
    if m == "imul" and len(ops) == 3:
        a, b = lf.rd(st, ops[1]), lf.rd(st, ops[2])
        r = Const(fold(m, a.value, b.value)) if isinstance(a, Const) and isinstance(b, Const) \
            else mul_const(a, b.value) if isinstance(b, Const) and isinstance(b.value, int) \
            else BinOp("*", a, b)
        if isinstance(r, BinOp):
            r.width = 4 if ops[0].size == 4 else 2
        st.flags = ("arith", r, m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m in ARITH:
        lf.whole_element = ops[0].type == X.X86_OP_MEM
        a, b = lf.rd(st, ops[0]), lf.rd(st, ops[1])
        lf.whole_element = False
        if m == "and" and ops[0].type == X.X86_OP_MEM and ops[0].size < 4 and isinstance(a, ir.Index) and \
                isinstance(b, Const) and isinstance(b.value, int) and isinstance(a.arr, Var) and \
                (lf.arrays.get(a.arr.key()) or {}).get("size") in (2, 4):
            # (and byte ptr [p + i*4], 0FEh: the element's other bytes stay)
            mask_ = (1 << 8 * ops[0].size) - 1
            b = Const(s32((b.value & mask_) | (0xffffffff ^ mask_)))
        if m == "and" and isinstance(a, SignOf) and isinstance(b, Const):
            lf.wr(st, ops[0], SignOf(a.x, b.value), stmts)        # sign adjustment
            return
        if m == "add" and isinstance(b, SignOf) and b.mask is not None and b.x == a:
            lf.wr(st, ops[0], SignOf(a, b.mask, biased=True), stmts)
            return
        if m == "xor" and isinstance(b, SignOf) and b.mask is None and not b.biased and \
                b.x == a:
            lf.wr(st, ops[0], SignOf(a, "abs"), stmts)    # cdq; xor eax, edx: half of Abs
            return
        if m == "sub" and isinstance(a, SignOf) and a.mask == "abs" and \
                isinstance(b, SignOf) and b.mask is None and b.x == a.x:
            r = ir.Call("Abs", [a.x], pure=True)          # ...; sub eax, edx: Abs(x)
            st.flags = ("arith", r, m)
            lf.wr(st, ops[0], r, stmts)
            return
        if m == "sub" and isinstance(b, SignOf) and b.mask is None and b.x == a:
            # cdq; sub eax, edx: x - (-1 if x < 0): the bias for x \ 2
            lf.wr(st, ops[0], SignOf(a, 1, biased=True), stmts)
            return
        if m == "and" and isinstance(b, Const) and b.value in (0xfffffffe, -2, 1) and \
                (isinstance(a, ir.Me) or isinstance(a, Var) and a.space == "arg" and a.off == 4
                 and lf.obj is not None):
            # VB tags the implicit Me argument in bit 0 (only Me: "x And 1" is real
            # code - in a module the first argument is a parameter: VBDoom SCRAMBLE)
            lf.wr(st, ops[0], a if b.value != 1 else Const(0), stmts)
            return
        if m in ("and", "or", "xor") and ops[0].type == X.X86_OP_REG and ops[0].size < 4 and \
                not __import__("os").environ.get("T_BYTE") and \
                ops[0].reg not in HIGH8 and isinstance(b, Const) and isinstance(b.value, int):
            # "and al, 0FEh": only the low byte changes (the compiler's short form
            # of And &HFFFFFFFE on a Long, gh_zip pvGetCrc32Table)
            keep = 0xffffff00 if ops[0].size == 1 else 0xffff0000
            low = b.value & (0xff if ops[0].size == 1 else 0xffff)
            if m == "or" and ops[0].size == 2 and low == 0xffff:
                # "or ax, 0FFFFh": a short mov ax, -1, whatever ax held (Rnd(-bt - 1) printed
                # Rnd((global_856014 Or 65535) - bt), Type mismatch)
                r = Const(-1)
                st.flags = ("arith", r, m)
                lf.wr(st, ops[0], r, stmts)
                return
            small = isinstance(a, ir.Deref) and a.size <= ops[0].size or \
                isinstance(a, Var) and any(w <= ops[0].size and not fp_ for w, fp_ in
                                           lf.sizes.get(a.key(), ())) and \
                not any(w > ops[0].size for w, fp_ in lf.sizes.get(a.key(), ()))
            # a Byte / Integer value: its upper bits are not part of it
            imm = s32((low | keep) if m == "and" and not small else
                      (low if ops[0].size == 1 or low < 0x8000 or not small else low - 0x10000))
            r = Const(fold(m, a.value, imm)) if isinstance(a, Const) and \
                isinstance(a.value, int) else BinOp(ARITH[m], a, Const(imm))
            # (the flags are the byte's, not the register's: and al, 3; jne)
            st.flags = ("arith", BinOp("And", a, Const(low)) if m == "and" and isinstance(r, BinOp) else r, m)
            lf.wr(st, ops[0], r, stmts)
            return
        full = (1 << 8 * ops[0].size) - 1
        if m == "xor" and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG \
           and ops[0].reg == ops[1].reg:
            r = Const(0)
        elif m in ("and", "or") and ops[0].type == X.X86_OP_MEM and isinstance(b, Const) and \
                isinstance(b.value, int) and b.value & full == (0 if m == "and" else full):
            # "and [x], 0" / "or [x], -1": the short stores of 0 and all ones in
            # unoptimised code (VBDoom "or word ptr [logopen], 0FFFFh": True)
            r = Const(0 if m == "and" else -1 if ops[0].size > 1 else 0xff)
        elif m == "sub" and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG \
                and ops[0].reg == ops[1].reg:
            r = Const(0)
        elif m == "or" and isinstance(b, Const) and ops[0].size == 4 and \
                b.value & 0xffffffff == 0xffffffff:
            r = Const(-1)                   # "or eax, -1": a short mov eax, -1
        elif m == "add":
            r = add(a, b)
        elif m == "sub":
            r = sub(a, b)
        elif isinstance(a, Const) and isinstance(b, Const):
            r = Const(fold(m, a.value, b.value))
        else:
            r = BinOp(ARITH[m], a, b)
        if isinstance(r, BinOp) and m in ("add", "sub", "imul"):
            r.width = 4 if ops[0].size == 4 else 2      # 32-bit arithmetic: the operands are Longs
        st.flags = ("arith", r, m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m == "sbb" and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG and \
            ops[0].reg == ops[1].reg and st.flags and st.flags[0] == "arith" and \
            st.flags[2] == "neg":
        # "neg r; sbb r, r" is -CF, and neg sets CF when r was not 0: that is
        # VB's -1/0 for r <> 0
        r = lf.boolify(BinOp("<>", st.flags[1], Const(0)))
        st.flags = ("arith", r, m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m == "sbb" and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_REG and \
            ops[0].reg == ops[1].reg and st.flags and st.flags[0] == "cmp":
        # "cmp a, b; sbb r, r" is -CF: VB's -1/0 for a < b (a target Compute:
        # a4 = a3 And a2 < a1 printed a broken condition, then Byte overflow)
        r = lf.boolify(BinOp("<", st.flags[1], st.flags[2]))
        st.flags = ("arith", r, m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m == "neg":
        lf.whole_element = ops[0].type == X.X86_OP_MEM
        a = lf.rd(st, ops[0])
        lf.whole_element = False
        r = a.a if isinstance(a, UnOp) and a.op == "-" else UnOp("-", a)
        st.flags = ("arith", r, m)
        if ops[0].type == X.X86_OP_REG and ops[0].size < 4:
            # and al, 3; neg al; sbb eax, eax tests the byte: the mask without the bits and al left
            # alone (CBool(WAttack(sq) And 3) on an Integer printed (x And -253) <> 0, ChessBrainVB
            # CalcThreats' PawnProtected was true for every other attacker); and al, 1; dec al likewise
            full = (1 << 8 * ops[0].size) - 1

            def low_mask(e):
                if isinstance(e, BinOp) and e.op == "And" and isinstance(e.b, Const) and \
                        isinstance(e.b.value, int) and e.b.value & ~full & 0xffffffff:
                    return BinOp("And", e.a, Const(e.b.value & full))
                if isinstance(e, BinOp) and e.op in ("+", "-") and isinstance(e.b, Const):
                    x = low_mask(e.a)
                    return BinOp(e.op, x, e.b) if x is not e.a else e
                return e
            low = low_mask(a)
            if low is not a:
                st.flags = ("arith", UnOp("-", low), m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m == "not":
        lf.whole_element = ops[0].type == X.X86_OP_MEM
        a = lf.rd(st, ops[0])
        lf.whole_element = False
        lf.wr(st, ops[0], UnOp("Not", a), stmts)
        return
    if m == "inc":
        lf.whole_element = ops[0].type == X.X86_OP_MEM
        r = add(lf.rd(st, ops[0]), Const(1))
        lf.whole_element = False
        if isinstance(r, BinOp):
            r.width = 4 if ops[0].size == 4 else 2
        st.flags = ("arith", r, m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m == "dec":
        lf.whole_element = ops[0].type == X.X86_OP_MEM
        r = sub(lf.rd(st, ops[0]), Const(1))
        lf.whole_element = False
        if isinstance(r, BinOp):
            r.width = 4 if ops[0].size == 4 else 2
        st.flags = ("arith", r, m)
        lf.wr(st, ops[0], r, stmts)
        return
    if m in ("shl", "sal"):
        a, b = lf.rd(st, ops[0]), lf.rd(st, ops[1])
        if isinstance(b, Const):
            r = mul_const(a, 1 << b.value)      # (folds x * 5 shl 2 into x * 20)
            if isinstance(r, BinOp):
                r.width = 4 if ops[0].size == 4 else 2   # a Long product (VBDoom CLng(pal(i)) * 256&)
            lf.wr(st, ops[0], r, stmts)
        else:
            lf.wr(st, ops[0], BinOp("<<", a, b), stmts)
        return
    if m in ("shr", "sar"):
        a, b = lf.rd(st, ops[0]), lf.rd(st, ops[1])
        if isinstance(a, SignOf) and a.biased and isinstance(b, Const) and \
                a.mask == (1 << b.value) - 1:
            lf.wr(st, ops[0], BinOp("\\", a.x, Const(1 << b.value)), stmts)   # x \ 2^k
            return
        if isinstance(b, Const) and m == "sar" and b.value == 31 and ops[0].size == 4:
            # -1 for a negative number, 0 else: VB's x < 0.  (x \\ &H80000000 is 0: a For with a step
            # only known at run time, (sar step, 31) Xor i <= (sar step, 31) Xor limit, tested the
            # wrong way going down; ChessBrainVB MovePossible)
            lf.wr(st, ops[0], BinOp("<", a, Const(0)), stmts)
        elif isinstance(b, Const):
            lf.wr(st, ops[0], BinOp("\\", a, Const(1 << b.value)), stmts)
        else:
            lf.wr(st, ops[0], BinOp(">>", a, b), stmts)
        return
    if m in ("cmp", "test"):
        a, b = lf.rd(st, ops[0]), lf.rd(st, ops[1])
        if ops[0].type == X.X86_OP_REG and ops[0].size < 4:
            a = lf.low_value(a, ops[0].size)
            if ops[1].type == X.X86_OP_REG:
                b = lf.low_value(b, ops[1].size)
        st.flags = (m, a, b, ops[0].size)
        n = lf.p.bound_cmps.get(ins.address) if m == "cmp" else None
        if n is not None and hasattr(a, "key"):
            lf.note_index_bound(a, n)       # an array index below n
        return
    if m == "cdq":
        st.set("edx", SignOf(st.get("eax")))
        return
    if m == "cwde":
        st.set("eax", coerce(st.get("eax"), 2, True))
        return
    if m == "cwd":                          # dx:ax = sign-extended ax (before idiv)
        st.set("edx", SignOf(st.get("eax")))
        return
    if m in ("idiv", "div"):
        d = lf.rd(st, ops[0])
        n = st.get("eax")
        st.set("eax", BinOp("\\", n, d))
        st.set("edx", BinOp("Mod", n, d))
        return
    if m == "mul":
        st.set("eax", BinOp("*", st.get("eax"), lf.rd(st, ops[0])))
        st.set("edx", Unknown("mulhi"))
        return
    if m.startswith("set"):
        # setcc yields 1/0; a VB comparison yields -1/0, so negate it
        f = lf.cond_expr(st, "j" + m[3:])
        lf.wr(st, ops[0], UnOp("-", f) if f is not None else Unknown(m), stmts)
        return
    if m.startswith("cmov"):
        return
    if fpu(lf, st, ins, stmts):
        return
    stmts.append(Unknown("asm %s %s" % (m, ins.op_str)))


def fold(m, a, b):
    if m == "imul":
        return s32(a * b)
    if m == "and":
        return a & b
    if m == "or":
        return a | b
    if m == "xor":
        return a ^ b
    return 0


def coerce(v, size, signed):
    """Narrow a value to `size` bytes; constants fold."""
    if isinstance(v, Const):
        x = v.value & ((1 << (size * 8)) - 1)
        if signed and x & (1 << (size * 8 - 1)):
            x -= 1 << (size * 8)
        return Const(x)
    return v


FPU_READS = {"fstp", "fst", "fistp", "fist", "fcom", "fcomp", "fcompp", "fucom", "fucomp",
             "fucompp", "fchs", "fabs", "fxch", "fyl2x", "fpatan"} | set(FP_FUNCTIONS) | \
    set(FIARITH) | set(FARITH)


def fpu(lf, st, ins, stmts):
    m = ins.mnemonic
    ops = ins.operands
    if not st.fpu and m in FPU_READS and getattr(lf, "last_call", None) and \
            lf.last_call[0] == ins.address and lf.last_call[1] is not None:
        # st(0) read right after a call with nothing pushed: the callee left
        # its result there (ChessBrainVB "call TimeElapsed; fstp [ebp-18h]")
        lf.m.__dict__.setdefault("float_callees", set()).add(lf.last_call[1])
    if m in ("fld", "fild"):
        # fild reads an integer: the operand's size is not a float width
        v = lf.rd(st, ops[0], fp=(m == "fld"))
        if m == "fild" and not isinstance(v, Const):
            v = copy.copy(v)
            v.from_fild = True          # (mkf, below: what VB converted, with CSng or implicitly)
        st.fpush(v)
        return True
    if m in FP_CONSTANTS:
        st.fpush(FpConst(m))
        return True
    if m == "fyl2x":
        # st(1) * log2(st(0)), popped: fldln2; fld x; fyl2x is VB's Log(x), inlined by the optimiser
        # (ChessBrainVB InitReductionArray printed ?asm and left Reductions() nothing to go on)
        x = st.st(0)
        st.fpop()
        y = st.st(0)
        ln = ir.Call("Log", [x], "Double", pure=True)
        if isinstance(y, FpConst) and y.name == "fldln2":
            r = ln
        elif isinstance(y, FpConst) and y.name == "fldlg2":
            r = BinOp("/", ln, ir.Call("Log", [Const(10)], "Double", pure=True))
        else:
            r = BinOp("*", y, BinOp("/", ln, ir.Call("Log", [Const(2)], "Double", pure=True)))
        st.setst(0, r)
        return True
    if m == "fpatan":
        # atan(st(1) / st(0)), popped: fld x; fld1; fpatan is Atn(x)
        x = st.st(0)
        st.fpop()
        y = st.st(0)
        one = isinstance(x, Const) and x.value == 1 or isinstance(x, FpConst) and x.name == "fld1"
        st.setst(0, ir.Call("Atn", [y if one else BinOp("/", y, x)], "Double", pure=True))
        return True
    if m in FP_FUNCTIONS:
        st.setst(0, ir.Call(FP_FUNCTIONS[m], [st.st(0)], "Double", pure=True))
        if m == "fptan":
            st.fpush(Const(1))              # (and a 1 on top, which fstp st(0) drops)
        return True
    if m == "fldz":
        st.fpush(Const(0.0))
        return True
    if m == "fld1":
        st.fpush(Const(1.0))
        return True
    if m in ("fstp", "fst", "fistp", "fist"):
        v = st.st(0)
        fp = m in ("fstp", "fst")
        if ops[0].type == X.X86_OP_REG:          # fstp st(i): copy then pop
            i = lf.FPREGS.get(ops[0].reg, 0)
            st.setst(i, v)
            if m == "fstp":
                st.fpop()
            return True
        if fp and getattr(v, "from_fild", False) and ops[0].mem.index == 0 and (
                ops[0].mem.base == X.X86_REG_ESP or
                ops[0].mem.base == X.X86_REG_EBP and ops[0].mem.disp < 0 and
                not lf.is_named_local(Var("local", ops[0].mem.disp))):
            # an integer converted into a temporary, for a ByRef Single (VectorScalar(x, CSng(i)):
            # as VectorScalar(x, i) "ByRef argument type mismatch", and the parameter was made a
            # Variant for it, whose arithmetic rounds otherwise).  In a frameless procedure any of
            # them: a = PopIntStack: a / 200 is CSng(PopIntStack()) / 200, not Long / Single, whose
            # Double keeps digits the Single lost (DarwinBots DNAcos)
            size = ops[0].size
            v = ir.Call("CSng" if size == 4 else "CDbl", [v], "Single" if size == 4 else "Double",
                        pure=True)
        n0 = len(stmts)
        lf.wr(st, ops[0], v, stmts, fp=fp)
        if fp and len(stmts) > n0 and isinstance(stmts[-1], Assign):
            stmts[-1].fp_store = ops[0].size    # (types: a Single, whatever the expression's type)
        if m in ("fstp", "fistp"):
            st.fpop()
        return True
    if m in FIARITH:
        b = lf.rd(st, ops[0], fp=False)  # (an integer: no float width, as for fild)
        a = st.fpop()
        st.fpush(mkf(FIARITH[m], a, b, m))
        return True
    if m in FARITH:
        op = FARITH[m]
        if not ops:                      # faddp / fmulp with implicit st(1),st(0)
            b = st.fpop()
            a = st.fpop()
            st.fpush(mkf(op, a, b, m))
            return True
        if len(ops) == 1 and ops[0].type == X.X86_OP_REG and ops[0].reg in lf.FPREGS and \
                m.endswith("p"):
            # "fdivp st(1)" (DE F9) is fdivp st(1), st(0): st(i) = st(i) op st(0),
            # then a pop (as "fdiv st(1)" - D8 F1, st(0) = st(0) / st(1) - it divided
            # the other way round and left st(1) behind: ChessBrainVB CalcTime,
            # Ratio * (1 + Inc / (MyTime * 8.2)))
            i = lf.FPREGS[ops[0].reg]
            a, b = st.st(i), st.st(0)
            st.setst(i, mkf(op, a, b, m))
            st.fpop()
            return True
        if len(ops) == 1:
            b = lf.rd(st, ops[0], fp=True)
            a = st.fpop()
            st.fpush(mkf(op, a, b, m))
            return True
        a = lf.rd(st, ops[0], fp=True)
        b = lf.rd(st, ops[1], fp=True)
        if m.endswith("p"):
            # st(i), st(0): st(i) = st(i) op st(0), then a pop
            i = lf.FPREGS.get(ops[0].reg, 1) if ops[0].type == X.X86_OP_REG else 1
            st.setst(i, mkf(op, a, b, m))
            st.fpop()
        elif ops[0].type == X.X86_OP_REG and ops[0].reg in lf.FPREGS and \
                lf.FPREGS[ops[0].reg] != 0:
            st.setst(lf.FPREGS[ops[0].reg], mkf(op, a, b, m))   # fadd st(i), st(0)
        else:
            st.setst(0, mkf(op, a, b, m))
        return True
    if m == "fchs":
        st.setst(0, UnOp("-", st.st(0)))
        return True
    if m == "fabs":
        st.setst(0, ir.Call("Abs", [st.st(0)], pure=True))  # (a function, not an operator)
        return True
    if m in ("fcom", "fcomp", "fcompp", "fucom", "fucomp", "fucompp"):
        a = st.st(0)
        b = lf.rd(st, ops[0], fp=True) if ops else st.st(1)
        for _ in range(2 if m.endswith("pp") else (1 if m.endswith("p") else 0)):
            st.fpop()
        st.flags = ("fcom", a, b)
        st.fsw = st.flags       # (integer instructions between change only EFLAGS:
        return True             # ChessBrainVB CalcTime "fcomp; inc eax; cdq; ... fnstsw")
    if m == "fnstsw":
        f = st.flags if st.flags and st.flags[0] == "fcom" else st.fsw
        if f and f[0] == "fcom":
            st.set("eax", FSW(f[1], f[2]))
        return True
    if m in ("fldcw", "fnstcw", "fclex", "finit", "fninit"):
        return True
    if m == "fxch":
        a, b = st.st(0), st.st(1)
        st.setst(0, b)
        st.setst(1, a)
        return True
    return False


def mkf(op, a, b, m):
    if m in REVERSED_F:
        a, b = b, a
    e = BinOp(op, a, b)
    e.fp = True     # floating point, whatever its operands (render: CDbl of two Longs)
    return e
