"""Argument count of a Declare'd API function, read from the DLL itself:
the stdcall export ends in "ret N" (N/4 arguments).  Forwarded exports are
followed; Win98's 32->16 thunks (push id; jmp common) are recognised by
the stack size they pass to the common thunk routine when possible."""
import os

import capstone
import pefile
from capstone import x86

from . import msfiles

SYSDIR = msfiles.system_dir()
_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True
_cache = {}


def _open(lib):
    key = lib.lower()
    if key in _cache:
        return _cache[key]
    name = lib if "." in lib else lib + ".dll"
    path = None
    if os.path.isdir(SYSDIR):
        for fn in os.listdir(SYSDIR):
            if fn.lower() == name.lower():
                path = os.path.join(SYSDIR, fn)
                break
    pe = None
    if path:
        try:
            pe = pefile.PE(path, fast_load=True)
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
            pe.mem = pe.get_memory_mapped_image()
            pe.exports = {}
            pe.ordinals = {}
            ed = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
            if ed is not None:
                for e in ed.symbols:
                    if e.name:
                        pe.exports[e.name.decode()] = e
                    pe.ordinals[e.ordinal] = e
        except Exception:
            pe = None
    _cache[key] = pe
    return pe


def stack_bytes(lib, fn, depth=0):
    """Bytes of arguments the export pops, or None when unknown."""
    pe = _open(lib)
    if pe is None or depth > 4:
        return None
    e = pe.exports.get(fn)
    if e is None and fn.startswith("#"):
        e = pe.ordinals.get(int(fn[1:]))
    if e is None:
        return None
    if e.forwarder:
        dll, _, sym = e.forwarder.decode().partition(".")
        return stack_bytes(dll, sym, depth + 1)
    return _walk(pe, e.address)


def _walk(pe, rva, limit=2000):
    base = pe.OPTIONAL_HEADER.ImageBase
    mem = pe.mem
    # Win98 32->16 thunk: "mov cx, NNxx; call [common]", NN = argument bytes
    first = list(_md.disasm(mem[rva:rva + 16], base + rva))[:2]
    if len(first) == 2 and first[0].mnemonic == "mov" and first[0].op_str.startswith("cx,") \
            and first[1].mnemonic == "call" and first[0].operands[1].type == x86.X86_OP_IMM:
        return (first[0].operands[1].imm >> 8) & 0xff
    seen = set()
    work = [rva]
    rets = []
    steps = 0
    while work and steps < limit:
        pc = work.pop()
        while steps < limit:
            if pc in seen or pc < 0 or pc >= len(mem):
                break
            seen.add(pc)
            steps += 1
            ins = next(_md.disasm(mem[pc:pc + 16], base + pc), None)
            if ins is None:
                break
            m = ins.mnemonic
            if m == "ret":
                rets.append(ins.operands[0].imm if ins.operands else 0)
                break
            if m == "jmp":
                op = ins.operands[0]
                if op.type == x86.X86_OP_IMM:
                    pc = op.imm - base
                    continue
                break
            if m.startswith("j"):
                op = ins.operands[0]
                if op.type == x86.X86_OP_IMM:
                    work.append(op.imm - base)
            pc += ins.size
    if rets:
        # the most common non-zero pop wins (cleanup paths may differ)
        nz = [r for r in rets if r] or rets
        return max(set(nz), key=nz.count)
    return None


def arity(lib, fn):
    b = stack_bytes(lib, fn)
    return None if b is None else b // 4
