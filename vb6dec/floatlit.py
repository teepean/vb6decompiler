"""A Single's bits printed as an integer.

Fast code stores and compares a Single literal with integer instructions: x = 1.5 is
mov [x], 3FC00000h and If x = 1.5 is cmp [x], 3FC00000h, with no FPU in sight, so what the
lift has is a dword constant.  VB reads x = &H3FC00000 as the number 1069547520 and
stores that number's bits instead (DarwinBots regang's .b = 0.1, SphereCd = 0.09, and 248
more).  What a Single can hold in such a store is only ever bits, so with the final Types
in hand the literal is a float, whatever it looks like.  The same for a Currency's literal, its
bits being ten thousand times the number.  Works on the written source, as Dim lines, Type blocks
and parameter lists are what VB will go by."""
import os
import re
import struct

HEAD = re.compile(r"\s*(?:(?:Public|Private|Friend) )?(?:Static )?(Sub|Function|Property \w+) "
                  r"(\w+)\((.*)\)(?: As (\w+))?\s*$")
TAIL = re.compile(r"\s*End (?:Sub|Function|Property)\b")
TYPE = re.compile(r"\s*(?:(?:Public|Private) )?Type (\w+)\s*$")
DECL = re.compile(r"\s*(?:(?:Public|Private|Global|Dim|Static) )(\w+)(\([^)]*\))? As (\w+)")
LOCAL = re.compile(r"\s*(?:Dim|Static) (\w+)(\([^)]*\))? As (\w+)")
FIELD = re.compile(r"\s*(\w+)(\([^)]*\))? As (\w+)")
PARAM = re.compile(r"(?:Optional )?(?:ByVal |ByRef )?(?:ParamArray )?(\w+)(\(\))? As (\w+)")
LIT = re.compile(r"(?<![\w.&!#])(&H[0-9A-F]{5,8}&?|-?\d{8,10})(?![\w.!#])")
OPS = ("<>", "<=", ">=", "=", "<", ">", "+", "-", "*", "/")
STMT_CALL = re.compile(r"\s*(?:\d+ +)?(?:[\w().]+\.)?(\w+) +(?![=<>])")


def value_of(text):
    """The dword a literal's text means, or None."""
    t = text.rstrip("&")
    try:
        n = int(t[2:], 16) if t.startswith("&H") else int(t)
    except ValueError:
        return None
    return n & 0xffffffff if -(1 << 31) <= n < (1 << 32) else None


def float_text(n):
    """Dword n as a Single literal, as short as still gives the same bits; None if it has
    no such value: a NaN, infinity, or a denormal (those are small integers, in a slot
    reused for something else)."""
    if n & 0x7f800000 in (0, 0x7f800000):
        return None
    raw = struct.pack("<I", n)
    v = struct.unpack("<f", raw)[0]
    if v == int(v) and abs(v) < 1e7:
        return "%d!" % v
    for p in (6, 7, 8, 9):
        t = "%.*g" % (p, v)
        if struct.pack("<f", float(t)) == raw:
            break
    return t.upper() + "!"


def lvalue_before(ln, end):
    """The variable or element whose text ends just before position end, or None."""
    i, depth = end, 0
    while i > 0:
        c = ln[i - 1]
        if c == ")":
            depth += 1
        elif c == "(":
            if not depth:
                break
            depth -= 1
        elif not depth and not (c.isalnum() or c in "._"):
            break
        i -= 1
    t = ln[i:end]
    return t if t and (t[0].isalpha() or t[0] == "_") else None


def lvalue_after(ln, start):
    """The same, starting at position start."""
    i, depth = start, 0
    while i < len(ln):
        c = ln[i]
        if c == "(":
            depth += 1
        elif c == ")":
            if not depth:
                break
            depth -= 1
        elif not depth and not (c.isalnum() or c in "._"):
            break
        i += 1
    t = ln[start:i]
    return t if t and (t[0].isalpha() or t[0] == "_") else None


def path_of(text):
    """x(i).f(j).g as [x, f, g]."""
    out, cur, depth = [], "", 0
    for c in text:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif not depth:
            if c == ".":
                out.append(cur)
                cur = ""
            else:
                cur += c
    out.append(cur)
    return out


class Scope:
    def __init__(self, texts):
        self.types, self.public, self.private = {}, {}, {}
        for fn, lines in texts.items():
            mine = self.private.setdefault(fn, {})
            cur = None
            for ln in lines:
                if HEAD.match(ln):
                    break                           # (declarations come first)
                if cur is not None:
                    if ln.strip().startswith("End Type"):
                        cur = None
                    else:
                        m = FIELD.match(ln)
                        if m:
                            cur[m.group(1).lower()] = m.group(3)
                    continue
                m = TYPE.match(ln)
                if m:
                    cur = self.types.setdefault(m.group(1).lower(), {})
                    continue
                m = DECL.match(ln)
                if m:
                    (self.public if ln.lstrip().startswith(("Public", "Global")) else
                     mine)[m.group(1).lower()] = m.group(3)

    def resolve(self, text, fn, local):
        """The declared type of an lvalue's text, or None."""
        parts = path_of(text)
        if parts[0] == "Me" and len(parts) > 1:
            parts = parts[1:]
        n = parts[0].lower()
        t = local.get(n) or self.private.get(fn, {}).get(n) or self.public.get(n)
        for f in parts[1:]:
            if t is None:
                return None
            fields = self.types.get(t.lower())
            t = fields.get(f.lower()) if fields else None
        return t


def split_params(text):
    out, cur, depth = [], "", 0
    for c in text:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "," and not depth:
            out.append(cur)
            cur = ""
        else:
            cur += c
    return out + [cur] if cur.strip() else out


def masked(ln):
    """ln with what its strings say blanked out, positions kept."""
    return re.sub(r'"[^"]*"', lambda q: '"' + " " * (len(q.group(0)) - 2) + '"', ln)


def call_args(mask, start):
    """Spans of the arguments from start up to the call's closing parenthesis or the line's end."""
    spans, depth, s, i = [], 0, start, start
    while i < len(mask):
        c = mask[i]
        if c == "(":
            depth += 1
        elif c == ")":
            if not depth:
                break
            depth -= 1
        elif c == "," and not depth:
            spans.append((s, i))
            s = i + 1
        i += 1
    return spans + [(s, i)]


def literal_edits(mask, fn, local, scope):
    """(start, end, float) of each literal in x = LIT, x <> LIT, x - LIT, LIT * x of a Single x."""
    out = []
    for m in LIT.finditer(mask):
        n = value_of(m.group(1))
        if n is None:
            continue
        before = mask[:m.start()].rstrip()
        var = None
        for op in OPS:
            if before.endswith(op):
                end = len(before) - len(op)
                while end and before[end - 1] == " ":
                    end -= 1
                var = lvalue_before(before, end)
                break
        if var is None or scope.resolve(var, fn, local) != "Single":
            q = re.match(r"\s*(?:<>|<=|>=|=|<|>|\+|-|\*|/)\s*", mask[m.end():])
            var = lvalue_after(mask, m.end() + q.end()) if q else None
            if var is None or scope.resolve(var, fn, local) != "Single":
                continue
        t = float_text(n)
        if t is not None:
            out.append((m.start(), m.end(), t))
    return out


def arg_edits(mask, local, sigs):
    """The same for a literal given to a Single parameter: what the compiler pushes (or leaves in a
    temporary, ByRef) for one is its bits.  datirob.infoupdate n, ..., 1, ... printed &H3F800000."""
    out = []

    def args(name, start):
        sig = sigs.get(name.lower())
        if not sig or name.lower() in local:
            return
        for i, (a, b) in enumerate(call_args(mask, start)):
            if i >= len(sig) or not sig[i]:
                continue
            while a < b and mask[a] == " ":
                a += 1
            while b > a and mask[b - 1] == " ":
                b -= 1
            m = LIT.fullmatch(mask[a:b])
            n = value_of(m.group(1)) if m else None
            t = float_text(n) if n is not None else None
            if t is not None:
                out.append((a, b, t))
    m = STMT_CALL.match(mask)
    if m:
        args(m.group(1), m.end())
    for q in re.finditer(r"\b(\w+)\(", mask):
        args(q.group(1), q.end())
    return out


CY_STORE = re.compile(r"(\s*)([\w.()]+) = (-?\d+|&H[0-9A-F]+&?)\s*$")


def currency_store(ln, fn, local, scope):
    """x = 15000 of a Currency x: a Currency's literal is its bits as well, ten thousand times the
    number (mov eax, 3A98h; xor edx, edx for CyIf = 1.5@; c = 2.5@ with mov [c], 61A8h).  What VB
    converts prints CCur(...), so a plain number here is only ever that.  None if it isn't this."""
    m = CY_STORE.match(ln)
    if not m or scope.resolve(m.group(2), fn, local) != "Currency":
        return None
    t = m.group(3).rstrip("&")
    n = int(t) if not t.startswith("&H") else int(t[2:], 16)
    if t.startswith("&H") and n >= 1 << 31:
        n -= 1 << 32
    if not n:
        return None
    return "%s%s = %s@" % (m.group(1), m.group(2), repr(n / 10000.0).rstrip("0").rstrip("."))


def fix_line(ln, fn, local, scope, sigs):
    """ln with the bits of each Single literal in it written as a float."""
    n = currency_store(ln, fn, local, scope)
    if n is not None:
        return n
    mask = masked(ln)
    edits = sorted(set(literal_edits(mask, fn, local, scope) + arg_edits(mask, local, sigs)),
                   reverse=True)
    for a, b, t in edits:
        ln = ln[:a] + t + ln[b:]
    return ln


def signatures(texts):
    """{procedure name: [is the parameter a Single]}, for names that say one thing."""
    out = {}
    for lines in texts.values():
        for ln in lines:
            m = HEAD.match(ln)
            if not m:
                continue
            sig = []
            for p in split_params(m.group(3)):
                q = PARAM.match(p.strip())
                sig.append(bool(q) and q.group(3) == "Single" and not q.group(2))
            name = m.group(2).lower()
            if name in out and out[name] != sig:
                sig = None                  # (several procedures of that name, in forms)
            out[name] = sig
    return {k: v for k, v in out.items() if v and any(v)}


def fix(outdir, files):
    texts = {}
    for _, fn, _ in files:
        with open(os.path.join(outdir, fn), encoding="cp1252", newline="") as f:
            texts[fn] = f.read().split("\r\n")
    scope = Scope(texts)
    sigs = signatures(texts)
    for fn, lines in texts.items():
        out, local, changed = [], None, False
        for ln in lines:
            m = HEAD.match(ln)
            if m:
                local = {}
                for p in split_params(m.group(3)):
                    q = PARAM.match(p.strip())
                    if q:
                        local[q.group(1).lower()] = q.group(3)
                if m.group(4):
                    local[m.group(2).lower()] = m.group(4)
            elif TAIL.match(ln):
                local = None
            elif local is not None:
                q = LOCAL.match(ln)
                if q:
                    local[q.group(1).lower()] = q.group(3)
                elif LIT.search(ln) or CY_STORE.match(ln):
                    n = fix_line(ln, fn, local, scope, sigs)
                    changed = changed or n != ln
                    ln = n
            out.append(ln)
        if changed:
            with open(os.path.join(outdir, fn), "w", encoding="cp1252", newline="") as f:
                f.write("\r\n".join(out))
