"""Procedure discovery and control-flow graphs for VB6 native code."""
import os
import sys
import capstone
from capstone import x86

_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True

# push ebp; mov ebp,esp; sub esp,imm8; push imm32 (SEH handler); mov eax,fs:[0]
SEH_PROLOGUE = (b"\x55\x8b\xec\x83\xec", b"\x68")


def seh_prologue(b):
    """Code bytes b start a procedure with an SEH frame: push ebp; mov ebp,
    esp; the bookkeeping space - sub esp, imm8 (optimised code), or push ecx
    once or more (no optimisation, VBDoom: two, for the procedure descriptor
    and the saved esp) - then push imm32, the handler, and mov eax, fs:[0]."""
    if b.startswith(SEH_PROLOGUE[0]):
        # (fast code may read fs:[0] first: DarwinBots posto, with its 13 KB frame)
        return b[6:7] == SEH_PROLOGUE[1] or \
            b[6:12] == b"\x64\xa1\x00\x00\x00\x00" and b[12:13] == SEH_PROLOGUE[1]
    if not b.startswith(b"\x55\x8b\xec\x51"):
        return False
    n = 4
    while b[n:n + 1] == b"\x51":
        n += 1
    return b[n:n + 1] == b"\x68" and b[n + 5:n + 11] == b"\x64\xa1\x00\x00\x00\x00"


# Runtime functions that never return (raise a VB error)
# (not __vbaError, the Error statement: a handler's Resume Next or On Error Resume Next goes on to
# the code after it, and that code is always there: t77pushecx lost its "done1" line.  Nor
# __vbaEnd, __vbaStopExe: with the code after them went the ret N, so I_Error "x" lost its parameter)
NORETURN = {"__vbaGosubReturn", "__vbaErrorOverflow", "__vbaGenerateBoundsError", "__vbaFPException",
            "__vbaRaiseEvent_noreturn", "__vbaFailedFriend"}


def is_noreturn_call(im, ins, regs=None):
    if ins.mnemonic != "call":
        return False
    op = ins.operands[0]
    if op.type == x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
        return im.imports.get(op.mem.disp) in NORETURN
    if op.type == x86.X86_OP_REG and regs:
        return iat_at(regs, ins.address, op.reg) in NORETURN
    if op.type == x86.X86_OP_IMM and im.valid(op.imm) and im.valid(op.imm + 5) and \
            bytes(im.bytes(op.imm, 2)) == b"\xff\x25":
        # "call stub", the stub "jmp [iat]" (no import table calls in this build): without this a
        # procedure ended in __vbaFPException ran on into the next one, whose ret 4 and code it took
        # for its own (DarwinBots DNAaddstore: a Function with a parameter, mod32000's)
        return im.imports.get(im.u32(op.imm + 2)) in NORETURN
    return False


def iat_regs(proc, im):
    """Writes to registers that cache import pointers, in address order:
    [(address, register, import name or None)]."""
    out = []
    for a in sorted(proc.insns):
        ins = proc.insns[a]
        ops = ins.operands
        m = ins.mnemonic
        if not ops or ops[0].type != x86.X86_OP_REG or \
                m in ("push", "cmp", "test", "call", "jmp", "ret") or m.startswith(("j", "loop")):
            continue        # these read the register, they do not write it
        name = None
        if m == "mov" and len(ops) == 2 and ops[1].type == x86.X86_OP_MEM and \
                ops[1].mem.base == 0 and ops[1].mem.index == 0:
            name = im.imports.get(ops[1].mem.disp)
        out.append((a, ops[0].reg, name))
    return out


def iat_at(events, va, reg):
    """The import a register holds just before va."""
    name = None
    for a, r, n in events:
        if a >= va:
            break
        if r == reg:
            name = n
    return name


def iat_at_flow(proc, va, reg):
    """The import a register holds just before va, following the control
    flow: on entry to a block a register holds an import only when every
    predecessor leaves it holding that one (a "call edi" after a join where
    the last write in address order is on another path, gh_qr)."""
    blocks = getattr(proc, "blocks", None)
    if not blocks:
        return iat_at(getattr(proc, "iatev", ()), va, reg)
    flow = getattr(proc, "_iat_flow", None)
    if flow is None:
        ev = {}
        for a, r, n in getattr(proc, "iatev", ()):
            ev[a] = (r, n)
        preds = {b: [] for b in blocks}
        for b in blocks.values():
            for t in b.succs:
                if t in preds:
                    preds[t].append(b.start)

        def run(start, state):
            st = dict(state)
            for ins in blocks[start].insns:
                if ins.address in ev:
                    r, n = ev[ins.address]
                    st[r] = n
            return st
        inn = {b: None for b in blocks}         # None: not reached yet
        inn[proc.va] = {}
        out = {}
        work = [proc.va] + [b for b in blocks if not preds[b]]
        for b in work:
            if inn[b] is None:
                inn[b] = {}
        changed = True
        while changed:
            changed = False
            for b in sorted(blocks):
                ps = [out[p] for p in preds[b] if p in out]
                if b == proc.va or not preds[b]:
                    new = {}
                elif ps:
                    keys = set.intersection(*[set(p) for p in ps])
                    new = {k: ps[0][k] for k in keys if all(p.get(k) == ps[0][k] for p in ps)}
                else:
                    continue
                if inn[b] != new or b not in out:
                    inn[b] = new
                    out[b] = run(b, new)
                    changed = True
        proc._iat_flow = flow = (inn, sorted(blocks))
    inn, starts = flow
    import bisect
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    b = proc.blocks[starts[i]]
    state = dict(inn.get(b.start) or {})
    ev = {a: (r, n) for a, r, n in getattr(proc, "iatev", ())}
    for ins in b.insns:
        if ins.address >= va:
            break
        if ins.address in ev:
            r, n = ev[ins.address]
            state[r] = n
    return state.get(reg)


def looks_like_entry(im, va):
    """A procedure prologue (an AddressOf target), not an epilogue."""
    b = im.bytes(va, 3)
    return b[:1] in (b"\x55", b"\x53", b"\x56", b"\x57") or b[:2] == b"\x83\xec" or \
        b[:2] == b"\x81\xec"



def after_padding(im, t):
    """Does t follow the nops between two procedures (or the int 3s after the thunks)?  (A number that
    happens to look like an aligned address: gh_doom's tables of flags, whose 410000h went for a
    procedure.  And a cleanup's continuation, right after the cleanup's ret, pushed with other things
    to do before its jmp.)"""
    try:
        return im.bytes(t - 1, 1) in (b"\x90", b"\xcc")
    except Exception:
        return False


def insns_tail(insns, pc, count=5):
    """The last instructions decoded before pc (the debug print of _switch)."""
    return [insns[a] for a in sorted(insns) if a < pc][-count:]


class Mem:
    """An x86 memory operand (capstone's X86OpMem as plain attributes)."""
    __slots__ = ("segment", "base", "index", "scale", "disp")

    def __init__(self, segment, base, index, scale, disp):
        self.segment, self.base, self.index, self.scale, self.disp = segment, base, index, scale, disp


class Op:
    """An operand (capstone's X86Op).  As there, imm, reg and mem are views of
    one union: the bits of a register operand read as imm and so on."""
    __slots__ = ("type", "size", "access", "reg", "imm", "mem")

    def __init__(self, type, size, access, reg, imm, mem):
        self.type, self.size, self.access, self.reg, self.imm, self.mem = \
            type, size, access, reg, imm, mem


class Template:
    """What all the instructions with the same encoding share."""
    __slots__ = ("id", "size", "mnemonic", "op_str", "operands", "prefix", "code", "access",
                 "rel", "op")


REG_NAME = {}


class Insn:
    """A decoded instruction with what capstone's CsInsn offers the decompiler,
    as plain attributes: a CsInsn takes 5.7 kB (its ctypes copy of the 1.7 kB
    cs_detail, and each operand's once read) and a Python call per attribute;
    a target decodes 2.17 million of them, which are 107,000 encodings."""
    __slots__ = ("address", "size", "mnemonic", "op_str", "operands", "prefix", "id", "t")

    def __init__(self, t, va):
        self.t = t
        self.address = va
        self.size = t.size
        self.mnemonic = t.mnemonic
        self.op_str = t.op_str
        self.operands = t.operands
        self.prefix = t.prefix
        self.id = t.id

    def __repr__(self):
        return "<Insn 0x%x: %s %s>" % (self.address, self.mnemonic, self.op_str)

    def __reduce__(self):                   # (snapshot.py: its template once, and the address)
        t = self.t
        if t.rel:
            return _rel_insn, (t, self.address, self.operands[0].imm)
        return Insn, (t, self.address)

    def regs_access(self):
        t = self.t
        if t.access is None:
            t.access = next(_md.disasm(t.code, self.address, 1)).regs_access()
        return t.access

    @staticmethod
    def reg_name(r):
        n = REG_NAME.get(r, False)
        if n is False:
            n = REG_NAME[r] = _md.reg_name(r)
        return n


def _rel_insn(t, va, imm):
    ins = Insn(t, va)
    d = t.op
    ins.operands = [Op(d.type, d.size, d.access, imm & 0xffffffff, imm, Mem(
        imm & 0xffffffff, 0, 0, 0, 0))]
    ins.op_str = hex(imm)
    return ins


def _operand(o):
    v = o.value
    m = v.mem
    return Op(o.type, o.size, o.access, v.reg, v.imm,
              Mem(m.segment, m.base, m.index, m.scale, m.disp))


def template_of(ins):
    t = Template()
    t.id, t.size, t.mnemonic, t.op_str = ins.id, ins.size, ins.mnemonic, ins.op_str
    t.operands = [_operand(o) for o in ins.operands]
    t.prefix = list(ins.prefix)
    t.code = bytes(ins.bytes)
    t.access = None
    t.rel = 0
    t.op = None
    return t


# Encodings seen, a trie on their bytes: a leaf is the Template.  (An x86
# decoder reads only the bytes of the instruction, so a complete encoding is
# never the start of another.  tools/checkdecode.py compares this with capstone
# for every instruction of an EXE.)  A direct call or jump is a leaf after its
# opcode: the operand comes from its displacement.
_trie = {}
REL_OPCODES = {0xe8: 4, 0xe9: 4, 0xeb: 1, **{0x70 + i: 1 for i in range(16)}}


def _learn(ins):
    t = template_of(ins)
    code = t.code
    node = _trie
    if code[0] in REL_OPCODES and t.size == 1 + REL_OPCODES[code[0]]:
        t.rel, t.op = REL_OPCODES[code[0]], t.operands[0]
        key = code[:1]
    elif code[0] == 0x0f and 0x80 <= code[1] < 0x90 and t.size == 6:
        t.rel, t.op = 4, t.operands[0]
        key = code[:2]
    else:
        key = code
    for b in key[:-1]:
        node = node.setdefault(b, {})
    node[key[-1]] = t
    return t


def decode_uncached(mem, o, va):
    """The instruction at offset o of the image, va its address."""
    node = _trie
    i = o
    n = len(mem)
    while i < n:
        node = node.get(mem[i])
        i += 1
        if node is None:
            break
        if type(node) is Template:
            t = node
            if o + t.size > n:
                break
            if t.rel:
                r = int.from_bytes(mem[o + t.size - t.rel:o + t.size], "little", signed=True)
                return _rel_insn(t, va, (va + t.size + r) & 0xffffffff)
            return Insn(t, va)
    c = next(_md.disasm(bytes(mem[o:o + 16]), va, 1), None)
    if c is None:
        return None
    t = _learn(c)
    if t.rel:                       # (the template has this instruction's target)
        return decode_uncached(mem, o, va)
    return Insn(t, va)


def decode(im, va):
    """The instruction at va, decoded once per image: Proc._build sweeps each
    procedure twice, and call targets are decoded again later.  (Callers only
    read the objects, so they can be shared.)"""
    seen = im.__dict__.setdefault("_decoded", {})
    ins = seen.get(va, False)
    if ins is False:
        ins = seen[va] = decode_uncached(im.mem, va - im.base, va)
    return ins


class Block:
    def __init__(self, start):
        self.start = start
        self.end = start
        self.insns = []
        self.succs = []         # list of VAs
        self.preds = []
        self.kind = None        # 'ret', 'jmp', 'cond', 'fall', 'retcall' (push addr; jmp)

    def __repr__(self):
        return "<Block %#x-%#x %s -> %s>" % (self.start, self.end, self.kind,
                                             [hex(s) for s in self.succs])


class Proc:
    """One native procedure."""

    def __init__(self, prj, va, name=None):
        self.prj = prj
        self.va = va
        self.name = name
        self.owner = None           # Object
        self.kind = None            # 'event', 'method', 'proc'
        self.blocks = {}
        self.seh = False
        self.frame = 0
        self.procdesc = None
        self.exit_va = None         # where [ebp-4] is cleared and the epilogue starts
        self.cleanup_va = None      # error-path cleanup (from the proc descriptor)
        self.ret_pop = 0
        self.calls = set()          # direct call targets
        self.conts = set()          # epilogues entered by returning from the cleanup
        self.iatregs = {}
        self.bounds_at = {}         # block start -> array element count from its check
        self.bound_cmps = {}        # VA of a bounds check's cmp -> the count it allows
        self._build()

    # ----------------------------------------------------------------
    def _build(self):
        im = self.prj.im
        b = im.bytes(self.va, 32)
        if seh_prologue(b):
            self.seh = True
        work = [self.va]
        starts = {self.va}
        insns = {}
        # a first sweep to find which registers cache import pointers
        self._sweep_plain(self.va, insns)
        self.insns = insns
        self.iatev = self.iatregs = iat_regs(self, im)
        insns = {}
        # pass 1: linear sweep along control flow, recording leaders
        while work:
            pc = work.pop()
            while True:
                if pc in insns:
                    break
                if any(lo <= pc < hi for lo, hi in getattr(self, "tables", ())):
                    break                   # a jump table's entries: data, not code
                ins = decode(im, pc)
                if ins is None:
                    break
                insns[pc] = ins
                m = ins.mnemonic
                nxt = pc + ins.size
                if m == "ret":
                    # the cleanup subroutines end in a plain ret; keep the largest
                    self.ret_pop = max(self.ret_pop, ins.operands[0].imm if ins.operands else 0)
                    break
                if m == "jmp" and self._switch(im, insns, pc, ins):
                    for t in self.switches[pc][1]:
                        starts.add(t)
                        work.append(t)
                    break
                if m == "jmp":
                    op = ins.operands[0]
                    if pc == self.va and op.type == x86.X86_OP_IMM:
                        # the whole procedure is "jmp other": the compiler made the
                        # only call a tail call (Sub Main: RunAll5)
                        self.tail = op.imm
                        self.calls.add(op.imm)
                        break
                    if op.type == x86.X86_OP_IMM and op.imm != self.va and \
                            self._inside(op.imm) and seh_prologue(im.bytes(op.imm, 32)):
                        # a jump to another procedure's entry (its exception
                        # frame prologue): a tail call too (a target Proc_9274C0
                        # "If IsNull(x) Then Proc_EEA4D0 Else ..." took in the
                        # rest of the program, 6 MB, as its own code)
                        self.__dict__.setdefault("tail_jumps", {})[pc] = op.imm
                        self.calls.add(op.imm)
                        break
                    if op.type == x86.X86_OP_IMM and self._inside(op.imm):
                        starts.add(op.imm)
                        work.append(op.imm)
                    break
                if m == "jo":
                    # VB's overflow checks: the target is the overflow error (once
                    # even the middle of an instruction, gh_qr pvApplyMask)
                    pc = nxt
                    continue
                if m.startswith("j") or m in ("loop", "loope", "loopne", "jecxz"):
                    op = ins.operands[0]
                    starts.add(op.imm)
                    starts.add(nxt)
                    work.append(op.imm)
                    pc = nxt
                    continue
                if m == "call":
                    op = ins.operands[0]
                    if op.type == x86.X86_OP_IMM:
                        self.calls.add(op.imm)
                    if is_noreturn_call(im, ins, self.iatregs):
                        break
                if m == "push" and ins.operands[0].type == x86.X86_OP_IMM:
                    # "push cont; jmp cleanup" or "push cont; <cleanup>; ret" is a
                    # local call of the exit cleanup; cont is the real epilogue
                    t = ins.operands[0].imm
                    # (or anywhere, when the jmp right after it goes to a cleanup
                    # just before t: a long procedure's last block, 8 KB from its
                    # exits - a target Proc_868870 lost its ret 10h, so all four
                    # parameters)
                    j = decode(im, nxt)
                    near_cleanup = j is not None and j.mnemonic == "jmp" and \
                        j.operands[0].type == x86.X86_OP_IMM and \
                        j.operands[0].imm < t < j.operands[0].imm + 0x400
                    # (fast code's procedures start with anything: mov eax, [esp + 4].  They are aligned
                    # to 16, and a cleanup's address is pushed for the jmp that follows)
                    frameless = t % 16 == 0 and not (j is not None and j.mnemonic == "jmp") and \
                        after_padding(im, t)
                    if self._inside(t) and (pc < t < pc + 0x400 or near_cleanup) and \
                            not looks_like_entry(im, t) and not frameless:
                        starts.add(t)
                        work.append(t)
                        self.conts.add(t)
                    elif self._inside(t) and t != self.va and (
                            looks_like_entry(im, t) and not (pc < t < pc + 0x400) or frameless):
                        # AddressOf P: a procedure only a pointer reaches (VBDoom
                        # SetWindowLong hwnd, GWL_WNDPROC, AddressOf DoomWndProc)
                        self.calls.add(t)
                        self.__dict__.setdefault("addressof", set()).add(t)
                pc = nxt
        # error-path cleanup from the proc descriptor ([ebp-8])
        self.reachable = set(insns)
        self.handlers = {}          # On Error GoTo index -> handler VA
        if self.seh:
            # the descriptor is the first pointer the prologue stores in the
            # SEH bookkeeping area ([ebp-4], [ebp-8] or [ebp-0x10])
            for pc in sorted(insns):
                ins = insns[pc]
                if ins.mnemonic == "mov" and len(ins.operands) == 2 and \
                   ins.operands[0].type == x86.X86_OP_MEM and ins.operands[0].mem.base == x86.X86_REG_EBP and \
                   -0x20 <= ins.operands[0].mem.disp < 0 and ins.operands[1].type == x86.X86_OP_IMM and \
                   self.prj.im.valid(ins.operands[1].imm) and \
                   im.section_of(ins.operands[1].imm) == ".text" and \
                   not (self.prj.code_start <= ins.operands[1].imm < self.prj.code_end):
                    self.procdesc = ins.operands[1].imm
                    break
                if pc > self.va + 0x60:
                    break
            if self.procdesc and im.valid(self.procdesc):
                # On Error GoTo handlers: +0x10 -> {count, (index, VA) ...}
                tab = im.u32(self.procdesc + 0x10)
                if im.valid(tab) and not self._inside(tab):
                    n = im.u32(tab)
                    for k in range(min(n, 64)):
                        idx, va = im.u32(tab + 4 + 8 * k), im.u32(tab + 8 + 8 * k)
                        # (the descriptor says so; a procedure can run past 64 KB:
                        # a target Proc_789740's handler is 93 KB in)
                        if self._inside(va) and self.va < va < self.va + 0x80000:
                            self.handlers[idx] = va
                            # a block starts there also where the normal flow
                            # falls into it (Set p = Nothing / err1: If
                            # Err.Number <> 0 ...: L90ABB5 had no label)
                            starts.add(va)
                            if va not in insns:
                                self._sweep_more(va, insns, starts)
            if self.procdesc and im.valid(self.procdesc):
                for k in range(1, 4):
                    c = im.u32(self.procdesc + 4 * k)
                    if c and self._inside(c) and self.va < c < self.va + 0x10000:
                        if c not in self.reachable:
                            self.cleanup_va = c
                        if c not in insns:
                            starts.add(c)
                            self._sweep_more(c, insns, starts)
        # pass 2: cut blocks
        self.insns = insns
        order = sorted(insns)
        cur = None
        for pc in order:
            ins = insns[pc]
            if cur is None or pc in starts or pc != cur.end:
                if cur is not None and cur.kind is None:
                    cur.kind = "fall"
                    cur.succs = [pc] if pc == cur.end else []
                cur = Block(pc)
                self.blocks[pc] = cur
            cur.insns.append(ins)
            cur.end = pc + ins.size
            m = ins.mnemonic
            if m == "ret":
                cur.kind = "ret"
                cur = None
            elif is_noreturn_call(im, ins, self.iatregs):
                cur.kind = "raise"
                cur = None
            elif m == "jmp" and pc in getattr(self, "switches", {}):
                cur.kind = "switch"             # Select Case through a jump table
                cur.succs = list(dict.fromkeys(self.switches[pc][1]))
                cur = None
            elif m == "jmp" and (pc == self.va and getattr(self, "tail", None) is not None or
                                 pc in getattr(self, "tail_jumps", {})):
                cur.kind = "ret"                # a tail call: lifted as call + return
                cur = None
            elif m == "jmp":
                op = ins.operands[0]
                if len(cur.insns) >= 2 and cur.insns[-2].mnemonic == "push" and \
                   cur.insns[-2].operands[0].type == x86.X86_OP_IMM and \
                   cur.insns[-2].operands[0].imm in self.conts:
                    cur.kind = "retcall"
                    cur.succs = [op.imm, cur.insns[-2].operands[0].imm]
                else:
                    cur.kind = "jmp"
                    cur.succs = [op.imm] if op.type == x86.X86_OP_IMM else []
                cur = None
            elif m == "jo":
                pass                            # an overflow check: no branch
            elif m.startswith("j") or m.startswith("loop"):
                cur.kind = "cond"
                cur.succs = [ins.operands[0].imm, cur.end]
                cur = None
        if cur is not None and cur.kind is None:
            cur.kind = "fall"
            cur.succs = [cur.end] if cur.end in self.blocks else []
        for b in self.blocks.values():
            if b.kind == "fall" and not b.succs and b.end in self.blocks:
                b.succs = [b.end]
            for s in b.succs:
                if s in self.blocks:
                    self.blocks[s].preds.append(b.start)
        self.end = max(b.end for b in self.blocks.values())
        # a Function returning Single/Double leaves its value on the FPU:
        # "fld [x]" right before the final ret
        self.float_ret = False
        for b in self.blocks.values():
            if b.kind == "ret" and len(b.insns) >= 2:
                # (register restores may come between: pop edi; fld [ebp-18h]; pop esi;
                # mov fs:[0], ecx; ... ret 4, gh_json utc_SystemTimeToDate.  Optimised code:
                # fld [esp]; add esp, 8; ret, a plain ret with no parameters: DarwinBots
                # calc_handycap, whose callers read st(0) only instructions after the call)
                for ins in reversed(b.insns[:-1]):
                    m = ins.mnemonic
                    if m in ("fld", "fild"):
                        self.float_ret = True
                        # (how wide the value is read: a Single's Function or a Double's)
                        self.float_ret_size = ins.operands[0].size if m == "fld" and \
                            ins.operands[0].type == x86.X86_OP_MEM else None
                        break
                    if not (m in ("pop", "leave") or m == "add" and ins.op_str.startswith("esp") or
                            m == "mov" and (ins.op_str.startswith(("esp", "ebp", "ecx")) or
                                            "fs:" in ins.op_str)):
                        break

    def _sweep_plain(self, va, insns, limit=200000):
        """Linear sweep following direct jumps, for the import-register scan.  (Limit: more than the
        largest procedure.  It was 4000: past that, mov ebx, [__vbaAryLock] was not seen, so ebx
        still held the bounds error, call ebx ended the procedure, and DarwinBots Reproduce lost
        everything after its 5400th instruction: the birth tie, the new bot's DNA length...)"""
        work = [va]
        while work and len(insns) < limit:
            pc = work.pop()
            while len(insns) < limit:
                if pc in insns:
                    break
                ins = decode(self.prj.im, pc)
                if ins is None:
                    break
                insns[pc] = ins
                m = ins.mnemonic
                if m == "ret" or is_noreturn_call(self.prj.im, ins):
                    break                       # (the error stubs end the procedure's code: past
                                                # them is the next one's, and limit ran out on it
                                                # before the rest of this one was seen)
                if m == "jmp":
                    op = ins.operands[0]
                    if op.type == x86.X86_OP_IMM and self._inside(op.imm) and \
                            (op.imm == self.va or not seh_prologue(self.prj.im.bytes(op.imm, 32))):
                        pc = op.imm
                        continue
                    break                       # (or a tail call: another procedure)
                if m.startswith(("j", "loop")) and m != "jo" and \
                        ins.operands[0].type == x86.X86_OP_IMM:
                    work.append(ins.operands[0].imm)    # (jo: the overflow error, a stub of those)
                pc += ins.size

    def _sweep_more(self, va, insns, starts):
        im = self.prj.im
        pc = va
        while pc not in insns:
            ins = decode(im, pc)
            if ins is None:
                return
            insns[pc] = ins
            if ins.mnemonic == "jmp" and ins.operands[0].type == x86.X86_OP_IMM and \
                    self._inside(ins.operands[0].imm):
                # a handler's jump back into the procedure (Resume label: gh_zip
                # CompressArchive "Resume QH" jumps into the middle of code the
                # first sweep decoded) starts a block there
                starts.add(ins.operands[0].imm)
                self._sweep_more(ins.operands[0].imm, insns, starts)
                return
            if ins.mnemonic in ("ret", "jmp") or is_noreturn_call(im, ins, self.iatregs):
                return
            if ins.mnemonic.startswith("j"):
                starts.add(ins.operands[0].imm)
                starts.add(pc + ins.size)
                self._sweep_more(ins.operands[0].imm, insns, starts)
            pc += ins.size

    def _switch(self, im, insns, pc, ins):
        """jmp dword ptr [reg*4 + table], guarded by "cmp reg, N; ja default":
        a Select Case jump table with N + 1 entries.  Records
        self.switches[pc] = (index register, [target per index])."""
        op = ins.operands[0]
        if op.type != x86.X86_OP_MEM or op.mem.base != 0 or op.mem.index == 0 or \
                op.mem.scale != 4 or not self._inside(op.mem.disp):
            return False
        reg = op.mem.index
        n = None
        slot = None                         # the frame slot the index was loaded from
        prev = [insns[a] for a in sorted(insns) if a < pc][-12:]
        # two tables (sparse cases): "movzx eax, byte ptr [eax + bytes]; jmp
        # [eax*4 + jumps]" - the index picks a byte, the byte a jump (VBDoom
        # P_CrossSpecialLine); the guard tests the index register before
        bytetab = None
        extra = 0                           # instructions before the jmp the node leaves out
        if prev and prev[-1].mnemonic == "movzx" and prev[-1].operands[0].type == x86.X86_OP_REG \
                and prev[-1].operands[0].reg == reg and prev[-1].operands[1].type == x86.X86_OP_MEM \
                and prev[-1].operands[1].size == 1 and prev[-1].operands[1].mem.scale == 1 and \
                prev[-1].operands[1].mem.disp and self._inside(prev[-1].operands[1].mem.disp):
            mo = prev[-1].operands[1].mem
            if mo.base and not mo.index:
                bytetab, reg = mo.disp, mo.base
                prev = prev[:-1]
                extra = 1
        # the same in unoptimised code: "xor eax, eax; mov al, byte ptr [ecx +
        # bytes]; jmp [eax*4 + jumps]" (a target Convert)
        elif len(prev) >= 2 and prev[-1].mnemonic == "mov" and prev[-2].mnemonic == "xor" and \
                prev[-1].operands[0].type == x86.X86_OP_REG and \
                _md.reg_name(prev[-1].operands[0].reg) == _md.reg_name(reg)[1] + "l" and \
                prev[-1].operands[1].type == x86.X86_OP_MEM and prev[-1].operands[1].size == 1 and \
                prev[-2].operands[0].type == x86.X86_OP_REG and prev[-2].operands[0].reg == reg and \
                prev[-2].operands[1].type == x86.X86_OP_REG and prev[-2].operands[1].reg == reg:
            mo = prev[-1].operands[1].mem
            if mo.base and not mo.index and mo.disp and self._inside(mo.disp):
                bytetab, reg = mo.disp, mo.base
                prev = prev[:-2]
                extra = 2
        for p in reversed(prev):
            if p.mnemonic == "cmp" and p.operands[0].type == x86.X86_OP_REG and \
                    p.operands[0].reg == reg and p.operands[1].type == x86.X86_OP_IMM:
                n = p.operands[1].imm + 1
                break
            if slot is not None and p.mnemonic == "cmp" and \
                    p.operands[0].type == x86.X86_OP_MEM and \
                    (p.operands[0].mem.base, p.operands[0].mem.disp) == slot and \
                    p.operands[1].type == x86.X86_OP_IMM:
                # unoptimised code: "cmp [ebp-74h], 8; ja default; mov eax,
                # [ebp-74h]; jmp [eax*4 + table]" (VBDoom G_Ticker, P_CrossSpecialLine)
                n = p.operands[1].imm + 1
                break
            if slot is None and p.mnemonic == "mov" and p.operands[0].type == x86.X86_OP_REG and \
                    p.operands[0].reg == reg and p.operands[1].type == x86.X86_OP_MEM and \
                    p.operands[1].mem.index == 0:
                slot = (p.operands[1].mem.base, p.operands[1].mem.disp)
                continue
            if p.operands and p.operands[0].type == x86.X86_OP_REG and \
                    p.operands[0].reg == reg and p.mnemonic not in ("cmp", "test", "push"):
                break                       # the index is computed here: no guard seen
        if not n or n > 512:
            if os.environ.get("VB6DEC_DEBUG_SWITCH"):
                sys.stderr.write("no jump table at %x: %s\n" % (pc, " / ".join(
                    "%s %s" % (p.mnemonic, p.op_str) for p in insns_tail(insns, pc))))
            return False
        targets = []
        if bytetab is not None:
            picks = [im.u8(bytetab + k) for k in range(n)]
            jumps = max(picks) + 1
        else:
            picks, jumps = list(range(n)), n
        for k in range(jumps):
            t = im.u32(op.mem.disp + 4 * k)
            if not self._inside(t):
                return False
            targets.append(t)
        targets = [targets[k] for k in picks]
        if not hasattr(self, "switches"):
            self.switches = {}
            self.tables = []
        # (reg, a target per index, how many byte-table loads the node leaves out)
        self.switches[pc] = (_md.reg_name(reg), targets, extra)
        self.tables.append((op.mem.disp, op.mem.disp + 4 * jumps))
        if bytetab is not None:
            self.tables.append((bytetab, bytetab + n))
        for lo, hi in self.tables[-2:] if bytetab is not None else self.tables[-1:]:
            for a in [a for a in insns if lo <= a < hi]:
                del insns[a]                # decoded before the table was known
        return True

    def _inside(self, va):
        p = self.prj
        return p.code_start <= va < p.code_end

    def __repr__(self):
        return "<Proc %s %#x>" % (self.name, self.va)


def find_declare_stubs(prj):
    """Map Declare thunk VA -> (lib, function).

    Thunk: mov eax,[cache]; or eax,eax; je L; jmp eax; L: push desc;
           mov eax,DllFunctionCall-thunk; call eax; jmp eax
    """
    im = prj.im
    descs = {ptr: (lib, fn) for lib, fn, ptr in prj.externals if lib}
    out = {}
    text = [s for s in im.sections if s[2] == ".text"][0]
    data = im.bytes(text[0], text[1])
    i = data.find(b"\x0b\xc0\x74\x02\xff\xe0\x68")
    while i >= 0:
        stub = text[0] + i - 5
        if data[i - 5] == 0xa1:
            d = im.u32(text[0] + i + 7)
            if d in descs:
                out[stub] = descs[d]
        i = data.find(b"\x0b\xc0\x74\x02\xff\xe0\x68", i + 1)
    return out


def discover(prj):
    """All procedures: event handlers, Sub Main, and everything reachable by
    direct calls, plus any SEH-prologue procedures found by scanning."""
    im = prj.im
    procs = {}
    roots = []
    for obj in prj.objects:
        for c in obj.controls:
            for j, stub, target in c.events:
                if target:
                    roots.append((target, obj, ("event", c, j)))
    if prj.sub_main:
        roots.append((prj.sub_main, None, ("submain",)))
    # SEH prologues anywhere in the code range
    code = im.bytes(prj.code_start, prj.code_end - prj.code_start)
    i = code.find(b"\x55\x8b\xec")
    while i >= 0:
        if seh_prologue(code[i:i + 32]):
            roots.append((prj.code_start + i, None, None))
        i = code.find(b"\x55\x8b\xec", i + 1)
    work = list(roots)
    info = {}
    for va, obj, why in roots:
        if why is not None or va not in info:
            info.setdefault(va, (obj, why))
    while work:
        va, obj, why = work.pop()
        if va in procs:
            continue
        p = Proc(prj, va)
        o, w = info.get(va, (None, None))
        p.owner, p.why = o, w
        procs[va] = p
        for t in p.calls:
            if prj.code_start <= t < prj.code_end and t not in procs:
                work.append((t, None, None))
    return dict(sorted(procs.items()))
