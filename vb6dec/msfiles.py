"""Where Microsoft's files are.  vb6dec reads them and does not come with them: VB6.OLB and MSVBVM60.DLL say what
the runtime's and the controls' members are called, STDOLE2.TLB and the OCXs the rest, WIN32API.TXT the Declares.

For each file, the first of:
 1. its environment variable (VB6OLB, MSVBVM60, STDOLE2, VBWIN32API; for folders VBSYSDIR, VBOCXDIR), which wins
    even if nothing is there: that is how to see what the file is worth (tools/needfiles.py);
 2. VB6DIR, a folder with copies, sub-folders too, in any spelling of the name (run_vb6dec.py --vb6-dir);
 3. on Windows, where VB6's setup and Windows put them, by the registry;
 4. in a development checkout, share/ and SYSTEM/ above the project.
"""
import os
import sys

WINDOWS = sys.platform == "win32"
HERE = os.path.dirname(os.path.abspath(__file__))
CHECKOUT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
ENV = {"VB6.OLB": "VB6OLB", "MSVBVM60.DLL": "MSVBVM60", "STDOLE2.TLB": "STDOLE2", "WIN32API.TXT": "VBWIN32API"}
IN_CHECKOUT = {
    "VB6.OLB": ("share", "Microsoft Visual Studio", "VB98", "VB6.OLB"),
    "MSVBVM60.DLL": ("SYSTEM", "MSVBVM60.DLL"),
    "STDOLE2.TLB": ("SYSTEM", "STDOLE2.TLB"),
    "WIN32API.TXT": ("share", "Microsoft Visual Studio", "Common", "Tools", "Winapi", "WIN32API.TXT"),
}
LIBRARY = (".ocx", ".dll", ".tlb", ".olb")
program = None          # the program being decompiled: its OCXs may well be beside it
_found = {}


def _search(top, name, depth=4):
    """top/name or below, in any spelling of the name."""
    try:
        entries = sorted(os.listdir(top))
    except OSError:
        return ""
    for e in entries:
        if e.lower() == name.lower() and os.path.isfile(os.path.join(top, e)):
            return os.path.join(top, e)
    if depth:
        for e in entries:
            p = os.path.join(top, e)
            if os.path.isdir(p):
                r = _search(p, name, depth - 1)
                if r:
                    return r
    return ""


# ---- the Windows registry ------------------------------------------------------------------------------------
def _reg_value(root, key, value=""):
    """A value in the 32-bit view (VB6 and its controls are 32-bit programs), then the other."""
    import winreg
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as k:
                v = winreg.QueryValueEx(k, value)[0]
                if isinstance(v, str) and v:
                    return os.path.expandvars(v)
        except OSError:
            pass
    return ""


def _reg_subkeys(root, key):
    import winreg
    out = []
    try:
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    return out
                i += 1
    except OSError:
        return out


def _existing(path):
    """A path from the registry as a file: without its type library number ("x.dll\\2") and its quotes.  A
    32-bit program's System32 is SysWOW64 to us."""
    path = path.strip().strip('"')
    if path[-2:-1] == "\\" and path[-1:].isdigit():
        path = path[:-2]
    if os.path.isfile(path):
        return path
    low = path.lower()
    i = low.find("\\system32\\")
    if i >= 0:
        p = os.path.join(system_dir(), path[i + 10:])
        if os.path.isfile(p):
            return p
    return ""


def _typelib_file(libid):
    """The file of a type library, its latest version."""
    import winreg
    root = "TypeLib\\" + libid

    def version(v):
        try:
            return [int(x, 16) for x in v.split(".")]
        except ValueError:
            return [0]
    for ver in sorted(_reg_subkeys(winreg.HKEY_CLASSES_ROOT, root), key=version, reverse=True):
        for lcid in _reg_subkeys(winreg.HKEY_CLASSES_ROOT, root + "\\" + ver):
            p = _reg_value(winreg.HKEY_CLASSES_ROOT, "%s\\%s\\%s\\win32" % (root, ver, lcid))
            p = _existing(p) if p else ""
            if p:
                return p
    return ""


def _vb_folders():
    """Where VB98 may be: what VB6's setup wrote, where its type library is registered, the usual places."""
    import winreg
    out = []
    d = _reg_value(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\6.0\Setup\Microsoft Visual Basic",
                   "ProductDir")
    if d:
        out.append(d)
    p = _typelib_file("{FCFB3D2E-A0FA-1068-A738-08002B3371B5}")
    if p:
        out.append(os.path.dirname(p))
    for v in ("ProgramFiles(x86)", "ProgramW6432", "ProgramFiles"):
        if os.environ.get(v):
            out.append(os.path.join(os.environ[v], "Microsoft Visual Studio", "VB98"))
    return [d for i, d in enumerate(out) if os.path.isdir(d) and d not in out[:i]]


def _on_windows(name):
    if name == "VB6.OLB":
        for d in _vb_folders():
            p = _search(d, name, 0)
            if p:
                return p
    elif name == "WIN32API.TXT":
        for d in _vb_folders():
            p = _search(os.path.join(os.path.dirname(d), "Common", "Tools", "Winapi"), name, 0)
            if p:
                return p
    else:
        return _search(system_dir(), name, 0)
    return ""


# ---- the files -----------------------------------------------------------------------------------------------
def find(name):
    """The path of one of the files in ENV; "" if it is nowhere."""
    v = os.environ.get(ENV[name])
    if v:
        return v
    if name not in _found:
        p = ""
        if os.environ.get("VB6DIR"):
            p = _search(os.environ["VB6DIR"], name)
        if not p and WINDOWS:
            p = _on_windows(name)
        if not p and name in IN_CHECKOUT:
            q = os.path.join(CHECKOUT, *IN_CHECKOUT[name])
            p = q if os.path.isfile(q) else ""
        _found[name] = p
    return _found[name]


def system_dir():
    """The folder of the system's 32-bit DLLs, as far as there is one."""
    v = os.environ.get("VBSYSDIR")
    if v:
        return v
    if WINDOWS:
        root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
        wow = os.path.join(root, "SysWOW64")
        return wow if os.path.isdir(wow) else os.path.join(root, "System32")
    return os.path.join(CHECKOUT, "SYSTEM")


def _library_files(folder, kinds=LIBRARY):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, fn) for fn in names if fn.lower().endswith(kinds)]


def ocx_files():
    """Files that may hold a control's type library.  Of the system's folder only the OCXs: a Windows has thousands
    of DLLs there."""
    out = []
    env = os.environ.get("VBOCXDIR")
    for d in env.split(os.pathsep) if env else ():
        out += _library_files(d)
    if os.environ.get("VB6DIR"):
        out += _library_files(os.environ["VB6DIR"])
    if WINDOWS:
        if program:
            out += _library_files(os.path.dirname(os.path.abspath(program)), (".ocx",))
        out += _library_files(system_dir(), (".ocx",))
        for d in _vb_folders():
            out += _library_files(d, (".ocx", ".olb", ".tlb"))
    elif not env:
        out += _library_files(os.path.join(CHECKOUT, "share", "ocx"))
    seen = set()
    return [p for p in out if not (p.lower() in seen or seen.add(p.lower()))]


def ocx_folder_given():
    return bool(os.environ.get("VBOCXDIR"))


def scan_files():
    """Everything to look through for an interface no library loaded so far has: Scripting's FileSystemObject, and
    the system's own type libraries (New HTMLDocument, MSHTML.TLB)."""
    out = ocx_files()
    if WINDOWS:
        out += [p for p in (_search(system_dir(), n, 0) for n in ("scrrun.dll", "msxml3.dll", "msxml6.dll")) if p]
    return out + _library_files(system_dir(), (".tlb", ".olb") if WINDOWS else (".tlb",))


_libraries = {}


def library_of(guid):
    """On Windows, the file with the type library that has this interface or class, from the registry."""
    if not WINDOWS or not guid:
        return ""
    if guid not in _libraries:
        import winreg
        g = "{%s}" % guid.strip("{}").upper()
        path = ""
        for kind in ("Interface", "CLSID"):
            libid = _reg_value(winreg.HKEY_CLASSES_ROOT, "%s\\%s\\TypeLib" % (kind, g))
            if libid:
                path = _typelib_file(libid)
            if path:
                break
        if not path:
            p = _reg_value(winreg.HKEY_CLASSES_ROOT, "CLSID\\%s\\InprocServer32" % g)
            path = _existing(p) if p else ""
        _libraries[guid] = path
    return _libraries[guid]


def is_vb_own(path):
    """A type library VB always has, which a project does not refer to."""
    base = os.path.basename(path or "").lower()
    return not base or base in ("vb6.olb", "msvbvm60.dll") or base.startswith("stdole")


def report():
    """[(file, where it is or "", what is lost without it, or None if nothing can be done without it)]"""
    return [("VB6.OLB", find("VB6.OLB"), None),
            ("MSVBVM60.DLL", find("MSVBVM60.DLL"), None),
            ("STDOLE2.TLB", find("STDOLE2.TLB"), "some OLE types will have no names"),
            ("WIN32API.TXT", find("WIN32API.TXT"), "API calls will have poorer Declare statements")]
