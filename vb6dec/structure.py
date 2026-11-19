"""Turn the lifted CFG back into VB control structures.

Natural loops become Do/Loop, two-way branches become If/ElseIf/Else, and
anything that does not fit falls back to a label with GoTo.
"""
from . import ir
from .ir import UnOp, BinOp, Const

NEG = {"=": "<>", "<>": "=", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}


def is_test(e):
    """A condition built from comparisons (safe for De Morgan)."""
    if isinstance(e, BinOp) and e.op in NEG:
        return True
    if isinstance(e, BinOp) and e.op in ("And", "Or"):
        return is_test(e.a) and is_test(e.b)
    return False


def negate(e):
    if isinstance(e, BinOp) and e.op in NEG:
        return BinOp(NEG[e.op], e.a, e.b)
    if isinstance(e, BinOp) and e.op in ("And", "Or") and is_test(e):
        return BinOp("Or" if e.op == "And" else "And", negate(e.a), negate(e.b))
    if isinstance(e, UnOp) and e.op == "Not":
        return e.a
    return UnOp("Not", e)


# ------------------------------------------------------------------ regions
class Seq:
    def __init__(self, items):
        self.items = items


class Block:
    def __init__(self, va, stmts):
        self.va, self.stmts = va, stmts


class If:
    def __init__(self, cond, then, els=None):
        self.cond, self.then, self.els = cond, then, els


class Loop:
    """kind: 'while' (test at top), 'until' (test at bottom), 'forever'."""

    def __init__(self, kind, cond, body):
        self.kind, self.cond, self.body = kind, cond, body


class Goto:
    def __init__(self, target):
        self.target = target


class Label:
    def __init__(self, va):
        self.va = va


class Exit:
    pass


class ExitLoop:
    """Leave the innermost loop (Exit Do / Exit For)."""
    pass


class Handlers:
    """Marks where the error handlers start (after an explicit Exit)."""
    pass


class ResumeTo:
    """Resume <label>: leave the error handler for the label."""

    def __init__(self, target):
        self.target = target


def resume_split(stmts):
    """(statements before, statements after) a __vbaResume(0) in the block,
    or None.  (Exit blocks: the compiler may copy the label's code after the
    call - gh_zip OpenArchive "Resume QH", QH: pvVfsClose: Exit Function.)"""
    for i, s in enumerate(stmts):
        e = getattr(s, "e", None)
        if getattr(e, "resume", False) and len(e.args) == 1 and \
                isinstance(e.args[0], Const) and e.args[0].value == 0:
            return stmts[:i], stmts[i + 1:]
    return None


def resume_at_end(stmts):
    """The block ends with __vbaResume(0) (Resume label, with its jump), but
    for a Function's return marker: the statements without the call, or
    None."""
    from .ir import Return, Free
    k = len(stmts)
    while k and isinstance(stmts[k - 1], (Return, Free)):
        k -= 1
    if not k:
        return None
    e = getattr(stmts[k - 1], "e", None)
    if getattr(e, "resume", False) and len(e.args) == 1 and \
            isinstance(e.args[0], Const) and e.args[0].value == 0:
        return stmts[:k - 1] + stmts[k:]
    return None


# ------------------------------------------------------------------ analysis
def dominators(nodes, entry):
    order = rpo(nodes, entry)
    idx = {a: i for i, a in enumerate(order)}
    dom = {a: set(order) for a in order}
    dom[entry] = {entry}
    changed = True
    while changed:
        changed = False
        for a in order:
            if a == entry:
                continue
            preds = [p for p in nodes[a].preds if p in idx]
            if not preds:
                continue
            new = set.intersection(*[dom[p] for p in preds]) | {a}
            if new != dom[a]:
                dom[a] = new
                changed = True
    return dom


def rpo(nodes, entry):
    seen, post = set(), []

    def go(a):
        seen.add(a)
        for s in nodes[a].succs:
            if s in nodes and s not in seen:
                go(s)
        post.append(a)
    go(entry)
    return post[::-1]


def back_edges(nodes, entry, dom):
    out = []
    for a in nodes:
        for s in nodes[a].succs:
            if s in dom.get(a, ()):          # s dominates a: a -> s is a back edge
                out.append((a, s))
    return out


def loop_body(nodes, tail, head):
    body = {head}
    stack = [tail]
    while stack:
        n = stack.pop()
        if n in body:
            continue
        body.add(n)
        stack.extend(p for p in nodes[n].preds if p in nodes)
    return body


class Structurer:
    def __init__(self, lf, body):
        self.lf = lf
        self.nodes = lf.nodes
        self.entry = lf.p.va
        self.stmts = {a: list(ss) for a, ss, _ in body}   # copies: recovery edits them
        self.brs = {a: br for a, _, br in body}
        self.dom = dominators(self.nodes, self.entry)
        self.loops = {}
        for tail, head in back_edges(self.nodes, self.entry, self.dom):
            self.loops.setdefault(head, set()).update(loop_body(self.nodes, tail, head))
        self.labels = set()
        self.exit_labels = {}       # Resume targets ending the procedure -> their code
        self.emitted = set()
        self.active = set()         # headers of the loops being structured
        self.follows = {}           # loop body (frozenset) -> the node after the loop
        self.latches = {}           # bottom-tested loop body -> its latch (the test)

    def run(self):
        r = self.region(self.entry, None, frozenset())
        handlers = getattr(self.lf.p, "handlers", {})
        if handlers:
            ends = r.items[-1] if isinstance(r, Seq) and r.items else r
            items = [r] + ([] if isinstance(ends, Exit) else [Exit()]) + [Handlers()]
            for idx, va in sorted(handlers.items()):
                if va in self.nodes:
                    # (labelled also when the normal flow already placed it:
                    # VBDoom M_LoadDefaults' "done:" follows its loop)
                    self.labels.add(va)
                if va in self.nodes and va not in self.emitted:
                    items.append(self.region(va, None, frozenset()))
            r = Seq(items)
        # GoTo targets nothing placed (a loop left through a test that only
        # the loop reaches, gh_zip OpenArchive): after the body, under their
        # label, ending in GoTos back into it
        for _ in range(16):
            pending = sorted(a for a in self.labels if a in self.nodes and a not in self.emitted)
            if not pending:
                break
            items = list(r.items) if isinstance(r, Seq) else [r]
            if not items or not isinstance(items[-1], Exit):
                items.append(Exit())
            for a in pending:
                if a not in self.emitted:
                    items.append(self.region(a, None, frozenset()))
                    items.append(Exit())
            r = Seq(items)
        ends = [Block(a, tail) for a, tail in sorted(self.exit_labels.items())
                if a not in self.emitted]
        if ends:
            r = Seq((list(r.items) if isinstance(r, Seq) else [r]) + ends)
        # a GoTo to a loop's test from code structured after the loop: the
        # head keeps its label, and the loop stays a Do While (a target
        # Text3 Copy_Click: GoTo LB11268 with no such label)
        for lp, head in self.__dict__.get("top_loops", ()):
            if head in self.labels:
                lp.head = head
        return r, self.labels

    def entry_loop_guard(self, a, inloop):
        return None

    # -------------------------------------------------------------- regions
    def region(self, a, stop, inloop, until=None):
        """Linear chain of structures from node a up to (not including) stop;
        with until, the chain ends after that node's statements."""
        items = []
        seen = set()
        while a is not None and a != stop and a in self.nodes:
            if inloop and a == self.latches.get(inloop) and a != until:
                # the bottom test of the loop, reached from inside a branch: go to
                # it.  Printing it here as well would run it twice on this path
                # (once here, once more at the end of the body after End If).
                if self.stmts.get(a):
                    items.append(Goto(a))
                    self.labels.add(a)
                break
            if inloop and a not in inloop:
                # leaving the loop: to the node after it is Exit Do / Exit For;
                # a path that just ends the procedure is structured in place
                if a == self.follows.get(inloop):
                    items.append(ExitLoop())
                    break
                na = self.nodes[a]
                if na.kind not in ("exit", "cond") and a not in self.emitted and \
                        len(na.succs) == 1 and na.succs[0] == self.follows.get(inloop) and \
                        all(p in inloop for p in na.preds):
                    # a block on the way out to the loop's follow (Exit For after
                    # a statement, gh_zip FileInfo): its statements, then Exit
                    self.emitted.add(a)
                    items.append(Block(a, self.stmts.get(a, [])))
                    items.append(ExitLoop())
                    break
                if self.nodes[a].kind != "exit" and not self.ends_procedure(a, inloop):
                    items.append(Goto(a))
                    self.labels.add(a)
                    break
            if a == until:
                self.emitted.add(a)
                b = Block(a, self.stmts.get(a, []))
                items.append(b)
                break
            if a in seen:
                items.append(Goto(a))
                self.labels.add(a)
                break
            seen.add(a)
            if a in self.emitted:
                n = self.nodes[a]
                if n.kind == "exit" and not self.stmts.get(a):
                    items.append(Exit())        # jump to the shared epilogue
                    break
                items.append(Goto(a))
                self.labels.add(a)
                break
            if a in self.loops and a not in self.active:
                node, nxt = self.loop(a, inloop)
                items.append(node)
                a = nxt
                continue
            self.emitted.add(a)
            n = self.nodes[a]
            items.append(Block(a, self.stmts.get(a, [])))
            if n.kind == "exit":
                cont = getattr(n, "cont", None)
                split = resume_split(self.stmts.get(a, []))
                if cont is not None and split is not None:
                    # Resume label where the label's code ends the procedure: the
                    # compiler put it (or just the exit) right after __vbaResume
                    # ("Resume QH ... QH: Exit Function").  The label and that code
                    # go at the end, before End Function.
                    before, after = split
                    from .ir import Return, Free
                    tail = [s for s in after if not isinstance(s, Free)]
                    key = cont
                    if any(not isinstance(s, Return) for s in tail):
                        # code of its own: a label no block or other label has
                        key = cont + 1
                        while key in self.nodes or key in self.labels or \
                                key in self.exit_labels:
                            key += 1
                    items[-1] = Block(a, before)
                    items.append(ResumeTo(key))
                    self.labels.add(key)
                    self.exit_labels[key] = tail
                    break
                items.append(Exit())
                break
            if n.kind == "cond":
                node, nxt = self.two_way(a, stop, inloop)
                items.append(node)
                a = nxt
                continue
            succ = n.succs[0] if n.succs else None
            rest = resume_at_end(self.stmts.get(a, [])) if succ is not None else None
            if rest is not None and len(n.succs) == 1 and succ in self.nodes:
                # Resume label: the statement, then the jump to the label
                items[-1] = Block(a, rest)
                items.append(ResumeTo(succ))
                self.labels.add(succ)
                break
            if succ is not None and succ in inloop and succ not in self.emitted:
                pass
            a = succ
        return Seq(items)

    def ends_procedure(self, a, inloop):
        """Every path from a ends the procedure without coming back to the
        loop or its follow node (... : Exit Sub inside a loop): structure it
        in place."""
        follow = self.follows.get(inloop)
        seen, stack = set(), [a]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in inloop or x == follow or x not in self.nodes:
                return False
            n = self.nodes[x]
            if n.kind == "exit":
                continue
            if not n.succs or len(seen) > 64:
                return False
            stack.extend(n.succs)
        return True

    def two_way(self, a, stop, inloop):
        n = self.nodes[a]
        taken, fall = n.succs
        cond = self.brs[a].cond
        join = self.join_of(a, stop, inloop)
        # VB emits "if not cond then skip"; print the fall-through as the body
        then_start, else_start = fall, taken
        c = negate(cond)
        then = self.region(then_start, join, inloop) if then_start != join else None
        els = self.region(else_start, join, inloop) if else_start != join else None
        if then is None and els is not None:
            c, then, els = cond, els, None
        return If(c, then, els), join

    def join_of(self, a, stop, inloop):
        """Immediate post-dominator of a within the current region."""
        n = self.nodes[a]
        reach = []
        for s in n.succs:
            seen = set()
            stack = [s] if s not in self.dom.get(a, ()) else []
            while stack:
                x = stack.pop()
                if x in seen or x not in self.nodes or x == stop:
                    continue
                if inloop and x not in inloop:
                    continue
                seen.add(x)
                # back edges (to a node that dominates x) lead to a loop head,
                # not to the join of this If
                stack.extend(y for y in self.nodes[x].succs if y not in self.dom.get(x, ()))
            reach.append(seen)
        common = set.intersection(*reach) if reach else set()
        common -= self.emitted
        if not common:
            return stop
        order = rpo(self.nodes, self.entry)
        for x in order:
            if x in common:
                return x
        return stop

    def loop(self, head, inloop):
        self.active.add(head)
        try:
            return self._loop(head, inloop)
        finally:
            self.active.discard(head)

    def _loop(self, head, inloop):
        body_set = self.loops[head]
        inner = frozenset(body_set)
        n = self.nodes[head]
        self.emitted.add(head)
        latches = [p for p in n.preds if p in body_set]
        exits = [s for a in body_set for s in self.nodes[a].succs if s not in body_set]
        # the loop's own exit: the most common target (an Exit Do elsewhere)
        targets = [x for x in exits if x in self.nodes and self.nodes[x].kind != "exit"] or exits
        after = max(targets, key=targets.count) if targets else None
        self.follows[inner] = after
        pre = self.stmts.get(head, [])
        # top-tested: the header only tests
        if n.kind == "cond" and not pre and head not in latches and \
                (n.succs[0] not in body_set or n.succs[1] not in body_set):
            taken, fall = n.succs
            cond = self.brs[head].cond
            if taken in body_set:
                test, cont, after = cond, taken, fall
            else:
                test, cont, after = negate(cond), fall, taken
            self.follows[inner] = after
            body = self.region(cont, head, inner)
            lp = Loop("while", test, body)
            if head in self.labels:
                lp.head = head          # GoTo the test from inside (gh_zip CompressArchive)
            # (or from code structured later: see run)
            self.__dict__.setdefault("top_loops", []).append((lp, head))
            return lp, after
        # bottom-tested: a single latch that is a two-way exit test
        if len(latches) == 1:
            latch = latches[0]
            ln = self.nodes[latch]
            if ln.kind == "cond" and (ln.succs[0] not in body_set or ln.succs[1] not in body_set):
                taken, fall = ln.succs
                cond = self.brs[latch].cond
                if taken == head:
                    test, after = cond, fall
                else:
                    test, after = negate(cond), taken
                self.follows[inner] = after
                if latch == head:
                    body = Seq([Block(head, pre)])
                else:
                    self.emitted.discard(head)
                    self.latches[inner] = latch
                    try:
                        body = self.region(head, None, inner, until=latch)
                    finally:
                        del self.latches[inner]
                    if latch not in self.emitted:
                        # every path reached the test from inside a branch (GoTo):
                        # its statements end the body, under their label
                        self.emitted.add(latch)
                        body = Seq(list(body.items) + [Block(latch, self.stmts.get(latch, []))])
                return Loop("loopwhile", test, body), after
        self.emitted.discard(head)
        body = self.region(head, None, inner)
        return Loop("forever", None, body), after


# ------------------------------------------------------------------ loop recovery
class For:
    def __init__(self, var, start, end, step, body):
        self.var, self.start, self.end, self.step, self.body = var, start, end, step, body


class ForEach:
    def __init__(self, var, coll, body):
        self.var, self.coll, self.body = var, coll, body


def each_call(e, prefix):
    """__vbaForEachCollVar(&enum, &v, coll) / __vbaNextEachCollVar(&enum, &v)."""
    return isinstance(e, ir.Call) and e.name.startswith(prefix) and e.obj is None


def unaddr(e):
    return e.e if isinstance(e, ir.AddrOf) else e


def next_at_ends(body, is_next):
    """[(block, statement)] when every path through body that reaches its end
    runs a statement is_next accepts last (the others leave: Exit Do, Exit
    Sub, GoTo: gh_zip Extract), else None."""
    found = []

    def ends(r):
        if r is None:
            return False
        if isinstance(r, Block):
            live = [x for x in r.stmts if not isinstance(x, ir.Free)]
            if live and is_next(live[-1]):
                found.append((r, live[-1]))
                return True
            # (a register's copy after it, fast code: t = NextEach(...): esi = o1057)
            if len(live) > 1 and is_next(live[-2]) and plain_copy(live[-1]) and \
                    not any(mentions(e, y) for e in live[-1].exprs()
                            for y in unaddr_all(live[-2].src.args)):
                found.append((r, live[-2]))
                return True
            return False
        if isinstance(r, (Exit, ExitLoop, Goto)):
            return True             # leaves the loop body
        if isinstance(r, Seq):
            items = [x for x in r.items if not (isinstance(x, Block) and not x.stmts)]
            for i in range(len(items) - 1, -1, -1):
                x = items[i]
                if isinstance(x, Block) and not any(
                        not isinstance(y, ir.Free) for y in x.stmts):
                    continue
                return ends(x)
            return False
        if isinstance(r, If):
            return r.els is not None and ends(r.then) and ends(r.els)
        return False
    return found if ends(body) and found else None


FOR_INIT = {"VarForInit": "VarForNext", "CyForInit": "CyForNext"}


def as_runtime_for(guard, lp):
    """If CyForInit(&c, e, s, start, e, s) <> 0 Then Do ... Loop While CyForNext(&c, e, s) <> 0, with the
    results tested directly (no temporary, as in optimised code) -> For c = start To e Step s."""
    def call_of(c):
        if isinstance(c, BinOp) and c.op == "<>" and isinstance(c.b, ir.Const) and c.b.value == 0 and \
                isinstance(c.a, ir.Call):
            return c.a
        return None
    a, b = call_of(guard), call_of(lp.cond)
    if a is None or b is None or a.name not in FOR_INIT or b.name != FOR_INIT[a.name] or len(a.args) != 6:
        return None
    fa = [unaddr(x) for x in a.args]
    if [unaddr(x) for x in b.args] != fa[:3]:
        return None
    return For(fa[0], fa[3], fa[4], fa[5], lp.body)


def as_varfor(prev, lp):
    """t = VarForInit(&v, &s1, &s2, &start, &end, &step): Do While t <> 0: ...:
    t = VarForNext(&v, &s1, &s2): Loop -> For v = start To end Step step ...
    Next (a Variant counter; runtime.py, tests/t18varfor)"""
    if getattr(lp, "head", None) is not None:
        return None                 # a GoTo to the test
    c = lp.cond
    if not (isinstance(c, BinOp) and c.op == "<>" and isinstance(c.a, ir.Var) and
            isinstance(c.b, ir.Const) and c.b.value == 0):
        return None
    t = c.a
    blk = next((b for b in reversed(prev) if isinstance(b, Block) and b.stmts), None)
    if blk is None:
        return None
    first = blk.stmts[-1]
    if not (isinstance(first, ir.Assign) and first.dst == t and
            isinstance(first.src, ir.Call) and first.src.name in FOR_INIT and
            len(first.src.args) == 6):
        return None
    fa = [unaddr(x) for x in first.src.args]
    state = fa[:3]
    next_name = FOR_INIT[first.src.name]

    def is_next(x):
        return isinstance(x, ir.Assign) and x.dst == t and isinstance(x.src, ir.Call) and \
            x.src.name == next_name and [unaddr(y) for y in x.src.args] == state
    found = next_at_ends(lp.body, is_next)
    if not found:
        return None
    blk.stmts = blk.stmts[:-1]
    for b, n in found:
        b.stmts = [x for x in b.stmts if x is not n]
    return For(fa[0], fa[3], fa[4], fa[5], lp.body)


def plain_copy(s):
    """x = y or x = 1: nothing that runs code."""
    return isinstance(s, ir.Assign) and isinstance(s.dst, ir.Var) and isinstance(s.src, (ir.Var, ir.Const))


def unaddr_all(args):
    return [unaddr(x) for x in args]


def as_foreach(prev, lp):
    """t = ForEach*(enum, v, c): Do While t <> 0: ...: t = NextEach*(enum, v): Loop
    -> For Each v In c ... Next"""
    if getattr(lp, "head", None) is not None:
        return None                 # a GoTo to the test
    c = lp.cond
    if not (isinstance(c, BinOp) and c.op == "<>" and isinstance(c.a, ir.Var) and
            isinstance(c.b, ir.Const) and c.b.value == 0):
        return None
    t = c.a
    blk = next((b for b in reversed(prev) if isinstance(b, Block) and b.stmts), None)
    if blk is None:
        return None
    # (registers filled in the same block before the loop: t = ForEachCollVar(...): esi = total, fast code)
    k = len(blk.stmts) - 1
    while k > 0 and not (isinstance(blk.stmts[k], ir.Assign) and blk.stmts[k].dst == t) and \
            len(blk.stmts) - k <= 3 and plain_copy(blk.stmts[k]):
        k -= 1
    first = blk.stmts[k]
    if not (isinstance(first, ir.Assign) and first.dst == t and
            each_call(first.src, "ForEach") and len(first.src.args) >= 3):
        return None
    if any(mentions(e, y) for z in blk.stmts[k + 1:] for e in z.exprs()
           for y in unaddr_all(first.src.args)):
        return None
    # (enum state..., &v, collection): ForEachCollVar passes one state pointer,
    # ForEachVar four (gh_json: For Each json_Key In JsonValue.Keys)
    nstate = len(first.src.args) - 2
    def is_next(x):
        return isinstance(x, ir.Assign) and x.dst == t and each_call(x.src, "NextEach") and \
            len(x.src.args) == nstate + 1
    found = next_at_ends(lp.body, is_next)
    if not found:
        return None
    nexts = [n for _, n in found]
    fa = [unaddr(x) for x in first.src.args]
    enum, var, coll = fa[:nstate], fa[nstate], fa[nstate + 1]
    if any([unaddr(x) for x in n.src.args[:nstate]] != enum or
           unaddr(n.src.args[nstate]) != var for n in nexts):
        return None
    blk.stmts = blk.stmts[:k] + blk.stmts[k + 1:]
    for b, n in found:
        b.stmts = [x for x in b.stmts if x is not n]
    return ForEach(var, coll, lp.body)


def fold_pow2_mod(prev, x):
    """t = v And &H80000001: If t < 0 Then t = ((t - 1) Or -2) + 1  ->  t = v Mod 2
    (the compiler's signed remainder by a power of two, 2^k for any k)."""
    if not (isinstance(x, If) and x.els is None and isinstance(x.cond, BinOp) and
            x.cond.op == "<" and isinstance(x.cond.a, ir.Var) and
            isinstance(x.cond.b, ir.Const) and x.cond.b.value == 0):
        return False
    t = x.cond.a
    body = branch_assigns(x.then)
    if not body or len(body) != 1:
        return False
    fix = body[0]
    # ((t - 1) Or -m) + 1
    e = fix.src if isinstance(fix, ir.Assign) and fix.dst == t else None
    if not (isinstance(e, BinOp) and e.op == "+" and e.b == ir.Const(1) and
            isinstance(e.a, BinOp) and e.a.op == "Or" and isinstance(e.a.b, ir.Const) and
            isinstance(e.a.a, BinOp) and e.a.a.op == "-" and e.a.a.a == t and
            e.a.a.b == ir.Const(1)):
        return False
    m = -e.a.b.value
    if m < 2 or m & (m - 1):
        return False
    blk = next((b for b in reversed(prev) if isinstance(b, Block) and b.stmts), None)
    if blk is None:
        return False
    d = blk.stmts[-1]
    mask = (0x80000000 | (m - 1))
    if not (isinstance(d, ir.Assign) and d.dst == t and isinstance(d.src, BinOp) and
            d.src.op == "And" and isinstance(d.src.b, ir.Const) and
            d.src.b.value & 0xffffffff == mask):
        return False
    blk.stmts[-1] = ir.Assign(t, BinOp("Mod", d.src.a, ir.Const(m)))
    blk.stmts[-1].va = getattr(d, "va", None)
    return True


def branch_assigns(r):
    """The statements of a branch that is a single straight block, or None."""
    if r is None:
        return []
    if isinstance(r, Block):
        return r.stmts
    if isinstance(r, Seq):
        items = [x for x in r.items if not (isinstance(x, Block) and not x.stmts)]
        if all(isinstance(x, Block) for x in items):
            return [s for x in items for s in x.stmts]
    return None


def bool_value(stmts, var=None):
    """A branch that only sets a register to a 0/1/-1 constant."""
    if len(stmts) != 1 or not isinstance(stmts[0], ir.Assign):
        return None
    s = stmts[0]
    if not (isinstance(s.dst, ir.Var) and s.dst.space == "reg" and isinstance(s.src, ir.Const)):
        return None
    if var is not None and s.dst != var:
        return None
    if s.src.value not in (0, 1, -1):
        return None
    return s.dst, s.src.value


def last_block(r):
    if isinstance(r, Block):
        return r
    if isinstance(r, Seq):
        for x in reversed(r.items):
            if isinstance(x, Block) and x.stmts:
                return x
            if isinstance(x, Exit) or (isinstance(x, Block) and not x.stmts):
                continue
            return None
    return None


def hoist_common(x):
    """Identical last statements of both branches move after the If."""
    moved = []
    if leaves(x.then) or leaves(x.els):
        # last_block looks past a trailing Exit: the statement runs before it,
        # moving it behind the If skips it (gh_dict Let CompareMode lost
        # dict_pCompareMode = Value behind two Exit Property)
        return moved
    while True:
        a, b = last_block(x.then), last_block(x.els)
        if a is None or b is None or not a.stmts or not b.stmts:
            break
        sa, sb = a.stmts[-1], b.stmts[-1]
        if not (isinstance(sa, (ir.Assign, ir.ExprStmt)) and type(sa) is type(sb)
                and repr(sa) == repr(sb)):
            break
        moved.insert(0, sa)
        a.stmts.pop()
        b.stmts.pop()
    return moved


def tested(cond, var):
    """cond tests var for truth: var <> 0, -var <> 0, var = 0 ..."""
    if isinstance(cond, BinOp) and cond.op in ("<>", "=") and isinstance(cond.b, ir.Const) \
            and cond.b.value == 0:
        e = cond.a
        if isinstance(e, UnOp) and e.op == "-":
            e = e.a
        if e == var:
            return cond.op == "<>"
    return None


def leaves(r):
    """True when control never falls out of the end of region r."""
    if isinstance(r, Seq):
        return bool(r.items) and leaves(r.items[-1])
    if isinstance(r, (Exit, ExitLoop, Goto)):
        return True
    if isinstance(r, If):
        return r.els is not None and leaves(r.then) and leaves(r.els)
    return False


def flatten_else(items):
    """If c Then ... Exit ... Else B End If  ->  If c Then ... End If : B, and the other way round
    (If c Then B Else ... Exit Sub: what stops a For's last statement being its step, For t...: If
    a(t) <> -1 Then t = t + 1 Else a(t) = n: Exit Sub: DarwinBots Add_Bot)."""
    out = []
    for x in items:
        if isinstance(x, If) and x.els is not None and x.then is not None and leaves(x.then) \
                and not leaves(x.els):
            out.append(If(x.cond, x.then, None))
            out.extend(x.els.items if isinstance(x.els, Seq) else [x.els])
            continue
        if isinstance(x, If) and x.els is not None and x.then is not None and leaves(x.els) \
                and not leaves(x.then):
            out.append(If(negate(x.cond), x.els, None))
            out.extend(x.then.items if isinstance(x.then, Seq) else [x.then])
            continue
        out.append(x)
    return out


def recover(r):
    """Rewrite rotated loops: If c Then (Do ... Loop While c) -> Do While c,
    and the For pattern (init, guard, increment) -> For ... Next.  Also folds
    a comparison the compiler materialised as 1/0 before testing it."""
    if isinstance(r, Seq):
        items = [recover(x) for x in r.items]
        # If c Then r = 1 Else r = 0 ; If r <> 0 Then ... -> If c Then ...
        merged = []
        i = 0
        while i < len(items):
            x = items[i]
            if isinstance(x, If) and x.els is not None:
                moved = hoist_common(x)
                bt, be = branch_assigns(x.then), branch_assigns(x.els)
                vt = bool_value(bt) if bt is not None else None
                ve = bool_value(be, vt[0]) if be is not None and vt else None
                nxt = [y for y in items[i + 1:] if not (isinstance(y, Block) and not y.stmts)]
                if vt and ve and vt[1] != ve[1] and nxt and isinstance(nxt[0], If):
                    j = items.index(nxt[0])
                    between = [y for y in items[i + 1:j] if isinstance(y, Block)]
                    t = tested(nxt[0].cond, vt[0])
                    if t is not None and not any(y.stmts for y in between):
                        c = x.cond if (vt[1] != 0) == t else negate(x.cond)
                        if moved:
                            merged.append(Block(None, moved))
                        merged.append(If(c, nxt[0].then, nxt[0].els))
                        i = j + 1
                        continue
                if moved:
                    merged.append(x)
                    merged.append(Block(None, moved))
                    i += 1
                    continue
            merged.append(x)
            i += 1
        items = flatten_else(merged)
        out = []
        for x in items:
            if fold_pow2_mod(out, x):
                continue
            if isinstance(x, Loop) and x.kind == "loopwhile":
                f = as_for(out, None, x)            # constant bounds: no guard needed
                if f is not None:
                    out.append(f)
                    continue
            if isinstance(x, Loop) and x.kind == "while":
                f = as_foreach(out, x) or as_varfor(out, x)
                if f is not None:
                    out.append(f)
                    continue
                f = as_for(out, x.cond, x, top=True)      # test at the top, step at the end
                if f is not None:
                    out.append(f)
                    continue
            if isinstance(x, If) and x.els is None:
                lp = only_loop(x.then)
                if lp is not None:
                    f = as_runtime_for(x.cond, lp) or as_for(out, x.cond, lp)
                    if f is not None:
                        out.append(f)
                        continue
                    if same_cond(x.cond, lp.cond):
                        w = Loop("while", lp.cond, lp.body)
                        out.append(as_foreach(out, w) or as_varfor(out, w) or w)
                        continue
            out.append(x)
        return Seq(out)
    if isinstance(r, If):
        return If(r.cond, recover(r.then) if r.then else None, recover(r.els) if r.els else None)
    if isinstance(r, Loop):
        return keep_head(r, Loop(r.kind, r.cond, recover(r.body)))
    return r


def only_loop(r):
    if isinstance(r, Loop) and r.kind == "loopwhile":
        return r
    if isinstance(r, Seq):
        items = [x for x in r.items if not (isinstance(x, Block) and not x.stmts) and
                 not isinstance(x, Exit)]
        if len(items) == 1:
            return only_loop(items[0])
    return None


FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "<>": "<>"}


def norm(c, var=None):
    """Comparison with var (or the left operand) on the left."""
    if not isinstance(c, BinOp) or c.op not in FLIP:
        return c
    if var is not None and c.b == var and c.a != var:
        return BinOp(FLIP[c.op], c.b, c.a)
    return c


def same_cond(a, b):
    if a == b:
        return True
    if isinstance(a, BinOp) and isinstance(b, BinOp) and a.op in FLIP:
        return BinOp(FLIP[a.op], a.b, a.a) == b
    return False


def keep_head(old, new):
    """A rebuilt loop keeps the label of its header test."""
    if getattr(old, "head", None) is not None:
        new.head = old.head
    return new


def as_for(prev, guard, lp, top=False):
    """prev[-1] ends with 'i = start'; guard tests i against end; the loop
    body ends with 'i = i + step' and loops while i <= end (>= for step<0)."""
    body = lp.body
    if getattr(lp, "head", None) is not None and lp.kind == "while":
        return None                 # a GoTo to the test: For would start over
    stmts = flat_blocks(body)
    if not stmts:
        return None
    last_blk, inc = stmts[-1]
    if not isinstance(inc, ir.Assign) or not isinstance(inc.dst, ir.Var):
        return None
    var = inc.dst
    src = inc.src
    if not (isinstance(src, BinOp) and src.op in ("+", "-") and src.a == var and
            isinstance(src.b, ir.Const)):
        return None
    step = src.b.value if src.op == "+" else -src.b.value
    c = norm(lp.cond, var)
    want = "<=" if step > 0 else ">="
    if not (isinstance(c, BinOp) and c.op == want and c.a == var):
        return None
    if top:
        guard = None                        # the loop test itself is the guard
        top_ok = True
    else:
        top_ok = False
    if not stable_limit(c.b, body):
        return None
    start_test = False
    if guard is not None:
        g = norm(guard, var)
        if not (isinstance(g, BinOp) and g.op == want and g.a == var and g.b == c.b):
            start_test = True           # (perhaps the start's constant instead of the variable)
    # the start value is the last assignment to var before the guard
    start = None
    for blk in reversed(prev):
        if not isinstance(blk, Block):
            return None                 # (a test, a label or a loop between: not one statement)
        for i in range(len(blk.stmts) - 1, -1, -1):
            s = blk.stmts[i]
            if isinstance(s, ir.Assign) and s.dst == var:
                if any(mentions(e, var) for y in blk.stmts[i + 1:]
                       for e in getattr(y, "exprs", lambda: ())()):
                    return None         # read before the loop: it can't move into a For
                start = s.src
                if start_test:
                    # v1C = 1: If v20 >= 1 Then Do ... Loop While v1C <= v20 (fast code)
                    h = norm(guard, c.b)
                    if not (isinstance(h, BinOp) and h.op == FLIP[want] and h.a == c.b and h.b == start):
                        return None
                blk.stmts = blk.stmts[:i] + blk.stmts[i + 1:]
                break
        if start is not None:
            break
    if start is None:
        return None
    if guard is None and not top_ok:
        # without a guard the loop runs at least once: only valid when the
        # bounds are constants that make the first test true
        if not (isinstance(start, ir.Const) and isinstance(c.b, ir.Const) and
                (start.value <= c.b.value if step > 0 else start.value >= c.b.value)):
            blk.stmts.insert(i, ir.Assign(var, start))      # undo
            return None
    last_blk.stmts = [x for x in last_blk.stmts if x is not inc]
    f = For(var, start, c.b, step, body)
    f.limit_reads = 2 if guard is not None else 1       # (see fold_for_limits)
    return f


def fold_for_limits(r, lf, reads):
    """For i = 1 To t just after t = <value>, t read nowhere else: a For reads its limit once, so t
    can be the value again.  (simplify.inline_temps can't: until the loop is known to be a For, a
    test reading a field would read it every time.)  reads: simplify.var_index's counts."""
    if isinstance(r, Seq):
        items = [fold_for_limits(x, lf, reads) for x in r.items]
        for i, x in enumerate(items):
            if not (isinstance(x, For) and isinstance(x.end, ir.Var) and
                    x.end.space in ("local", "reg", "tmp", "stk") and
                    reads.get(x.end.key()) == getattr(x, "limit_reads", None) and
                    not lf.is_named_local(x.end)):
                continue
            j = i - 1
            while j >= 0 and isinstance(items[j], Block) and not items[j].stmts:
                j -= 1
            d = items[j].stmts[-1] if j >= 0 and isinstance(items[j], Block) else None
            if isinstance(d, ir.Assign) and isinstance(d.dst, ir.Var) and \
                    d.dst.key() == x.end.key() and d.kind != "set" and not getattr(d, "fixstr", None):
                x.end = d.src
                items[j].stmts = items[j].stmts[:-1]
        return Seq(items)
    if isinstance(r, If):
        return If(r.cond, fold_for_limits(r.then, lf, reads) if r.then else None,
                  fold_for_limits(r.els, lf, reads) if r.els else None)
    if isinstance(r, Loop):
        return keep_head(r, Loop(r.kind, r.cond, fold_for_limits(r.body, lf, reads)))
    if isinstance(r, (For, ForEach)):
        r.body = fold_for_limits(r.body, lf, reads)
    return r


def region_stmts(r):
    """Every statement in region r."""
    if isinstance(r, Seq):
        for x in r.items:
            yield from region_stmts(x)
    elif isinstance(r, Block):
        yield from r.stmts
    elif isinstance(r, If):
        for x in (r.then, r.els):
            if x is not None:
                yield from region_stmts(x)
    elif isinstance(r, (Loop, For, ForEach)):
        yield from region_stmts(r.body)


def stable_limit(end, body):
    """A For reads its limit once; a loop's test every time.  So a limit that reads memory is a
    Do While's (a For's value is in a temporary: fold_for_limits), and so is a variable the loop
    assigns."""
    if isinstance(end, Const):
        return True
    keys = set()
    for x in end.walk():
        if isinstance(x, (ir.Deref, ir.Index, ir.Field, ir.Call, ir.Prop)) or \
                isinstance(x, ir.Var) and x.space in ("global", "member"):
            return False
        if isinstance(x, ir.Var):
            keys.add(x.key())
    for s in region_stmts(body):
        if isinstance(s, ir.Assign) and isinstance(s.dst, ir.Var) and s.dst.key() in keys:
            return False
        for e in getattr(s, "exprs", lambda: ())():
            for x in e.walk():
                if isinstance(x, ir.AddrOf) and isinstance(x.e, ir.Var) and x.e.key() in keys:
                    return False
    return True


def flat_blocks(r):
    """(block, stmt) for every statement of r when r is only blocks at its
    end; [] when anything else (an If, a loop) comes after the last one."""
    def walk(r):
        if isinstance(r, Seq):
            return [y for x in r.items for y in walk(x)]
        if isinstance(r, Block):
            return [(r, s) for s in r.stmts]
        return [(None, None)]
    out = walk(r)
    return [x for x in out if x[1] is not None] if out and out[-1][1] is not None else []


# ------------------------------------------------------------------ Select Case
class Select:
    """Select Case subject / Case clauses... / Case Else."""

    def __init__(self, subject, cases, els):
        self.subject, self.cases, self.els = subject, cases, els


def mentions(e, s):
    return any(x == s for x in e.walk())


def clauses(cond, s):
    """Case clauses equivalent to cond as a test of subject s, or None:
    ("eq", c), ("range", lo, hi), ("is", op, c)."""
    if isinstance(cond, BinOp) and cond.op == "Or":
        a, b = clauses(cond.a, s), clauses(cond.b, s)
        return a + b if a is not None and b is not None else None
    if isinstance(cond, BinOp) and cond.op == "And":
        a, b = norm(cond.a, s), norm(cond.b, s)
        if isinstance(a, BinOp) and isinstance(b, BinOp) and a.a == s and b.a == s:
            if a.op == ">=" and b.op == "<=":
                lo, hi = a.b, b.b
            elif a.op == "<=" and b.op == ">=":
                lo, hi = b.b, a.b
            else:
                return None
            if not mentions(lo, s) and not mentions(hi, s) and simple(lo) and simple(hi):
                return [("range", lo, hi)]
        return None
    c = norm(cond, s)
    if isinstance(c, BinOp) and c.op in FLIP and c.a == s and not mentions(c.b, s) and simple(c.b):
        return [("eq", c.b)] if c.op == "=" else [("is", c.op, c.b)]
    return None


def simple(e):
    """A Case value: no calls (VB evaluates them only when the case is tried)."""
    return not any(isinstance(x, (ir.Call, ir.Prop)) for x in e.walk())


def subject_of(cond):
    """The tested expression of a comparison chain (left side of the first)."""
    while isinstance(cond, BinOp) and cond.op in ("Or", "And"):
        cond = cond.a
    if isinstance(cond, BinOp) and cond.op in FLIP:
        if isinstance(cond.a, (ir.Var, ir.Deref, ir.Prop, ir.Index)) and simple(cond.a):
            return cond.a
    return None


def sole(r):
    """r is (a Seq holding only) one If."""
    if isinstance(r, If):
        return r
    if isinstance(r, Seq):
        items = [x for x in r.items if not (isinstance(x, Block) and not x.stmts)]
        if len(items) == 1 and isinstance(items[0], If):
            return items[0]
    return None


def straighten(x, s):
    """If (s <> k) Then <the rest of the chain> Else B  ->  If s = k Then B Else ...:
    the compiler often tests the inverse; put the case first."""
    t, e = sole(x.then), sole(x.els) if x.els is not None else None
    if t is not None and e is None and x.els is not None and subject_of(t.cond) == s:
        n = negate(x.cond)
        if clauses(n, s) is not None:
            return If(n, x.els, x.then)
    c = norm(x.cond, s)
    if isinstance(c, BinOp) and c.op == "<>" and c.a == s and x.els is not None and \
            (e is None or subject_of(e.cond) != s):
        return If(BinOp("=", c.a, c.b), x.els, x.then)     # Case k ... Case Else
    cl = clauses(x.cond, s)
    if cl and len(cl) == 2 and x.els is not None and cl[0][:2] == ("is", "<") and \
            cl[1][:2] == ("is", ">") and (e is None or subject_of(e.cond) != s):
        # Case Is < lo, Is > hi ... Case Else B: really Case lo To hi: B
        lo, hi = cl[0][2], cl[1][2]
        rng = BinOp("And", BinOp(">=", s, lo), BinOp("<=", s, hi))
        return If(rng, x.els, x.then)
    return x


def dead_end(r):
    """An empty region."""
    if r is None:
        return True
    if isinstance(r, Seq):
        return all(dead_end(x) for x in r.items)
    return isinstance(r, Block) and not r.stmts


def selects(r):
    """Rewrite If/ElseIf chains that test one subject into Select Case."""
    if isinstance(r, Seq):
        return Seq([selects(x) for x in r.items])
    if isinstance(r, Loop):
        return keep_head(r, Loop(r.kind, r.cond, selects(r.body)))
    if isinstance(r, For):
        return For(r.var, r.start, r.end, r.step, selects(r.body))
    if isinstance(r, ForEach):
        return ForEach(r.var, r.coll, selects(r.body))
    if not isinstance(r, If):
        return r
    s = subject_of(r.cond)
    x = r
    chain, els = [], None
    while True:
        if s is not None:
            x = straighten(x, s)
        cl = clauses(x.cond, s) if s is not None else None
        if cl is None:
            break
        chain.append((cl, x.then))
        nxt = sole(x.els) if x.els is not None else None
        if nxt is not None and subject_of(nxt.cond) == s and clauses(
                straighten(nxt, s).cond, s) is not None:
            x = nxt
            continue
        els = x.els
        break
    rich = any(len(cl) > 1 or cl[0][0] != "eq" for cl, _ in chain)
    arms = len(chain) + (1 if els is not None and not dead_end(els) else 0)
    if arms >= 3 or (len(chain) >= 2 and rich):
        cases = [(cl, selects(b) if b is not None else None) for cl, b in chain]
        return Select(s, cases, selects(els) if els is not None else None)
    return If(r.cond, selects(r.then) if r.then else None, selects(r.els) if r.els else None)


# ------------------------------------------------------------ GoTo cleanup
def goto_targets(r, out):
    """Count the GoTos (and Resumes) to each label in region r."""
    if isinstance(r, (Goto, ResumeTo)):
        out[r.target] = out.get(r.target, 0) + 1
    elif isinstance(r, Seq):
        for x in r.items:
            goto_targets(x, out)
    elif isinstance(r, If):
        goto_targets(r.then, out) if r.then else None
        goto_targets(r.els, out) if r.els else None
    elif isinstance(r, (Loop, For, ForEach)):
        goto_targets(r.body, out)
    elif isinstance(r, Select):
        for _, b in r.cases:
            goto_targets(b, out)
        if r.els:
            goto_targets(r.els, out)
    return out


def skip_gotos(r, labels, counts=None):
    """If c Then GoTo L: <stmts>: L: ...   ->   If Not c Then <stmts>: L: ...
    when that GoTo is L's only one (the jump just skips the statements)."""
    if counts is None:
        counts = goto_targets(r, {})
    if isinstance(r, Seq):
        items = [skip_gotos(x, labels, counts) for x in r.items]
        out = []
        i = 0
        while i < len(items):
            x = items[i]
            tgt = None
            if isinstance(x, If) and x.els is None and x.then is not None:
                body = [y for y in (x.then.items if isinstance(x.then, Seq) else [x.then])
                        if not (isinstance(y, Block) and not y.stmts)]
                if len(body) == 1 and isinstance(body[0], Goto):
                    tgt = body[0].target
            if tgt is not None and counts.get(tgt) == 1:
                j = next((k for k in range(i + 1, len(items))
                          if isinstance(items[k], Block) and items[k].va == tgt), None)
                if j is not None and j > i + 1:
                    out.append(If(negate(x.cond), Seq(items[i + 1:j]), None))
                    labels.discard(tgt)
                    counts[tgt] = 0
                    i = j
                    continue
            out.append(x)
            i += 1
        return Seq(out)
    if isinstance(r, If):
        return If(r.cond, skip_gotos(r.then, labels, counts) if r.then else None,
                  skip_gotos(r.els, labels, counts) if r.els else None)
    if isinstance(r, Loop):
        return keep_head(r, Loop(r.kind, r.cond, skip_gotos(r.body, labels, counts)))
    if isinstance(r, For):
        return For(r.var, r.start, r.end, r.step, skip_gotos(r.body, labels, counts))
    if isinstance(r, ForEach):
        return ForEach(r.var, r.coll, skip_gotos(r.body, labels, counts))
    return r
