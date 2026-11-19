#!/usr/bin/env python3
"""Names from private programs in files that are to be handed out.  A word counts as private when it is in the
decompiled source of a program in --private and in none of --public, nor in the type libraries' names.

usage: privscan.py [--private DIR ...] [--public DIR ...] [--word WORD ...] [--allow FILE] [--lines] PATH ...

PATH is a file or a folder of what goes out.  --word adds a name to look for whatever else says.  --allow names a
file of words checked and found harmless (one a line, # comments).  Prints each word with where it is: comment,
string or code in Python, anywhere in any other text.  Exit status 1 if anything is found.
"""
import argparse
import io
import os
import re
import sys
import tokenize

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
GENERATED = re.compile(r"^(?:[vom][0-9A-Fa-f]+|t\d+|t_[a-z]+\d*|a\d+|f[0-9A-Fa-f]+|global_[0-9A-F]+|Proc_[0-9A-F]+|[0-9A-Fa-f]+)$")
SOURCE = (".bas", ".cls", ".frm", ".ctl", ".vbp", ".dsr", ".pag")
TEXT = (".py", ".txt", ".md", ".bat", ".json", ".c", ".sh")


def words_of(top):
    out = set()
    for root, _, files in os.walk(top):
        for f in files:
            if f.lower().endswith(SOURCE):
                text = open(os.path.join(root, f), encoding="cp1252", errors="replace").read()
                out |= {w.lower() for w in WORD.findall(text) if not GENERATED.match(w)}
    return out


def typelib_words():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from vb6dec import model
    from vb6dec.typelib import TypeLib
    out = set()
    for p in model.DEFAULT_LIBS:
        try:
            lib = TypeLib.from_file(p)
        except Exception:
            continue
        for t in lib.types:
            if t.name:
                out.add(t.name.lower())
            for m in getattr(t, "members", None) or ():
                if getattr(m, "name", None):
                    out.add(m.name.lower())
    return out


def python_parts(path):
    """(kind, line number, text) of a Python file's comments, strings and code."""
    src = open(path, "rb").read()
    code = {}
    try:
        for t in tokenize.tokenize(io.BytesIO(src).readline):
            if t.type == tokenize.COMMENT:
                yield "comment", t.start[0], t.string
            elif t.type == tokenize.STRING or t.type in getattr(tokenize, "_FSTRING_TOKENS", ()):
                yield "string", t.start[0], t.string
            elif t.type == tokenize.NAME:
                yield "code", t.start[0], t.string
    except (tokenize.TokenError, SyntaxError, IndentationError):
        yield "text", 1, src.decode("latin-1")
    return code


def text_parts(path):
    for n, ln in enumerate(open(path, encoding="latin-1", errors="replace"), 1):
        yield "text", n, ln


def walk(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(files):
                if f.lower().endswith(TEXT):
                    yield os.path.join(root, f)


def main(argv):
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("--private", action="append", default=[])
    ap.add_argument("--public", action="append", default=[])
    ap.add_argument("--word", action="append", default=[])
    ap.add_argument("--allow")
    ap.add_argument("--lines", action="store_true")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    bad = set()
    for d in a.private:
        bad |= words_of(d)
    good = typelib_words()
    for d in a.public:
        good |= words_of(d)
    bad -= good
    if a.allow and os.path.exists(a.allow):
        for ln in open(a.allow, encoding="utf-8"):
            ln = ln.split("#")[0].strip().lower()
            if ln:
                bad.discard(ln)
    bad |= {w.lower() for w in a.word}
    want = [w.lower() for w in a.word]
    hits = {}
    for f in walk(a.paths):
        parts = python_parts(f) if f.endswith(".py") else text_parts(f)
        for kind, n, text in parts:
            low = text.lower()
            found = {w.lower() for w in WORD.findall(text)} & bad
            found |= {w for w in want if w in low}
            for w in found:
                hits.setdefault(w, []).append((f, n, kind, text.strip()))
    for w in sorted(hits, key=lambda w: (-len(hits[w]), w)):
        kinds = sorted({h[2] for h in hits[w]})
        print("%-24s %3d  %s" % (w, len(hits[w]), ",".join(kinds)))
        if a.lines:
            for f, n, kind, text in hits[w][:6]:
                print("    %s:%d %s" % (os.path.relpath(f), n, text[:110]))
    print("privscan: %d word(s) in %d place(s)" % (len(hits), sum(map(len, hits.values()))))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
