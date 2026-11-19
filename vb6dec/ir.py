"""Expression / statement IR produced by the lifter and rendered as VB."""


class Expr:
    prec = 100              # binding strength for rendering (higher binds tighter)
    vbtype = None

    def walk(self):
        """This node and every node under it, parents first, left to right.
        (An explicit stack: a recursive generator hands every node up through
        each level above it.)  children() is called when a node is reached, as
        before, so a pass may rewrite the part not reached yet."""
        stack = [self]
        while stack:
            x = stack.pop()
            yield x
            c = x.children()
            if c:
                stack.extend(reversed(c))

    def children(self):
        return ()

    def replace(self, f):
        """Return a copy with every sub-expression mapped through f
        (f returns a replacement or None to keep and recurse)."""
        r = f(self)
        if r is not None:
            if "fp4" in self.__dict__ and r is not self:
                r.fp4 = True        # (a location rewritten is still read as a Single: udt.fp_fields)
            return r
        return self._rebuild(lambda c: c.replace(f))

    def _rebuild(self, g):
        return self

    CORE = ()               # constructor fields; anything else is copied by _keep

    def _keep(self, new):
        """Copy annotations (iface, control, new_class, vt, ...) onto a rebuilt
        node, so passes that rewrite expressions do not lose them."""
        for k, v in self.__dict__.items():
            if k not in self.CORE and k not in new.__dict__:
                new.__dict__[k] = v
        return new

    def __eq__(self, other):
        return type(self) is type(other) and self.key() == other.key()

    def __hash__(self):
        return hash((type(self).__name__, self.key()))

    def key(self):
        return id(self)


class Const(Expr):
    def __init__(self, value, vbtype=None):
        self.value, self.vbtype = value, vbtype

    def key(self):
        return (self.value, self.vbtype)

    def __repr__(self):
        return "Const(%r)" % (self.value,)


class Str(Expr):
    vbtype = "String"

    def __init__(self, text):
        self.text = text

    def key(self):
        return self.text

    def __repr__(self):
        return "Str(%r)" % self.text


class Var(Expr):
    """A named storage location: local slot, argument, global, member."""

    def __init__(self, space, off, name=None, vbtype=None):
        self.space, self.off = space, off      # space: 'local','arg','global','member','reg'
        self.name, self.vbtype = name, vbtype

    def key(self):
        return (self.space, self.off)

    def __repr__(self):
        return "Var(%s,%#x)" % (self.space, self.off) if isinstance(self.off, int) else "Var(%s,%s)" % (self.space, self.off)


class Me(Expr):
    def key(self):
        return "Me"

    def __repr__(self):
        return "Me"


class AddrOf(Expr):
    def __init__(self, e):
        self.e = e

    def children(self):
        return (self.e,)

    CORE = ("e",)

    def _rebuild(self, g):
        return self._keep(AddrOf(g(self.e)))

    def key(self):
        return self.e.key()

    def __repr__(self):
        return "&%r" % (self.e,)


class Deref(Expr):
    def __init__(self, e, size=4, off=0):
        self.e, self.size, self.off = e, size, off

    def children(self):
        return (self.e,)

    CORE = ("e", "size", "off")

    def _rebuild(self, g):
        return self._keep(Deref(g(self.e), self.size, self.off))

    def key(self):
        return (self.e.key(), self.size, self.off)

    def __repr__(self):
        return "*(%r+%#x)" % (self.e, self.off)


class BinOp(Expr):
    PREC = {"^": 14, "*": 12, "/": 12, "\\": 11, "Mod": 10, "+": 9, "-": 9, "&": 8,
            "=": 7, "<>": 7, "<": 7, ">": 7, "<=": 7, ">=": 7, "Like": 7, "Is": 7,
            "And": 5, "Or": 4, "Xor": 3, "Eqv": 2, "Imp": 1}

    def __init__(self, op, a, b, vbtype=None):
        self.op, self.a, self.b, self.vbtype = op, a, b, vbtype
        self.prec = self.PREC.get(op, 6)

    def children(self):
        return (self.a, self.b)

    CORE = ("op", "a", "b", "vbtype", "prec")

    def _rebuild(self, g):
        return self._keep(BinOp(self.op, g(self.a), g(self.b), self.vbtype))

    def key(self):
        return (self.op, self.a.key(), self.b.key())

    def __repr__(self):
        return "(%r %s %r)" % (self.a, self.op, self.b)


class UnOp(Expr):
    def __init__(self, op, a, vbtype=None):
        self.op, self.a, self.vbtype = op, a, vbtype
        self.prec = 13 if op == "-" else 6

    def children(self):
        return (self.a,)

    CORE = ("op", "a", "vbtype", "prec")

    def _rebuild(self, g):
        return self._keep(UnOp(self.op, g(self.a), self.vbtype))

    def key(self):
        return (self.op, self.a.key())

    def __repr__(self):
        return "(%s %r)" % (self.op, self.a)


class Call(Expr):
    """Function call / VB intrinsic.  name is the VB name to print."""

    def __init__(self, name, args, vbtype=None, raw=None, method=False, obj=None, pure=False):
        self.name, self.args, self.vbtype = name, list(args), vbtype
        self.raw = raw              # runtime/import name it came from
        self.obj = obj              # receiver for methods/properties
        self.method = method
        self.pure = pure

    def children(self):
        return ([self.obj] if self.obj is not None else []) + self.args

    CORE = ("name", "args", "vbtype", "raw", "obj", "method", "pure")

    def _rebuild(self, g):
        c = Call(self.name, [g(a) for a in self.args], self.vbtype, self.raw, self.method,
                 g(self.obj) if self.obj is not None else None, self.pure)
        return self._keep(c)

    def key(self):
        return (self.name, tuple(a.key() for a in self.args),
                self.obj.key() if self.obj is not None else None)

    def __repr__(self):
        o = "%r." % (self.obj,) if self.obj is not None else ""
        return "%s%s(%s)" % (o, self.name, ", ".join(map(repr, self.args)))


class Prop(Expr):
    """obj.Member (property get / control reference)."""

    control = None          # set when this is a form's control getter
    iface = None            # interface the value exposes

    def __init__(self, obj, name, args=(), vbtype=None):
        self.obj, self.name, self.args, self.vbtype = obj, name, list(args), vbtype

    def children(self):
        return [self.obj] + self.args

    CORE = ("obj", "name", "args", "vbtype")

    def _rebuild(self, g):
        p = Prop(g(self.obj), self.name, [g(a) for a in self.args], self.vbtype)
        return self._keep(p)

    def key(self):
        return (self.obj.key(), self.name, tuple(a.key() for a in self.args))

    def __repr__(self):
        # (the arguments too: structure takes statements that print alike for one, and
        # If t > 0 Then opt(t - 1) = True Else opt(0) = True lost its test; DarwinBots DispSettings)
        return "%r.%s%s" % (self.obj, self.name, "(%s)" % ", ".join(map(repr, self.args))
                            if self.args else "")


class Index(Expr):
    def __init__(self, arr, idx, vbtype=None):
        self.arr, self.idx, self.vbtype = arr, list(idx), vbtype

    def children(self):
        return [self.arr] + self.idx

    CORE = ("arr", "idx", "vbtype")

    def _rebuild(self, g):
        return self._keep(Index(g(self.arr), [g(i) for i in self.idx], self.vbtype))

    def key(self):
        return (self.arr.key(), tuple(i.key() for i in self.idx))

    def __repr__(self):
        return "%r(%s)" % (self.arr, ", ".join(map(repr, self.idx)))


class Field(Expr):
    def __init__(self, base, off, name=None, vbtype=None):
        self.base, self.off, self.name, self.vbtype = base, off, name, vbtype

    def children(self):
        return (self.base,)

    CORE = ("base", "off", "name", "vbtype")

    def _rebuild(self, g):
        return self._keep(Field(g(self.base), self.off, self.name, self.vbtype))

    def key(self):
        return (self.base.key(), self.off)

    def __repr__(self):
        return "%r.f%x" % (self.base, self.off)


class Unknown(Expr):
    def __init__(self, text):
        self.text = text

    def key(self):
        return self.text

    def __repr__(self):
        return "?%s" % self.text


# ---------------------------------------------------------------- statements
class Stmt:
    va = None

    def exprs(self):
        return ()


class Assign(Stmt):
    def __init__(self, dst, src, kind="let"):
        self.dst, self.src, self.kind = dst, src, kind      # kind: let / set / move

    def exprs(self):
        return (self.dst, self.src)

    def __repr__(self):
        return "%s%r = %r" % ("Set " if self.kind == "set" else "", self.dst, self.src)


class ExprStmt(Stmt):
    def __init__(self, e):
        self.e = e

    def exprs(self):
        return (self.e,)

    def __repr__(self):
        return repr(self.e)


class Free(Stmt):
    """Release of temporaries / locals; kept for temp analysis, never printed."""

    def __init__(self, targets, what):
        self.targets, self.what = targets, what

    def exprs(self):
        return tuple(self.targets)

    def __repr__(self):
        return "free(%s)" % ", ".join(map(repr, self.targets))


class Raw(Stmt):
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "' " + self.text


class Return(Stmt):
    value = None

    def exprs(self):
        return (self.value,) if self.value is not None else ()

    def __repr__(self):
        return "return"


class OnError(Stmt):
    def __init__(self, mode, target=None):
        self.mode, self.target = mode, target

    def __repr__(self):
        return "On Error %s" % self.mode


# ---------------------------------------------------------------- helpers
def subst(e, mapping):
    """Replace Var keys found in mapping."""
    def f(x):
        if isinstance(x, Var) and x.key() in mapping:
            return mapping[x.key()]
        return None
    return e.replace(f)


def uses(e, var):
    k = var.key()
    return sum(1 for x in e.walk() if isinstance(x, Var) and x.key() == k)


class Missing(Expr):
    """An omitted Optional argument (VT_ERROR / DISP_E_PARAMNOTFOUND)."""

    def key(self):
        return "missing"

    def __repr__(self):
        return "Missing"
