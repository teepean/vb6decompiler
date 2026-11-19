"""Derive calling-convention facts for every MSVBVM60 export by looking at
its code: stack bytes popped by 'ret N', registers read before written,
FPU result.  Output: data/runtime_auto.json"""
import json, sys
import pefile, capstone
from capstone import x86

path = sys.argv[1]
pe = pefile.PE(path)
base = pe.OPTIONAL_HEADER.ImageBase
mem = pe.get_memory_mapped_image()
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True

REGS = {x86.X86_REG_ECX: "ecx", x86.X86_REG_CX: "ecx", x86.X86_REG_CL: "ecx",
        x86.X86_REG_EDX: "edx", x86.X86_REG_DX: "edx", x86.X86_REG_DL: "edx",
        x86.X86_REG_EAX: "eax", x86.X86_REG_AX: "eax", x86.X86_REG_AL: "eax"}


def analyse(va, depth=0, seen=None):
    """Walk code from va (following jmps and conditional branches) for a
    few hundred instructions; return (set of rets, regs read before write)."""
    seen = seen if seen is not None else set()
    rets, read, written = set(), set(), set()
    work = [(va, frozenset())]
    steps = 0
    while work and steps < 3000:
        pc, wr = work.pop()
        wr = set(wr)
        while steps < 3000:
            if pc in seen:
                break
            seen.add(pc)
            steps += 1
            o = pc - base
            if o < 0 or o >= len(mem):
                break
            ins = next(md.disasm(mem[o:o + 16], pc), None)
            if ins is None:
                break
            r, w = ins.regs_access()
            # xor reg,reg is a write only
            if ins.mnemonic in ("xor", "sub") and len(ins.operands) == 2 and \
               ins.operands[0].type == x86.X86_OP_REG and ins.operands[1].type == x86.X86_OP_REG and \
               ins.operands[0].reg == ins.operands[1].reg:
                r = []
            for reg in r:
                n = REGS.get(reg)
                if n and n not in wr and ins.mnemonic not in ("push",) or (n and n not in wr and ins.mnemonic == "push" and False):
                    read.add(n)
            for reg in w:
                n = REGS.get(reg)
                if n:
                    wr.add(n)
            if ins.mnemonic == "call":
                # assume callee preserves nothing useful: eax/ecx/edx clobbered
                wr |= {"eax", "ecx", "edx"}
            if ins.mnemonic == "ret":
                rets.add(ins.operands[0].imm if ins.operands else 0)
                break
            if ins.mnemonic == "jmp":
                op = ins.operands[0]
                if op.type == x86.X86_OP_IMM:
                    pc = op.imm
                    continue
                rets.add("tail")
                break
            if ins.mnemonic.startswith("j") or ins.mnemonic.startswith("loop"):
                op = ins.operands[0]
                if op.type == x86.X86_OP_IMM:
                    work.append((op.imm, frozenset(wr)))
            pc += ins.size
    return rets, read


out = {}
for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
    if not e.name:
        continue
    name = e.name.decode()
    va = base + e.address
    rets, read = analyse(va)
    nums = sorted(x for x in rets if x != "tail")
    out[name] = {"ordinal": e.ordinal, "va": va,
                 "ret": nums, "tail": "tail" in rets,
                 "regs": sorted(read & {"ecx", "edx", "eax"})}
json.dump(out, open(sys.argv[2], "w"), indent=0, sort_keys=True)
print(len(out))
