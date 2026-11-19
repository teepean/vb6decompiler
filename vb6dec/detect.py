"""What a file is, before anything is done with it: Visual Basic 6 or something else, native code or p-code, and the
flags that can be read from it.  Needs none of Microsoft's files, and says what it can about any file at all.

detect(path) gives a dict; lines(info) the text.  Each flag comes from the file itself: from the VB header and its
project information, or from the code, where the compiler leaves no flag (the checks: resources.compile_options).
"""
import datetime
import os
import struct

from . import resources
from .pe import Image

# section names and strings of the usual packers: what is inside can't be read until it is unpacked
PACKERS = (("UPX", (b"UPX0", b"UPX1", b"UPX2")), ("ASPack", (b".aspack", b".adata")),
           ("PECompact", (b"PEC2", b"PECompact2", b"pec1", b"pec2")), ("Petite", (b".petite",)),
           ("MPRESS", (b".MPRESS1", b".MPRESS2")), ("NsPack", (b".nsp0", b".nsp1", b".nsp2")),
           ("Themida/WinLicense", (b".themida", b".winlice")), ("VMProtect", (b".vmp0", b".vmp1", b".vmp2")),
           ("Enigma", (b".enigma1", b".enigma2")), ("RLPack", (b".RLPack",)), ("FSG", (b"FSG!",)),
           ("ASProtect", (b".aspr", b".ASPr")))
VB16 = ((b"VBRUN100", "Visual Basic 1 (16-bit)"), (b"VBRUN200", "Visual Basic 2 (16-bit)"),
        (b"VBRUN300", "Visual Basic 3 (16-bit)"), (b"VB40016", "Visual Basic 4 (16-bit)"))
RUNTIMES = {"MSVBVM60.DLL": "Visual Basic 6", "MSVBVM50.DLL": "Visual Basic 5", "VB40032.DLL": "Visual Basic 4 (32-bit)"}
# the VB header's +34h: which of VB's own objects the project uses (Print, Form, Screen, Clipboard and App always)
MODEL_OBJECTS = ((0x1, "PictureBox"), (0x2, "Label"), (0x4, "TextBox"), (0x8, "Frame"), (0x10, "CommandButton"),
                 (0x20, "CheckBox"), (0x40, "OptionButton"), (0x80, "ComboBox"), (0x100, "ListBox"), (0x200, "HScrollBar"),
                 (0x400, "VScrollBar"), (0x800, "Timer"), (0x10000, "DriveListBox"), (0x20000, "DirListBox"),
                 (0x40000, "FileListBox"), (0x80000, "Menu"), (0x400000, "Shape"), (0x800000, "Line"),
                 (0x1000000, "Image"))
THREAD_FLAGS = ((1, "apartment threaded"), (2, "requires a license"), (4, "unattended execution"),
                (8, "single threaded"), (0x10, "retained in memory"))
OPTION_NAMES = (("BoundsCheck", "array bounds checks"), ("OverflowCheck", "integer overflow checks"),
                ("FlPointCheck", "floating point error checks"), ("FDIVCheck", "Pentium FDIV checks"))


def _register_use(text, tva):
    """Of 100 instructions, how many use esi, edi or ebx other than to save them, procedure by procedure.  Without
    optimisation variables stay in memory: at most 2.7 in the programs known (tools/regstats.py), against 6.8 and up
    for Small code."""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    total = used = 0
    k = text.find(b"\x55\x8b\xec")
    while k >= 0 and total < 300000:
        n = 0
        for i in md.disasm(text[k:k + 100000], tva + k):
            n += 1
            total += 1
            if i.mnemonic not in ("push", "pop") and any(r in i.op_str for r in ("esi", "edi", "ebx")):
                used += 1
            if i.mnemonic in ("ret", "retn") and n > 3:
                break
        k = text.find(b"\x55\x8b\xec", k + 3)
    return 100.0 * used / total if total else 0.0


def _public_classes(im, reg):
    """How many classes the COM registration data lists.  A Standard EXE lists its UserControls there at most (+24h
    is 1 for those, 2 for a class).  Read from an OCX and from EXEs: VB6 under Wine builds no ActiveX EXE to check with."""
    n = seen = 0
    try:
        off = im.u32(reg) if im.valid(reg) else 0
        while off and seen < 4096:
            e = reg + off
            n += im.u32(e + 0x24) == 2
            off = im.u32(e)
            seen += 1
    except (ValueError, struct.error):
        pass
    return n


def _other_language(pe, data, dlls):
    """A word on what it is, when it is no Visual Basic."""
    if len(pe.OPTIONAL_HEADER.DATA_DIRECTORY) > 14 and pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].Size:
        return ".NET (VB.NET, C# or the like; not classic Visual Basic)"
    if b"SOFTWARE\\Borland\\Delphi" in data or b"Embarcadero Delphi" in data or b"TObject" in data and b"Borland" in data:
        return "Delphi"
    if any(d.startswith(("MSVCR", "MSVCP", "VCRUNTIME", "UCRTBASE", "API-MS-WIN-CRT", "MFC")) for d in dlls):
        return "C or C++ (Microsoft)"
    if b"Go build ID" in data:
        return "Go"
    return "something other than Visual Basic"


def _packer(pe, data):
    names = [s.Name for s in pe.sections]
    for name, marks in PACKERS:
        for mark in marks:
            if any(n.startswith(mark) for n in names) or mark in (b"FSG!", b"PEC2", b"PECompact2") and mark in data[:4096]:
                return name
    return None


def _thunk_calls(im, text, tva):
    """(direct, through a thunk) calls of the VB runtime, and (sub esp, push ecx) prologues: optimised code calls
    [iat] and opens with sub esp, imm8; unoptimised calls only jmp [iat] thunks and opens with push ecx
    (docs/FINDINGS.md: right for all the suite's EXEs)."""
    vb = {slot for slot, name in im.imports.items() if "!" not in name}
    direct = 0
    thunks = set()
    k = text.find(b"\xff")
    while k >= 0:
        if k + 6 <= len(text) and text[k + 1] in (0x15, 0x25):
            if struct.unpack_from("<I", text, k + 2)[0] in vb:
                if text[k + 1] == 0x15:
                    direct += 1
                else:
                    thunks.add(tva + k)
        k = text.find(b"\xff", k + 1)
    thunk = 0
    k = text.find(b"\xe8")
    while k >= 0:
        if k + 5 <= len(text) and (tva + k + 5 + struct.unpack_from("<i", text, k + 1)[0]) & 0xffffffff in thunks:
            thunk += 1
        k = text.find(b"\xe8", k + 1)
    sub_esp = push_ecx = 0
    k = text.find(b"\x55\x8b\xec")
    while k >= 0:
        nxt = text[k + 3:k + 7]
        if nxt[:2] == b"\x83\xec" and nxt[3:4] == b"\x68":
            sub_esp += 1
        elif nxt[:1] == b"\x51":
            push_ecx += 1
        k = text.find(b"\x55\x8b\xec", k + 1)
    return direct, thunk, sub_esp, push_ecx


def _debug_info(pe):
    out = []
    for e in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
        info = getattr(e, "entry", None)
        pdb = getattr(info, "PdbFileName", b"") if info is not None else b""
        if pdb:
            out.append(pdb.rstrip(b"\0").decode("latin-1"))
        else:
            out.append({2: "CodeView", 1: "COFF", 4: "Misc"}.get(e.struct.Type, "type %d" % e.struct.Type))
    return out


def options(im):
    """What the code says of how it was compiled."""
    for va, size, name in im.sections:
        if name.startswith(".text"):
            text, tva = im.bytes(va, size), va
            break
    else:
        return {}
    out = dict(resources.compile_options(im))
    direct, thunk, sub_esp, push_ecx = _thunk_calls(im, text, tva)
    out["direct"], out["thunk"], out["sub_esp"], out["push_ecx"] = direct, thunk, sub_esp, push_ecx
    if direct + thunk:
        # Fast code calls the runtime through [iat]; Small code and no optimisation through jmp [iat] thunks
        if thunk * 2 < direct + thunk:
            out["optimisation"] = "Fast code"
        else:
            out["optimisation"] = "no optimisation" if _register_use(text, tva) < 4.5 else "Small code"
    return out


def detect(path):
    info = {"path": path, "supported": False, "reason": None}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(4096)
            f.seek(0)
            data = f.read()
    except OSError as e:
        info["kind"] = "cannot be read: %s" % e
        return info
    info["size"] = size
    if head[:2] != b"MZ":
        info["kind"] = "not a Windows program"
        info["reason"] = "This is not a Windows program."
        return info
    off = struct.unpack_from("<I", head, 0x3c)[0] if len(head) >= 0x40 else 0
    sig = data[off:off + 2]
    if sig == b"NE":
        info["kind"] = "16-bit Windows program (NE)"
        for mark, name in VB16:
            if mark in data:
                info["language"] = name
                break
        else:
            info["language"] = "something 16-bit"
        info["reason"] = "16-bit programs are not supported: only 32-bit Visual Basic 6."
        return info
    if data[off:off + 4] != b"PE\0\0":
        info["kind"] = "not a Windows PE program (%s)" % ("DOS" if not sig.strip(b"\0") or off == 0 else repr(sig))
        info["reason"] = "This is not a 32-bit Windows program."
        return info
    try:
        im = Image(path)
    except Exception as e:          # (pefile's words for a damaged file)
        info["kind"] = "a damaged Windows program: %s" % e
        info["reason"] = "This file cannot be read as a Windows program."
        return info
    pe = im.pe
    machine = pe.FILE_HEADER.Machine
    info["bits"] = {0x14c: 32, 0x8664: 64}.get(machine)
    info["kind"] = "%s Windows %s (PE)" % ("%d-bit" % info["bits"] if info["bits"] else "machine %#x" % machine,
                                           "library" if pe.FILE_HEADER.Characteristics & 0x2000 else "program")
    stamp = pe.FILE_HEADER.TimeDateStamp
    if stamp:
        try:
            info["built"] = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            pass
    dlls = [e.dll.decode("latin-1").upper() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
    info["dlls"] = dlls
    runtime = [d for d in dlls if d in RUNTIMES]
    magic = im.mem.find(b"VB5!")
    packer = _packer(pe, data)
    if packer:
        info["packer"] = packer
    if runtime and magic < 0 or not runtime and magic < 0 and packer:
        info["language"] = RUNTIMES[runtime[0]] if runtime else "unknown, it is packed"
        info["kind"] += ", packed" if packer else ""
        info["reason"] = ("It is packed with %s: unpack it first, then try again." % packer if packer else
                          "It uses the Visual Basic runtime but has no VB header where it should be: "
                          "it is probably packed or protected.  Unpack it first.")
        return info
    if magic < 0:
        info["language"] = _other_language(pe, data, dlls)
        info["reason"] = "This is no Visual Basic program."
        return info
    info["language"] = RUNTIMES[runtime[0]] if runtime else "Visual Basic 5 or 6, the runtime's name is not among its imports"
    from .project import Project
    try:
        prj = Project(path)
    except Exception as e:          # (a "VB5!" by chance, or a header that is not what it seems)
        info["reason"] = "It has the mark of a VB header, but no VB project can be read from it (%s)." % e
        return info
    info["header"] = prj.hdr
    entry = im.base + pe.OPTIONAL_HEADER.AddressOfEntryPoint
    try:
        info["entry_pushes_header"] = im.u8(entry) == 0x68 and im.u32(entry + 1) == prj.hdr
    except (ValueError, struct.error):
        info["entry_pushes_header"] = False
    names = set(im.imports.values())
    info["engine"] = sorted(n for n in names if n in ("ProcCallEngine", "MethCallEngine"))
    info["pcode"] = prj.pcode
    info["project"] = prj.project_name or prj.name
    info["title"] = prj.title
    info["exe_name"] = prj.exe_base
    ver = resources.version_info(im)
    if ver:
        info["version"] = "%d.%d.%d" % (ver["major"], ver["minor"], ver["revision"])
        info["company"] = ver["strings"].get("CompanyName")
    source = (prj.path_info or "").strip()
    info["source_path"] = source[3:] if source.startswith("*\\A") else source or None
    info["runtime_build"] = prj.runtime_build
    info["lang_dll"] = prj.lang_dll
    info["lcid"] = prj.lcid
    info["thread_flags"] = prj.thread_flags
    info["sub_main"] = bool(prj.sub_main)
    info["mdl"] = (im.u32(prj.hdr + 0x34), im.u32(prj.hdr + 0x38))
    info["declares"] = sum(1 for lib, fn, ptr in prj.externals if lib)
    if pe.FILE_HEADER.Characteristics & 0x2000:
        ext = os.path.splitext(path)[1].lower()
        info["type"] = "ActiveX control" if ext == ".ocx" else "ActiveX DLL"
    else:
        info["type"] = "ActiveX EXE" if _public_classes(im, prj.com_reg) else "Standard EXE"
    kinds = {}
    for o in prj.objects:
        try:
            kinds[o.kind] = kinds.get(o.kind, 0) + 1
        except Exception:
            kinds["?"] = kinds.get("?", 0) + 1
    info["objects"] = kinds
    info["debug"] = _debug_info(pe)
    if not prj.pcode:
        try:
            info["options"] = options(im)
        except Exception:
            pass
    if runtime != ["MSVBVM60.DLL"] and "MSVBVM60.DLL" not in runtime:
        info["reason"] = "This program was made with %s.  Only Visual Basic 6 is supported so far." % info["language"]
    elif prj.pcode:
        info["reason"] = "This program was compiled to p-code.  Only native code is supported so far."
    else:
        info["supported"] = True
    return info


def lines(info):
    """The text of a detection, a line each."""
    out = []

    def add(name, text):
        out.append("  %-12s %s" % (name, text))
    size = info.get("size")
    text = info.get("kind", "?")
    if size is not None:
        text = "%d bytes, %s" % (size, text)
    if info.get("built"):
        text += ", built %s" % info["built"]
    add("File", text)
    if info.get("language"):
        why = []
        if info.get("dlls"):
            why = [d for d in info["dlls"] if d in RUNTIMES]
        if info.get("header"):
            why.append("VB header at %08X%s" % (info["header"], ", the entry point gives it to the runtime"
                                                if info.get("entry_pushes_header") else ""))
        add("Language", info["language"] + (" (%s)" % "; ".join(why) if why else ""))
    if info.get("packer"):
        add("Packed", info["packer"])
    if "pcode" in info:
        if info["pcode"]:
            ev = ", ".join(info.get("engine") or ())
            add("Code", "p-code (no native code in the project information%s)" % (", imports " + ev if ev else ""))
        else:
            add("Code", "native code")
    if info.get("project"):
        add("Project", "%s, %s" % (info["project"], info.get("type")))
    if info.get("title") and info["title"] != info.get("project"):
        add("Title", info["title"])
    if info.get("exe_name"):
        add("EXE name", info["exe_name"] + (".exe" if info.get("type") in ("Standard EXE", "ActiveX EXE") else ""))
    if info.get("version"):
        add("Version", info["version"] + (", " + info["company"] if info.get("company") else ""))
    if info.get("objects"):
        order = (("Form", "form"), ("Module", "module"), ("Class", "class"), ("UserControl", "user control"),
                 ("PropertyPage", "property page"), ("UserDocument", "user document"))

        def count(n, word):
            return "%d %s%s" % (n, word, "" if n == 1 else "es" if word.endswith("ss") else "s")
        parts = [count(info["objects"][k], w) for k, w in order if k in info["objects"]]
        parts += [count(v, k) for k, v in sorted(info["objects"].items()) if k not in dict(order)]
        add("Contents", ", ".join(parts) + ", %s, starts at %s" % (
            count(info.get("declares", 0), "Declare"), "Sub Main" if info.get("sub_main") else "a form"))
    if info.get("source_path"):
        add("Source was", info["source_path"])
    if "runtime_build" in info:
        add("VB build", "%d, language DLL %r, LCID %d" % (info["runtime_build"], info.get("lang_dll"), info.get("lcid", 0)))
    if "thread_flags" in info:
        tf = info["thread_flags"]
        add("Flags", "%02X (%s)" % (tf, ", ".join(t for b, t in THREAD_FLAGS if tf & b) or "none"))
        used = [t for b, t in MODEL_OBJECTS if info["mdl"][0] & b]
        add("VB controls", "%08X (%s)" % (info["mdl"][0], ", ".join(used) or "none"))
    opts = info.get("options") or {}
    if opts:
        if "optimisation" in opts:
            add("Compiled as", "%s (from the code; runtime calls: %d direct, %d through thunks)" % (
                opts["optimisation"], opts["direct"], opts["thunk"]))
        kept = [t for k, t in OPTION_NAMES if opts.get(k) == 0]
        gone = [t for k, t in OPTION_NAMES if opts.get(k) == -1]
        if kept:
            add("Checks on", ", ".join(kept))
        if gone:
            add("Checks off", ", ".join(gone) + " (or nothing in the program needs them)")
    if "debug" in info:
        add("Debug info", ", ".join(info["debug"]) or "none")
    add("Can do it?", "yes" if info.get("supported") else "no: " + (info.get("reason") or "unknown"))
    return out

