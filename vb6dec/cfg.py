"""Clean control-flow graph: compiler-generated checks removed.

Removed:
  * HRESULT checks after COM calls (test eax,eax / jge ok / __vbaHresultCheckObj)
  * edges into blocks that only raise (overflow, bounds, FP exception)
  * the Pentium FDIV workaround (cmp [__adjust_fdiv],0 / jne _adj_fdiv_*)
Resolved:
  * "push cont; jmp cleanup" local calls of the exit cleanup routine
"""
import os
from capstone import x86

from .disasm import decode, NORETURN

CHECK_FUNCS = {"__vbaHresultCheckObj", "__vbaHresultCheck"}


class Node:
    def __init__(self, start, insns, succs, kind):
        self.start = start
        self.insns = insns
        self.succs = succs          # for 'cond': [taken, fallthrough]
        self.kind = kind            # 'fall', 'jmp', 'cond', 'ret', 'raise', 'exit'
        self.preds = []
        self.cleanup = None         # for 'exit': VA of the cleanup routine

    def __repr__(self):
        return "<Node %#x %s %s>" % (self.start, self.kind, [hex(s) for s in self.succs])


def import_name(model, ins):
    """Name of the runtime function a call/jmp instruction targets."""
    im = model.im
    op = ins.operands[0]
    if op.type == x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
        return im.imports.get(op.mem.disp)
    if op.type == x86.X86_OP_IMM:
        t = decode(im, op.imm)
        if t is not None and t.mnemonic == "jmp" and t.operands[0].type == x86.X86_OP_MEM \
           and t.operands[0].mem.base == 0:
            return im.imports.get(t.operands[0].mem.disp)
    return None


def reg_import(model, proc, va, reg):
    """Import a register holds at va."""
    from .disasm import iat_at_flow
    return iat_at_flow(proc, va, reg)


def call_name(model, proc, ins):
    n = import_name(model, ins)
    if n is None and proc is not None and ins.operands and ins.operands[0].type == x86.X86_OP_REG:
        n = reg_import(model, proc, ins.address, ins.operands[0].reg)
    return n


def calls_in(model, block, proc=None):
    for ins in block.insns:
        if ins.mnemonic in ("call", "jmp"):
            n = call_name(model, proc, ins)
            if n:
                yield n


def is_raise(model, block, proc=None):
    """The block ends in a call that never returns.  Any such call counts,
    not just the last instruction: when blocks were cut, "call ebp" could
    not be resolved yet (the last write to ebp in address order was an
    epilogue's pop on another path), so code after it stayed in the block."""
    for ins in block.insns:
        if ins.mnemonic in ("call", "jmp"):
            n = call_name(model, proc, ins)
            if n in NORETURN:
                return True
    return False


def resumes(model, block, proc=None):
    """The block ends "call __vbaResume; jmp label" (Resume label): its jump
    must stay a jump to the label itself - no copied tail, no merging the
    label's code into it (structure.Structurer prints Resume label)."""
    insns = block.insns
    if len(insns) < 2 or insns[-1].mnemonic != "jmp":
        return False
    calls = [i for i in insns if i.mnemonic == "call"]
    return bool(calls) and call_name(model, proc, calls[-1]) == "__vbaResume"


def is_check(model, block, proc=None):
    """Block that only reports a failed HRESULT."""
    names = list(calls_in(model, block, proc))
    insns = block.insns[:-1] if block.insns and block.insns[-1].mnemonic == "jmp" else block.insns
    return bool(names) and names[-1] in CHECK_FUNCS and \
        all(i.mnemonic in ("push", "mov", "lea", "call") for i in insns)


def refetch_check(model, proc, blocks, start, join):
    """Does the code from start on only report a failed HRESULT, fetching the object again for that
    through its array's bounds checks, and come back to join?  (Optimised code: mov edi, eax;
    test edi, edi; fnclex; jge ok; then rob(robfocus).console for the report, several blocks long:
    DarwinBots openconsole printed If rob(i).console.textout("...") < 0 Then.)"""
    plain = ("mov", "lea", "shl", "sub", "add", "imul", "movsx", "movzx", "cmp", "test", "push")
    seen, work, reported = set(), [start], False
    while work:
        a = work.pop()
        if a == join or a in seen:
            continue
        seen.add(a)
        b = blocks.get(a)
        if b is None or len(seen) > 10 or not b.insns:
            return False
        for i in b.insns:
            m = i.mnemonic
            if m == "call":
                name = call_name(model, proc, i)
                if name in CHECK_FUNCS:
                    reported = True
                elif not is_bounds_tail(model, type("B", (), {"insns": [i]})(), proc):
                    return False
            elif m not in plain and not m.startswith("j"):
                return False
        if b.insns[-1].mnemonic in ("ret",):
            return False
        work.extend(b.succs)
    return reported


def sa_guard(n, blocks, taken, fall):
    """The start of a SAFEARRAY's bounds check, going to its error when there is no array or not of
    these dimensions: test ecx, ecx; je E; cmp word ptr [ecx], 1; jne E.  E is "call <register>" and
    a reload, nothing pushed.  (Its register may be used for other things in other Cases, so its name
    can't be told from the addresses: DarwinBots ExecuteDNA rob(a1).ga(n), an If on the array.)"""
    if len(n.insns) < 2 or taken not in blocks:
        return False
    e = blocks[taken].insns
    if not e or not all(i.mnemonic in ("mov", "lea", "call", "jmp") for i in e):
        return False
    calls = [i for i in e if i.mnemonic == "call"]
    if len(calls) != 1 or calls[0].operands[0].type != x86.X86_OP_REG:
        return False

    def dims_cmp(i, reg=None):
        return i.mnemonic == "cmp" and i.operands[0].type == x86.X86_OP_MEM and \
            i.operands[0].size == 2 and i.operands[0].mem.disp == 0 and \
            i.operands[0].mem.index == 0 and i.operands[1].type == x86.X86_OP_IMM and \
            0 < i.operands[1].imm < 61 and (reg is None or i.operands[0].mem.base == reg)
    j, c = n.insns[-1], n.insns[-2]
    if j.mnemonic == "jne" and dims_cmp(c):
        return True
    # (the test of the array, and its dimensions further on, with the error block in common: the
    # array fetched again in between, rob(i).ga(j) in optimised code)
    if j.mnemonic == "je":
        for b in blocks.values():
            if taken in b.succs and len(b.insns) >= 2 and b.insns[-1].mnemonic == "jne" and \
                    dims_cmp(b.insns[-2]):
                return True
    return False


def is_bounds_tail(model, block, proc=None):
    """A block that raises the bounds error and reloads registers for a code path
    that, the call never returning, it doesn't take.  The call may go through a
    register loaded earlier in the code ("mov ebx, [__vbaGenerateBoundsError]; ...;
    jb ok; call ebx": flow analysis gives up on an ebx the procedure also uses as
    its zero)."""
    if not block.insns or not all(i.mnemonic in ("mov", "lea", "call", "jmp") for i in block.insns):
        return False
    calls = [i for i in block.insns if i.mnemonic == "call"]
    if len(calls) != 1:
        return False
    if call_name(model, proc, calls[0]) == "__vbaGenerateBoundsError":
        return True
    op = calls[0].operands[0]
    if op.type != x86.X86_OP_REG:
        return False
    before = sorted((i for b in proc.blocks.values() for i in b.insns
                     if i.address < calls[0].address),
                    key=lambda i: i.address)
    for i in reversed(before):
        if i.mnemonic == "mov" and i.operands[0].type == x86.X86_OP_REG and \
                i.operands[0].reg == op.reg:
            src = i.operands[1]
            return src.type == x86.X86_OP_MEM and src.mem.base == 0 and src.mem.index == 0 and \
                model.im.imports.get(src.mem.disp) == "__vbaGenerateBoundsError"
    return False


def only_loads(block):
    """A block that just loads registers (mov edi, [__vbaHresultCheckObj]), or
    clears the slot the check's result goes to (unoptimised code: the check
    path stores __vbaHresultCheckObj's eax in [ebp-N], the other one does
    "and dword ptr [ebp-N], 0" - VBDoom frmDoom.SetFocus)."""
    return bool(block.insns) and all(
        i.mnemonic in ("mov", "lea") or
        i.mnemonic == "and" and len(i.operands) == 2 and i.operands[1].type == x86.X86_OP_IMM and
        i.operands[1].imm == 0 and i.operands[0].type == x86.X86_OP_MEM
        for i in block.insns)


def new_guard(model, proc, n, new_block):
    """(variable, descriptor) when node n only tests whether a form's predeclared variable or VB's
    Global object exists yet, and new_block creates it: "cmp [G], 0; jne T", "push G; push desc;
    call __vbaNew2".  VB does that by itself at any use, so there is no If in the source (the arms
    may well differ by a label: DarwinBots loadpiccy_Click, "If Form1 = 0 Then ... Else ...")."""
    if len(n.insns) < 2 or "__vbaNew2" not in list(calls_in(model, new_block, proc)):
        return None
    # (what sets the flags: optimised code schedules stores between it and the jump)
    c = next((i for i in reversed(n.insns[:-1]) if i.mnemonic not in ("mov", "lea", "push")), None)
    if c is None:
        return None
    if c.mnemonic == "cmp" and c.operands[0].type == x86.X86_OP_MEM and \
            c.operands[0].mem.base == 0 and c.operands[0].mem.index == 0 and \
            c.operands[1].type == x86.X86_OP_IMM and c.operands[1].imm == 0:
        g = c.operands[0].mem.disp
    elif c.mnemonic == "test" and c.operands[0].type == x86.X86_OP_REG and \
            c.operands[1].type == x86.X86_OP_REG and c.operands[0].reg == c.operands[1].reg:
        # the variable kept in a register: mov ebx, [G]; test ebx, ebx; jne T; ...; call __vbaNew2;
        # mov ebx, [G]; T: (and again from T, as ebx is still G: DarwinBots F1Internet_Click)
        # (or read again only at T, for the flags alone: DarwinBots loadpiccy_Click)
        def loads(i):
            return i.mnemonic == "mov" and i.operands[0].type == x86.X86_OP_REG and \
                i.operands[0].reg == c.operands[0].reg and \
                i.operands[1].type == x86.X86_OP_MEM and \
                i.operands[1].mem.base == 0 and i.operands[1].mem.index == 0
        last = next((i for i in (new_block.insns[-1], *reversed(n.insns[:-1])) if loads(i)), None)
        if last is None:
            return None
        g = last.operands[1].mem.disp
    else:
        return None
    pushes = [i.operands[0].imm for i in new_block.insns
              if i.mnemonic == "push" and i.operands[0].type == x86.X86_OP_IMM]
    if len(pushes) < 2 or pushes[-2] != g:
        return None
    desc = pushes[-1]
    po = getattr(model, "obj_by_info", {}).get(desc)
    if po is not None:
        return (g, desc) if po.kind == "Form" else None
    cls, iface = model.class_of_desc(desc)
    po = getattr(iface, "obj", None)
    if cls is not None and cls.name == "Global" or po is not None and po.kind == "Form":
        return g, desc
    return None


def is_fdiv_adjust(model, block, proc=None):
    return any(n.startswith("_adj_f") for n in calls_in(model, block, proc))


def gosubs(model, proc, blocks, nodes):
    """GoSub L: "call __vbaGosub(&frame); test eax, eax; jne next; jmp L", 0 the first time, not 0
    when __vbaGosubReturn comes back: a statement, and on to next.  L is reached by no edge then, so
    it is a root like an error handler (a negative key in proc.handlers: those are On Error's
    numbers).  lift prints it from proc.gosubs."""
    for n in list(nodes.values()):
        if n.kind != "cond" or len(n.insns) < 3 or n.insns[-1].mnemonic != "jne" or \
                n.insns[-2].mnemonic != "test" or n.insns[-3].mnemonic != "call" or \
                call_name(model, proc, n.insns[-3]) != "__vbaGosub":
            continue
        taken, fall = n.succs
        fb = blocks.get(fall)
        if fb is None or len(fb.insns) != 1 or fb.insns[0].mnemonic != "jmp" or len(fb.succs) != 1:
            continue
        target = fb.succs[0]
        proc.__dict__.setdefault("gosubs", {})[n.insns[-3].address] = target
        proc.handlers[-target] = target
        n.insns = n.insns[:-2]
        n.kind = "fall" if taken == n.start + sum(i.size for i in n.insns) else "jmp"
        n.succs = [taken]


def build(model, proc):
    blocks = proc.blocks
    nodes = {}
    for b in blocks.values():
        kind = b.kind
        succs = list(b.succs)
        if kind == "raise" or is_raise(model, b, proc):
            kind, succs = "raise", []
        elif kind == "retcall":
            # push cont; jmp cleanup  ->  exit through the cleanup routine
            kind = "exit"
            cleanup, cont = succs
            succs = []
            n = Node(b.start, b.insns[:-2], succs, kind)
            n.cleanup = cleanup
            n.cont = cont
            nodes[b.start] = n
            continue
        elif kind == "ret":
            kind = "exit"
            if b.start not in proc.conts and b.insns[-1].operands == [] and proc.conts:
                pass
        elif kind == "jmp" and not succs:
            kind = "raise" if is_raise(model, b, proc) else "exit"
        elif kind == "switch":
            # a jump table: one test per distinct target, in index order, each
            # "index = v1 Or index = v2 ...", the last target as the final else
            sw = proc.switches[b.insns[-1].address]
            reg, targets = sw[0], sw[1]
            extra = sw[2] if len(sw) > 2 else 0     # (two tables: the index is the byte load's operand)
            order = list(dict.fromkeys(targets))
            # the most used target is the final else: the default of a sparse table
            # (else its dozens of indexes print as a condition too long for a line)
            common = max(order, key=targets.count)
            if targets.count(common) > 1:
                order.remove(common)
                order.append(common)
            keys = [(1 << 40) | (b.start << 8) | k for k in range(len(order))]
            n = Node(b.start, list(b.insns[:-1 - extra]),
                     [keys[0]] if len(order) > 1 else [order[0]],
                     "fall" if len(order) > 1 else "jmp")
            nodes[b.start] = n
            for k, t in enumerate(order[:-1]):
                vals = [i for i, x in enumerate(targets) if x == t]
                nxt = keys[k + 1] if k + 2 < len(order) else order[-1]
                c = Node(keys[k], [], [t, nxt], "cond")
                c.switch = (reg, vals)
                nodes[keys[k]] = c
            continue
        nodes[b.start] = Node(b.start, list(b.insns), succs, kind)
        if kind == "raise" and "__vbaGosubReturn" in list(calls_in(model, b, proc)):
            nodes[b.start].returns = True       # (Return: no failed check to drop as such)

    gosubs(model, proc, blocks, nodes)
    changed = True
    while changed:
        changed = False
        for n in nodes.values():
            if n.kind != "cond" or getattr(n, "switch", None) is not None:
                continue                    # (a jump table test is no check)
            taken, fall = n.succs
            if taken not in blocks or fall not in blocks:
                continue                    # into a jump table's synthetic tests
            tn, fn_ = nodes.get(taken), nodes.get(fall)
            drop = None
            hresult = False
            # (the check reached through a jump: "jge ok; jmp check" where the
            # compiler shares one check between identical calls)
            # (or after pushing its own arguments: "push 10h; push IID; push edi;
            # jmp shared" with "push eax; call check" there)
            chk = fall
            while chk in blocks and blocks[chk].insns and \
                    blocks[chk].insns[-1].mnemonic == "jmp" and \
                    all(i.mnemonic in ("push", "mov", "lea") for i in blocks[chk].insns[:-1]) and \
                    len(blocks[chk].succs) == 1 and blocks[chk].succs[0] != chk:
                chk = blocks[chk].succs[0]
            cn = nodes.get(chk)
            if fn_ is not None and is_check(model, blocks[fall], proc) and (
                    fn_.succs == [taken] or
                    # both paths load the check function's register, then meet
                    tn is not None and len(tn.succs) == 1 and fn_.succs == tn.succs and
                    only_loads(blocks[taken])):
                drop, hresult = fall, True
            elif chk != fall and cn is not None and chk in blocks and \
                    is_check(model, blocks[chk], proc) and cn.succs == [taken]:
                drop, hresult = fall, True
            elif n.insns and n.insns[-1].mnemonic == "jge" and \
                    refetch_check(model, proc, blocks, fall, taken):
                drop, hresult = fall, True
            elif tn is not None and tn.kind == "raise" and not getattr(tn, "returns", False):
                drop = taken
            elif fn_ is not None and fn_.kind == "raise" and not getattr(fn_, "returns", False):
                drop = fall
            elif tn is not None and is_fdiv_adjust(model, blocks[taken], proc):
                drop = taken
            elif fn_ is not None and is_bounds_tail(model, blocks[fall], proc) and fn_.succs == [taken]:
                drop = fall                 # (jb ok; call ebx; mov ecx, [ebp-24h]; ok:)
            elif tn is not None and sa_guard(n, blocks, taken, fall):
                drop = taken
            elif tn is not None and is_bounds_tail(model, blocks[taken], proc):
                # "cmp ecx, ebx; je E; cmp word ptr [ecx], 1; jne E ... E: call
                # __vbaGenerateBoundsError; mov ecx, [ebp-24h]" and on into the access:
                # the SAFEARRAY's fields printed as a record's (a target Proc_858E80
                # v24.pad1(1), MoveRow m3C.f14)
                drop = taken
            elif n.insns and n.insns[-1].mnemonic == "jne" and fn_ is not None and \
                    new_guard(model, proc, n, blocks[fall]) is not None:
                drop = fall
                proc.__dict__.setdefault("new_guards", set()).add(new_guard(model, proc, n, blocks[fall]))
            elif n.insns and n.insns[-1].mnemonic == "je" and tn is not None and \
                    new_guard(model, proc, n, blocks[taken]) is not None:
                drop = taken
                proc.__dict__.setdefault("new_guards", set()).add(new_guard(model, proc, n, blocks[taken]))
            if drop is not None:
                dn = nodes.get(drop)
                if dn is not None and dn.kind == "raise" and \
                        "__vbaGenerateBoundsError" in list(calls_in(model, blocks[drop], proc)):
                    # "cmp idx, N; jb ok" guards an array: N is its element count
                    for ins in reversed(n.insns):
                        if ins.mnemonic == "cmp" and ins.operands[1].type == x86.X86_OP_IMM:
                            proc.bounds_at[n.start] = ins.operands[1].imm
                            # (which value it checks: each index of a 2-D array has its own)
                            proc.bound_cmps[ins.address] = ins.operands[1].imm
                            break
                keep = fall if drop == taken else taken
                if n.insns and n.insns[-1].mnemonic.startswith(("j", "loop")):
                    n.insns = n.insns[:-1]      # the test itself is gone too
                if hresult:
                    # and what set its flags from the HRESULT: a test of eax read
                    # the call's value, which at a check several calls share
                    # (cross-jumped: "call [eax+204h]; jmp shared") made each
                    # path keep its call in eax, a second call each (a target
                    # t_eax = Me.Text4.SetFocus())
                    while n.insns and (n.insns[-1].mnemonic == "fnclex" or
                                       n.insns[-1].mnemonic in ("test", "cmp") and
                                       n.insns[-1].op_str.split(",")[0].strip() == "eax" or
                                       # (kept in a register for the report: test edi, edi)
                                       n.insns[-1].mnemonic == "test" and
                                       n.insns[-1].op_str.split(",")[0].strip() ==
                                       n.insns[-1].op_str.split(",")[-1].strip()):
                        n.insns = n.insns[:-1]
                n.kind = "fall" if keep == n.start + sum(i.size for i in n.insns) else "jmp"
                n.succs = [keep]
                n.dropped_cond = True
                changed = True
    # FDIV adjust blocks fall into the join; the join may now start a block whose
    # only remaining predecessor is the fdiv path
    reach = set()
    work = [proc.va] + list(getattr(proc, "handlers", {}).values())
    while work:
        a = work.pop()
        if a in reach or a not in nodes:
            continue
        reach.add(a)
        work.extend(nodes[a].succs)
    nodes = {a: n for a, n in nodes.items() if a in reach}
    for n in nodes.values():
        n.preds = []
    for n in nodes.values():
        for s in n.succs:
            if s in nodes:
                nodes[s].preds.append(n.start)
    resume = set(a for a, n in nodes.items() if n.kind == "jmp" and resumes(model, n, proc))
    gs = getattr(proc, "gosubs", {})
    # (GoSub ends its node: what the subroutine's Return goes to must start one, for liveness)
    resume |= {a for a, n in nodes.items() if any(i.address in gs for i in n.insns)}
    split_flag_branches(nodes, proc)
    split_register_joins(model, nodes, proc)
    if not os.environ.get("VB6DEC_NODUP") and not numbered_lines(model, proc):
        duplicate_tails(nodes, proc, keep=resume)
    nodes = merge_chains(nodes, proc.va, keep=resume,
                         handlers=set(getattr(proc, "handlers", {}).values()))
    if gs:
        # the flow no edge shows, for simplify's liveness: from a GoSub to its subroutine, and from
        # a Return to whatever follows a GoSub (r = 33: Return looked like a dead store)
        conts = []
        for n in nodes.values():
            for i in n.insns:
                if i.address in gs:
                    n.flow_extra = [gs[i.address]]
                    conts += n.succs
        for n in nodes.values():
            if any(i.mnemonic == "call" and call_name(model, proc, i) == "__vbaGosubReturn"
                   for i in n.insns):
                n.flow_extra = conts
    return nodes


TAIL_KEY = 2 << 40              # keys of duplicated tail blocks


def numbered_lines(model, proc):
    """Will the procedure print line numbers (lift.line_marks)?  Then no statement can be copied: one
    label, once (50 twice: "Duplicate declaration in current scope")."""
    im = model.im
    d = getattr(proc, "procdesc", None)
    if not d or not im.valid(d) or "rtcGetErl" not in im.imports.values():
        return False
    tab = im.u32(d + 0x18)
    if not im.valid(tab):
        return False
    return any(im.u16(tab + 4 + 2 * k) for k in range(min(im.u32(tab), 0x4000)))


def split_flag_branches(nodes, proc):
    """A block that is only a conditional jump, reached with other flags on each way in: the compiler
    shares jne X after any compare that wants it (cmp [mem], 1 falling in; cmp eax, 5; jmp there.
    ChessBrainVB IsCheckingMove read the first compare for both, and If PieceFrom = WQUEEN Or
    PieceFrom = WBISHOP asked MaxDistance instead).  Each way in gets its own copy of the jump."""
    count = 0
    handlers = set(getattr(proc, "handlers", {}).values())
    for a in sorted(nodes):
        n = nodes.get(a)
        if n is None or a == proc.va or a in handlers or n.kind != "cond" or len(n.insns) != 1 or \
                len(n.preds) < 2 or not n.insns[0].mnemonic.startswith("j") or \
                getattr(n, "switch", None) is not None:
            continue
        for p in list(n.preds[1:]):
            pn = nodes.get(p)
            if pn is None:
                continue
            count += 1
            k = TAIL_KEY + (a << 8) + 0x80 + count
            c = Node(k, list(n.insns), list(n.succs), n.kind)
            for attr in ("cleanup", "cont", "dropped_cond"):
                if hasattr(n, attr):
                    setattr(c, attr, getattr(n, attr))
            if a in proc.bounds_at:
                proc.bounds_at[k] = proc.bounds_at[a]
            c.preds = [p]
            c.orig = a
            nodes[k] = c
            pn.succs = [k if x == a else x for x in pn.succs]
            for s_ in c.succs:
                if s_ in nodes:
                    nodes[s_].preds.append(k)
        n.preds = n.preds[:1]


def split_register_joins(model, nodes, proc):
    """The same with a runtime call that takes its arguments in registers: the compiler shares it
    among the ways in, each setting ecx and edx first (lea edx, [t]; lea ecx, [v74]; jmp to the
    call __vbaVarMove of v44 = False above: a target's For over a Variant).  Across the join
    those are addresses of frame slots no variable can hold, and t_ecx = t_edx was all that came out.
    Each way in gets its own call, the rest of the block stays shared."""
    count = 0
    handlers = set(getattr(proc, "handlers", {}).values())

    def sets_reg(pn, regs):
        tail = [i for i in pn.insns if i.mnemonic != "jmp"][-5:]
        return any(i.mnemonic in ("mov", "lea") and i.op_str.split(",")[0].strip() in regs for i in tail)
    for a in sorted(nodes):
        n = nodes.get(a)
        if n is None or a == proc.va or a in handlers or len(n.preds) < 2 or not n.insns or \
                getattr(n, "switch", None) is not None:
            continue
        # the call, after no more than the lea of the argument that doesn't come from the ways in
        # (lea ecx, [vBC]; call __vbaVarMove, edx already set)
        at = next((j for j, i in enumerate(n.insns[:3]) if i.mnemonic == "call"), None)
        if at is None:
            continue
        if any(i.mnemonic not in ("mov", "lea") for i in n.insns[:at]):
            continue
        name = call_name(model, proc, n.insns[at])
        if not name or not name.startswith("__vba"):
            continue
        given = {i.op_str.split(",")[0].strip() for i in n.insns[:at]}
        regs = {"ecx", "edx"} - given
        preds = [nodes.get(p) for p in n.preds]
        if not regs or not all(pn is not None and sets_reg(pn, regs) for pn in preds):
            continue
        count += 1
        if len(n.insns) > at + 1:
            k = TAIL_KEY + (a << 8) + 0x40 + count
            rest = Node(k, n.insns[at + 1:], list(n.succs), n.kind)
            for attr in ("cleanup", "cont", "dropped_cond"):
                if hasattr(n, attr):
                    setattr(rest, attr, getattr(n, attr))
            rest.preds = [a]
            rest.orig = a
            if a in proc.bounds_at:
                proc.bounds_at[k] = proc.bounds_at[a]
            nodes[k] = rest
            for s_ in rest.succs:
                if s_ in nodes:
                    nodes[s_].preds = [k if x == a else x for x in nodes[s_].preds]
            n.insns, n.succs, n.kind = n.insns[:at + 1], [k], "fall"
        for p in list(n.preds[1:]):
            pn = nodes[p]
            count += 1
            k2 = TAIL_KEY + (a << 8) + 0x40 + count
            c = Node(k2, list(n.insns), list(n.succs), n.kind)
            for attr in ("cleanup", "cont", "dropped_cond"):
                if hasattr(n, attr):
                    setattr(c, attr, getattr(n, attr))
            c.preds = [p]
            c.orig = a
            nodes[k2] = c
            pn.succs = [k2 if x == a else x for x in pn.succs]
            for s_ in c.succs:
                if s_ in nodes:
                    nodes[s_].preds.append(k2)
        n.preds = n.preds[:1]


def duplicate_tails(nodes, proc, limit=24, keep=()):
    """A short chain of blocks that several paths jump into and that only
    leaves the procedure (C2 shares "result = x: Exit" between the Cases,
    gh_zip FileInfo: each Case builds a Variant, one VarCopy writes it): give
    each path its own copy, so the machine state of that path (which slot,
    which tag) reaches the shared code."""
    count = [0]

    def chain(a):
        out, n_ins = [], 0
        while a in nodes:
            n = nodes[a]
            if n.kind == "cond" or getattr(n, "switch", None) is not None:
                return None
            out.append(a)
            n_ins += len(n.insns)
            if n_ins > limit or len(out) > 8:
                return None
            if n.kind in ("exit", "raise"):
                return out
            if len(n.succs) != 1:
                return None
            a = n.succs[0]
        return None
    handlers = set(getattr(proc, "handlers", {}).values())
    for _ in range(8):
        changed = False
        for a in sorted(nodes, key=lambda x: (x >= TAIL_KEY, x)):
            n = nodes.get(a)
            if n is None or len(n.preds) < 2 or a == proc.va or a in handlers:
                continue
            ch = chain(a)
            if ch is None:
                continue
            for p in n.preds[1:]:
                pn = nodes.get(p)
                if pn is None or p in keep:
                    continue                # (Resume label jumps to the label itself)
                prev_key, first = None, None
                for b in ch:
                    o = nodes[b]
                    count[0] += 1
                    k = TAIL_KEY + (a << 8) + count[0]
                    c = Node(k, list(o.insns), list(o.succs), o.kind)
                    for attr in ("cleanup", "cont", "dropped_cond"):
                        if hasattr(o, attr):
                            setattr(c, attr, getattr(o, attr))
                    if b in proc.bounds_at:
                        proc.bounds_at[k] = proc.bounds_at[b]
                    c.preds = []
                    c.orig = b
                    nodes[k] = c
                    if prev_key is not None:
                        nodes[prev_key].succs = [k]
                        c.preds = [prev_key]
                    else:
                        first = k
                    prev_key = k
                # the copy's exit keeps the original successors (none for an exit)
                pn.succs = [first if x == a else x for x in pn.succs]
                nodes[first].preds = [p]
                for b in ch[1:]:
                    pass
            n.preds = n.preds[:1]
            changed = True
        if not changed:
            break
    # recompute predecessor lists
    for n in nodes.values():
        n.preds = []
    for n in nodes.values():
        for s in n.succs:
            if s in nodes:
                nodes[s].preds.append(n.start)


def merge_chains(nodes, entry, keep=(), handlers=()):
    """Merge a block into its predecessor when it is the only successor of a
    predecessor that is its only predecessor (keep: blocks that never take
    their successor in; handlers: On Error targets, which the normal flow
    may fall into but which need a node of their own for the label)."""
    changed = True
    while changed:
        changed = False
        for a in sorted(nodes):
            n = nodes.get(a)
            if n is None or n.kind not in ("fall", "jmp") or len(n.succs) != 1 or a in keep:
                continue
            s = n.succs[0]
            m = nodes.get(s)
            if m is None or s == entry or len(m.preds) != 1 or s == a or s in handlers:
                continue                    # (an error handler keeps its own node: its label)
            # drop the jmp instruction itself when merging
            insns = n.insns
            if n.kind == "jmp" and insns and insns[-1].mnemonic == "jmp":
                insns = insns[:-1]
            n.insns = insns + m.insns
            n.kind, n.succs = m.kind, m.succs
            if getattr(m, "switch", None) is not None:
                n.switch = m.switch         # a jump table test (see build)
            if m.kind == "exit":
                n.cleanup = m.cleanup
                n.cont = getattr(m, "cont", None)
            for t in m.succs:
                if t in nodes:
                    nodes[t].preds = [n.start if p == s else p for p in nodes[t].preds]
            if s in keep:
                keep.add(a)                 # (the block now ends as the kept one did)
            del nodes[s]
            changed = True
            break
    return nodes
