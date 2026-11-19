"""Project-level checks for compile errors VB6 would report in decompiled
source: ByRef argument type mismatches, undeclared constants in Type bounds,
assignments to whole array fields.

usage: vbcheck.py PROJECT_DIR...
"""
import os
import re
import sys

SCALAR = {"byte", "boolean", "integer", "long", "single", "double", "currency", "date",
          "string"}
PROC = re.compile(r"^\s*(?:Public |Private |Friend )?(?:Static )?(Sub|Function|Property (?:Get|Let|Set))"
                  r"\s+(\w+)\s*\((.*)\)(?:\s+As\s+(\w+))?", re.I)
DECLARE = re.compile(r'^\s*(?:Public |Private )?Declare (?:Sub|Function) (\w+) Lib "[^"]*"'
                     r'(?: Alias "[^"]*")?\s*\((.*)\)(?:\s+As\s+(\w+))?', re.I)
VAR = re.compile(r"^\s*(?:Dim|Static|Public|Private|Global)\s+(?!Declare\b|Type\b|Const\b|Enum\b|"
                 r"Sub\b|Function\b|Property\b|Event\b)(.*)$", re.I)
TYPE = re.compile(r"^\s*(?:Public |Private )?Type (\w+)", re.I)
CONST = re.compile(r"^\s*(?:Public |Private )?Const (\w+)", re.I)


def split_top(s, sep=","):
    """Split at top-level separators (outside parentheses and strings)."""
    out, depth, cur, q = [], 0, [], False
    for ch in s:
        if ch == '"':
            q = not q
        elif not q and ch == "(":
            depth += 1
        elif not q and ch == ")":
            depth -= 1
        if ch == sep and depth == 0 and not q:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return [x.strip() for x in out]


def params(text):
    """[(byref, name, type)] of a parameter list."""
    out = []
    for p in split_top(text):
        if not p:
            continue
        m = re.match(r"(?:Optional\s+)?(?:(ByVal|ByRef)\s+)?(?:ParamArray\s+)?(\w+)(\(\))?"
                     r"(?:\s+As\s+(\w+))?", p, re.I)
        if m:
            out.append((m.group(1) is None or m.group(1).lower() == "byref", m.group(2),
                        (m.group(4) or "Variant").lower()))
    return out


def var_decls(text):
    """[(name, type, is_array)] of a Dim/Public/Private statement body."""
    out = []
    text = re.sub(r"^WithEvents\s+", "", text.strip(), flags=re.I)
    for part in split_top(text):
        m = re.match(r"(\w+)(\s*\([^)]*\))?(?:\s+As\s+(?:New\s+)?([\w.]+))?", part, re.I)
        if m:
            arr = m.group(2)
            kind = False if arr is None else ("dynamic" if not arr.strip()[1:-1].strip() else "fixed")
            out.append((m.group(1), (m.group(3) or "Variant").lower(), kind))
    return out


class Project:
    def __init__(self, path):
        self.files = []
        for fn in sorted(os.listdir(path)):
            if fn.lower().endswith((".bas", ".frm", ".cls", ".ctl")):
                text = open(os.path.join(path, fn), encoding="latin-1").read()
                self.files.append((fn, text.replace("\r", "").split("\n")))
        self.procs = {}         # lower name -> (params, return type)
        self.file_procs = {}    # file -> the same for its own procedures: they come first
                                # (a target: CheckLen in Form19 and in Form18)
        self.types = {}         # lower UDT name -> {field: (type, is_array)}
        self.consts = set()
        self.globals = {}       # lower name -> (type, is_array) for Public module variables
        self.module_vars = {}   # file -> {lower name: (type, is_array)}
        for fn, lines in self.files:
            mv = self.module_vars.setdefault(fn, {})
            in_proc = in_type = None
            for ln in lines:
                if in_type is not None:
                    if re.match(r"^\s*End Type", ln, re.I):
                        in_type = None
                        continue
                    for name, t, arr in var_decls(ln):
                        self.types[in_type][name.lower()] = (t, arr)
                    continue
                m = TYPE.match(ln)
                if m:
                    in_type = m.group(1).lower()
                    self.types[in_type] = {}
                    continue
                m = DECLARE.match(ln)
                if m:
                    self.procs[m.group(1).lower()] = (params(m.group(2)), (m.group(3) or "").lower())
                    continue
                m = PROC.match(ln)
                if m:
                    self.procs[m.group(2).lower()] = (params(m.group(3)), (m.group(4) or "variant").lower())
                    self.file_procs.setdefault(fn, {})[m.group(2).lower()] = self.procs[m.group(2).lower()]
                    in_proc = True
                    continue
                if re.match(r"^\s*End (Sub|Function|Property)", ln, re.I):
                    in_proc = None
                    continue
                m = CONST.match(ln)
                if m:
                    self.consts.add(m.group(1).lower())
                    continue
                m = VAR.match(ln)
                if m and not in_proc:
                    for name, t, arr in var_decls(m.group(1)):
                        mv[name.lower()] = (t, arr)
                        if re.match(r"^\s*(Public|Global)\b", ln, re.I):
                            self.globals[name.lower()] = (t, arr)

    def check(self):
        out = []
        for fn, lines in self.files:
            out += self.check_file(fn, lines)
        return out

    def check_file(self, fn, lines):
        out = []
        scope = None
        local = {}
        in_type = None
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            if in_type is not None:
                if re.match(r"^End Type", s, re.I):
                    in_type = None
                    continue
                m = re.match(r"\w+\s*\(([^)]*)\)", s)
                if m:
                    for w in re.findall(r"[A-Za-z_]\w*", m.group(1)):
                        if w.lower() != "to" and w.lower() not in self.consts:
                            out.append((fn, i, "constant expression required: %s is not declared" % w))
                continue
            if TYPE.match(ln):
                in_type = True
                continue
            m = PROC.match(ln)
            if m:
                scope = m.group(2).lower()
                local = {p[1].lower(): (p[2], False) for p in params(m.group(3))}
                if m.group(1).lower() == "function":
                    local[scope] = ((m.group(4) or "variant").lower(), False)
                continue
            if re.match(r"^End (Sub|Function|Property)", s, re.I):
                scope = None
                continue
            if scope is None or s.startswith("'"):
                continue
            m = re.match(r"^(?:Dim|Static)\s+(.*)$", s, re.I)
            if m:
                for name, t, arr in var_decls(m.group(1)):
                    local[name.lower()] = (t, arr)
                continue
            out += [(fn, i, msg) for msg in self.check_stmt(s, local, fn)]
        return out

    def type_of(self, ref, local, fn):
        """Declared type of a variable reference: x, x.f.g, a(i), x.a(i).f"""
        ref = ref.strip()
        m = re.match(r"^(\w+)(\([^()]*\))?((?:\.\w+(?:\([^()]*\))?)*)$", ref)
        if not m:
            return None
        name, idx, rest = m.group(1).lower(), m.group(2), m.group(3)
        if name[0].isdigit():
            return None                          # a literal
        if name in local:
            t, arr = local[name]
        elif name in self.module_vars.get(fn, {}):
            t, arr = self.module_vars[fn][name]
        elif name in self.globals:
            t, arr = self.globals[name]
        elif name in self.procs or name in self.consts or name in ("me", "true", "false") or \
                name.startswith("vb"):
            return None                          # not a variable (vbNullString: a constant)
        elif idx is not None:
            return None                          # an undeclared name with (): a VB function
        else:
            t, arr = "variant", False           # an undeclared name: an implicit Variant
        if arr and idx is None and not rest:
            return None                          # a whole array: not checked here
        for f in re.findall(r"\.(\w+)(\([^()]*\))?", rest):
            fields = self.types.get(t)
            if fields is None or f[0].lower() not in fields:
                return None
            t, arr = fields[f[0].lower()]
        return t

    def check_stmt(self, s, local, fn):
        out = []
        # assignment to a whole array field of a UDT
        m = re.match(r"^((?:\w+)(?:\.\w+)+)\s*=", s)
        if m:
            base, *path = m.group(1).split(".")
            t = self.type_of(base, local, fn)
            for f in path:
                fields = self.types.get(t or "")
                if fields is None or f.lower() not in fields:
                    break
                t, arr = fields[f.lower()]
                if arr == "fixed" and f == path[-1]:
                    # (a dynamic array field takes another array: .Extra = baExtra)
                    out.append("can't assign to an array: %s" % m.group(1))
        for name, args in self.calls(s):
            sig = self.file_procs.get(fn, {}).get(name.lower()) or self.procs.get(name.lower())
            if sig is None:
                continue
            ps, _ = sig
            for (byref, pname, ptype), a in zip(ps, args):
                if not byref or ptype not in SCALAR and ptype not in self.types:
                    continue
                if not re.match(r"^\w+(\([^()]*\))?(\.\w+(\([^()]*\))?)*$", a):
                    continue                     # an expression: VB passes a temporary
                at = self.type_of(a, local, fn)
                if at is not None and at != ptype:
                    out.append("ByRef argument type mismatch: %s (%s) passed to %s As %s of %s"
                               % (a, at, pname, ptype, name))
        return out

    def calls(self, s):
        """(procedure name, [argument texts]) for each call in a statement."""
        out = []
        s = re.sub(r'"[^"]*"', '""', s)
        m = re.match(r"^(?:Call\s+)?(\w+)\s*(.*)$", s)
        if m and m.group(1).lower() in self.procs and not re.match(r"^\s*=", m.group(2)):
            rest = m.group(2).strip()
            if s.lower().startswith("call ") and rest.startswith("("):
                rest = rest[1:rest.rfind(")")]
            # (Proc (x) stays as it is: the parentheses pass a copy, no ByRef check)
            out.append((m.group(1), split_top(rest)))
            s = rest
        for f in re.finditer(r"\b(\w+)\s*\(", s):
            if f.group(1).lower() not in self.procs:
                continue
            j, depth = f.end(), 1
            while j < len(s) and depth:
                depth += {"(": 1, ")": -1}.get(s[j], 0)
                j += 1
            out.append((f.group(1), split_top(s[f.end():j - 1])))
        return out


if __name__ == "__main__":
    bad = 0
    for d in sys.argv[1:]:
        for fn, i, msg in Project(d).check():
            print("%s:%d: %s" % (os.path.join(d, fn), i, msg))
            bad += 1
    print("vbcheck: %d problem(s)" % bad)
