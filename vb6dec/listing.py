"""Annotated disassembly of a VB6 native program."""
from capstone import x86
from .typelib import Registry, INVOKE


def vb_string(im, va):
    """String constant: UTF-16 text preceded by its byte length."""
    try:
        n = im.u32(va - 4)
    except Exception:
        return None
    if n == 0 or n > 4096 or n & 1 or not im.valid(va + n):
        return None
    raw = im.bytes(va, n + 2)
    if raw[-2:] != b"\0\0":
        return None
    s = raw[:-2].decode("utf-16-le", "replace")
    # control characters too (vbBack, vbFormFeed: render.vb_literal prints Chr$(n))
    if all(c.isprintable() or ord(c) < 32 for c in s):
        return s
    return None


def vtable_iface_map(model, proc):
    """For every indirect 'call [reg+off]' find the IID the following
    __vbaHresultCheckObj names: {call VA: (interface, offset)}."""
    im = model.im
    out = {}
    insns = [proc.insns[k] for k in sorted(proc.insns)]
    at = {n.address: k for k, n in enumerate(insns)}
    from .disasm import iat_regs, iat_at, iat_at_flow
    regs = iat_regs(proc, im)
    for i, ins in enumerate(insns):
        if ins.mnemonic != "call":
            continue
        op = ins.operands[0]
        if op.type != x86.X86_OP_MEM or op.mem.base == 0 or op.mem.base == x86.X86_REG_INVALID:
            continue
        off = op.mem.disp
        pushes = []
        j, steps, hops = i + 1, 0, 0
        while j < len(insns) and steps < 64:
            n = insns[j]
            steps += 1
            j += 1
            if n.mnemonic == "jmp" and n.operands[0].type == x86.X86_OP_IMM and hops < 2 and \
                    n.operands[0].imm in at:
                # the check the compiler shares between identical calls (jge
                # past it, else jmp to it: a target Text3_GotFocus's SetFocus)
                j, hops = at[n.operands[0].imm], hops + 1
                continue
            if n.mnemonic == "push" and n.operands[0].type == x86.X86_OP_IMM:
                pushes.append(n.operands[0].imm)
            if n.mnemonic == "call":
                tgt = n.operands[0]
                if tgt.type == x86.X86_OP_MEM and tgt.mem.base == 0:
                    callee = im.imports.get(tgt.mem.disp)
                elif tgt.type == x86.X86_OP_REG:
                    # "call edi"; by the flow: in address order the last write to the register
                    # may be on another path (optimised code, a Case far from the rest: DarwinBots
                    # Toolbar1_ButtonClick Form1.t.Add printed vtbl_34)
                    callee = iat_at_flow(proc, n.address, tgt.reg) or iat_at(regs, n.address, tgt.reg)
                elif tgt.type == x86.X86_OP_IMM and im.valid(tgt.imm) and \
                        bytes(im.bytes(tgt.imm, 2)) == b"\xff\x25":
                    # "call stub", the stub "jmp [iat]" (optimised for size: t80arrmethod)
                    callee = im.imports.get(im.u32(tgt.imm + 2))
                else:
                    callee = None
                if callee == "__vbaHresultCheckObj":
                    if len(pushes) >= 2 and pushes[0] == off and im.valid(pushes[1]):
                        g = im.guid(pushes[1])
                        # the project's own forms and classes are not in any type library
                        iface = Registry.find_guid(g) or getattr(model, "iface_by_iid", {}).get(g)
                        out[ins.address] = (iface, off, g)
                if callee == "__vbaGenerateBoundsError":
                    # (the object fetched again for the check, by way of its array's bounds:
                    # optimised rob(i).console.textout, DarwinBots openconsole)
                    continue
                if callee in ("__vbaDerefAry1", "__vbaDerefAry") and pushes and pushes[0] == off:
                    # (the object fetched once more for the check, unoptimised: rob(i).console.cycle
                    # printed vtbl_718)
                    pushes = pushes[:2]
                    continue
                break
    return out


def member_at(iface, off):
    if iface is None:
        return None
    hits = [f for f in iface.all_funcs() if f.vtoff == off]
    if not hits:
        return None
    named = [f for f in hits if not f.name.startswith("_")]
    f = (named or hits)[0]
    return "%s.%s%s" % (iface.name, f.name, "" if f.invkind == 1 else " [%s]" % INVOKE.get(f.invkind, f.invkind))


def annotate(model, ins, vtmap):
    im = model.im
    m = ins.mnemonic
    if m in ("call", "jmp"):
        op = ins.operands[0]
        if op.type == x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
            return im.imports.get(op.mem.disp)
        if op.type == x86.X86_OP_IMM:
            n = model.callee_name(op.imm)
            if n:
                return n
            # jmp through an import thunk "jmp [iat]"
            from .disasm import decode
            t = decode(im, op.imm)
            if t is not None and t.mnemonic == "jmp" and t.operands[0].type == x86.X86_OP_MEM:
                return im.imports.get(t.operands[0].mem.disp)
        if ins.address in vtmap:
            iface, off, guid = vtmap[ins.address]
            return member_at(iface, off) or "%s+%#x" % (iface.name if iface else guid, off)
        if op.type == x86.X86_OP_MEM and op.mem.disp >= 0x2f8 and m == "call":
            return "vtbl+%#x (Me control/method?)" % op.mem.disp
    for op in ins.operands:
        if op.type == x86.X86_OP_IMM and im.valid(op.imm):
            s = vb_string(im, op.imm)
            if s is not None:
                return repr(s)
            if op.imm in model.names:
                return model.names[op.imm]
        if op.type == x86.X86_OP_MEM and op.mem.base == 0 and im.valid(op.mem.disp):
            sec = im.section_of(op.mem.disp)
            if op.mem.disp in im.imports:
                return im.imports[op.mem.disp]
            if sec == ".data":
                return "global_%X" % op.mem.disp
    return None


def proc_listing(model, proc):
    vtmap = vtable_iface_map(model, proc)
    lines = []
    owner = proc.owner.name if proc.owner else "?"
    if getattr(proc, "truth", None):
        lines.append("; ===== truth: %s.%s" % proc.truth)
    lines.append("; ===== %s.%s @%#x  seh=%s procdesc=%s exit=%s cleanup=%s ret %d" % (
        owner, proc.name, proc.va, proc.seh,
        hex(proc.procdesc) if proc.procdesc else None,
        hex(proc.exit_va) if proc.exit_va else None,
        hex(proc.cleanup_va) if proc.cleanup_va else None, proc.ret_pop))
    for b in sorted(proc.blocks.values(), key=lambda b: b.start):
        lines.append("  loc_%X:   ; %s preds=%s" % (b.start, b.kind, ",".join("%X" % p for p in b.preds)))
        for ins in b.insns:
            a = annotate(model, ins, vtmap)
            t = "    %08X  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str)
            if a:
                t = "%-60s ; %s" % (t, a)
            lines.append(t)
    return "\n".join(lines)


def program_listing(model):
    out = []
    prj = model.prj
    out.append("; project %s  (%s)  runtime build %d" % (prj.name, model.im.path, prj.runtime_build))
    for o in prj.objects:
        out.append("; object %d %s %s" % (o.index, o.kind, o.name))
        for c in o.controls:
            cc, di, ev = model.control_class(c)
            out.append(";    control %-16s %-14s index %d" % (c.name, cc.name if cc else c.guid, c.index))
    for va, n in sorted(model.declares.items()):
        out.append("; declare %#x %s!%s" % (va, n[0], n[1]))
    for p in model.procs.values():
        out.append("")
        out.append(proc_listing(model, p))
    return "\n".join(out)
