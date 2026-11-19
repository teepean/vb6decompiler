"""Machine state for symbolic execution of one block."""
from capstone import x86

from . import ir
from .ir import Const, Var, Me, AddrOf, Deref, BinOp, Unknown

X = x86
REG32 = {}
for _full, _parts in {
    "eax": ("EAX", "AX", "AL", "AH"), "ebx": ("EBX", "BX", "BL", "BH"),
    "ecx": ("ECX", "CX", "CL", "CH"), "edx": ("EDX", "DX", "DL", "DH"),
    "esi": ("ESI", "SI"), "edi": ("EDI", "DI"), "ebp": ("EBP", "BP"), "esp": ("ESP", "SP"),
}.items():
    for _p in _parts:
        REG32[getattr(X, "X86_REG_" + _p)] = _full
HIGH8 = {X.X86_REG_AH, X.X86_REG_BH, X.X86_REG_CH, X.X86_REG_DH}


class SP(ir.Expr):
    """The address esp0 + off (esp0 = stack pointer at procedure entry)."""

    def __init__(self, off):
        self.off = off

    def key(self):
        return self.off

    def __repr__(self):
        return "SP(%+#x)" % self.off


class Hi(ir.Expr):
    """Upper dword of an 8-byte stack value."""

    def __init__(self, of):
        self.of = of

    def key(self):
        return ("hi", self.of.key())

    def children(self):
        return [self.of]            # visible to use counts and substitution (inlining)

    def _rebuild(self, g):
        return self._keep(Hi(g(self.of)))

    def __repr__(self):
        return "Hi(%r)" % (self.of,)


class IatSlot(ir.Expr):
    """Pointer to an import address table entry, e.g. 'mov esi, [__vbaStrMove]'."""

    def __init__(self, va, name):
        self.va, self.name = va, name

    def key(self):
        return self.va

    def __repr__(self):
        return "&%s" % self.name


class FSW(ir.Expr):
    """FPU status word after a compare (fnstsw ax)."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def key(self):
        return ("fsw", self.a.key(), self.b.key())

    def children(self):
        return [self.a, self.b]     # (a register in a compare left for fnstsw is still a use)

    def _rebuild(self, g):
        return self._keep(FSW(g(self.a), g(self.b)))


class State:
    def __init__(self):
        self.regs = {}
        self.stack = {}         # esp0-relative offset -> Expr (pushed values)
        self.esp = 0
        self.fpu = []           # fpu[-1] is st(0)
        self.flags = None
        self.fsw = None         # the last FPU compare (the status word fnstsw reads)
        self.spilled = {}       # register -> the constant its variable got on this path

    def copy(self):
        s = State()
        s.regs = dict(self.regs)
        s.stack = dict(self.stack)
        s.esp = self.esp
        s.fpu = list(self.fpu)
        s.flags = self.flags
        s.fsw = self.fsw
        s.spilled = dict(self.spilled)
        return s

    # registers -------------------------------------------------------------
    def get(self, name):
        if name == "esp":
            return SP(self.esp)
        v = self.regs.get(name)
        return v if v is not None else Var("reg", name)

    def set(self, name, v):
        if name == "esp":
            if isinstance(v, SP):
                self.esp = v.off
            return
        self.regs[name] = v

    # stack -----------------------------------------------------------------
    def push(self, v):
        self.esp -= 4
        self.stack[self.esp] = v

    def pop(self):
        v = self.stack.pop(self.esp, None)
        self.esp += 4
        return v if v is not None else Unknown("pop")

    def args(self, n):
        """n dword arguments at the stack top (C order)."""
        return [self.stack.get(self.esp + 4 * i, Unknown("arg%d" % i)) for i in range(n)]

    def drop(self, n):
        for i in range(n):
            self.stack.pop(self.esp + 4 * i, None)
        self.esp += 4 * n

    # fpu -------------------------------------------------------------------
    def fpush(self, v):
        self.fpu.append(v)

    def fpop(self):
        return self.fpu.pop() if self.fpu else Unknown("st0")

    def st(self, i=0):
        return self.fpu[-1 - i] if len(self.fpu) > i else Unknown("st%d" % i)

    def setst(self, i, v):
        while len(self.fpu) <= i:
            self.fpu.insert(0, Unknown("st"))
        self.fpu[-1 - i] = v


def _c(e):
    return isinstance(e, Const) and isinstance(e.value, int)


def scaled(e):
    """(x, k) when e is k times one expression x: x * k, x * k1 + x * k2,
    x * k1 - x, ... - the compiler's multiply by a constant as lea / shl /
    add steps (x * 21 is (x + x * 4) * 4 + x).  Otherwise (e, 1)."""
    if isinstance(e, BinOp) and e.op == "*" and _c(e.b):
        x, k = scaled(e.a)
        return x, k * e.b.value
    if isinstance(e, BinOp) and e.op in ("+", "-") and not _c(e.b):
        xa, ka = scaled(e.a)
        xb, kb = scaled(e.b)
        if hasattr(xa, "key") and hasattr(xb, "key") and type(xa) is type(xb) and \
                xa.key() == xb.key():
            return xa, ka + kb if e.op == "+" else ka - kb
    return e, 1


def mul_const(x, k):
    """x * k, folding into x what multiplies it already."""
    base, k0 = scaled(x)
    k = s32(k0 * k)
    if k == 1:
        return base
    if k == 0:
        return Const(0)
    return BinOp("*", base, Const(k))


def same_base(a, b):
    """a and b are multiples of one expression: (x, ka, kb), else None."""
    if isinstance(a, (Const, SP)) or isinstance(b, (Const, SP)):
        return None
    xa, ka = scaled(a)
    xb, kb = scaled(b)
    if (ka, kb) != (1, 1) and hasattr(xa, "key") and hasattr(xb, "key") and \
            type(xa) is type(xb) and xa.key() == xb.key():
        return xa, ka, kb
    if (ka, kb) == (1, 1) and hasattr(a, "key") and hasattr(b, "key") and \
            type(a) is type(b) and a.key() == b.key() and \
            not isinstance(a, Const):
        return a, 1, 1                              # x + x
    return None


def add(a, b):
    """a + b with constant folding and SP arithmetic."""
    if isinstance(b, BinOp) and b.op == "*" and _c(b.a) and _c(b.b) and \
            isinstance(b.a.value, int) and isinstance(b.b.value, int):
        b = Const(b.a.value * b.b.value)    # (a constant subscript: jalki(2) is 1 * 8)
    sb = same_base(a, b)
    if sb is not None:
        return mul_const(sb[0], sb[1] + sb[2])      # x * k1 + x * k2: x * (k1 + k2)
    if isinstance(a, SP) and isinstance(b, Const):
        return SP(a.off + b.value)
    if isinstance(a, Me) and _c(b) and b.value > 0:
        return AddrOf(Var("member", b.value))     # VB only adds to Me to address a member
    if isinstance(a, AddrOf) and _c(b) and b.value > 0 and isinstance(b.value, int):
        # a field of an element, by address: With rob(t): VectorSub(.pos, ...) printed rob(t) + 24
        if isinstance(a.e, ir.Index):
            return AddrOf(Deref(a.e, 4, b.value))
        if isinstance(a.e, Deref) and isinstance(a.e.e, ir.Index):
            return AddrOf(Deref(a.e.e, 4, a.e.off + b.value))
        if isinstance(a.e, Var) and a.e.space == "member" and isinstance(a.e.off, int):
            # (a member record's field: With theTray: .szTip = s printed (m3C + 24) = s)
            return AddrOf(Var("member", a.e.off + b.value))
    if isinstance(a, BinOp) and a.op in ("+", "-") and _c(a.b) and _c(b):
        k = a.b.value if a.op == "+" else -a.b.value
        return add(a.a, Const(k + b.value))        # (x +- c1) + c2
    if isinstance(b, SP) and isinstance(a, Const):
        return SP(b.off + a.value)
    if isinstance(a, Const) and isinstance(b, Const):
        return Const(a.value + b.value)
    if _c(a) and not isinstance(b, Const):
        return add(b, a)                            # constants last: x + 1, not 1 + x
    if isinstance(b, Const) and b.value == 0:
        return a
    if isinstance(b, Const) and b.value < 0:
        return BinOp("-", a, Const(-b.value))
    return BinOp("+", a, b)


def sub(a, b):
    sb = same_base(a, b)
    if sb is not None and sb[1] != sb[2]:
        return mul_const(sb[0], sb[1] - sb[2])      # x * k1 - x * k2
    if isinstance(a, SP) and isinstance(b, Const):
        return SP(a.off - b.value)
    if isinstance(a, BinOp) and a.op in ("+", "-") and _c(a.b) and _c(b):
        k = a.b.value if a.op == "+" else -a.b.value
        return add(a.a, Const(k - b.value))        # (x +- c1) - c2
    if isinstance(a, Const) and isinstance(b, Const):
        return Const(a.value - b.value)
    if isinstance(b, Const) and b.value == 0:
        return a
    return BinOp("-", a, b)


def s32(v):
    v &= 0xffffffff
    return v - (1 << 32) if v & 0x80000000 else v
