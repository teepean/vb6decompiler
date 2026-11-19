#!/usr/bin/env python3
"""errlines.py DEC_DIR WORK [NN]: every compile error of a decompiled project,
not just the first.  Works on a copy in the new directory WORK: comments out
the offending line and rebuilds, up to NN times.  A line that opens or closes
a block cannot be commented out alone: replaced by a neutral opener when it is
an If, otherwise the census stops there.  Nothing is deleted."""
import os,re,shutil,subprocess,sys
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
import roundtrip,vbmake
PREFIX=vbmake.PREFIX
VB6=r"C:\Program Files\Microsoft Visual Studio\%s\VB6.EXE"%os.environ.get("VB6DIR","VB98")
src,work=sys.argv[1:3]; nmax=int(sys.argv[3]) if len(sys.argv)>3 else 40
shutil.copytree(src,work,ignore=shutil.ignore_patterns("*.exe","*.map","*.log","*.OBJ"))
vbp=[f for f in os.listdir(work) if f.lower().endswith(".vbp")][0]
env=dict(os.environ,WINEPREFIX=PREFIX,WINEDEBUG="-all")
for k in range(nmax):
    log="mk%02d.log"%k
    subprocess.run(["wine",VB6,"/make",vbp,"/out",log],cwd=work,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=300)
    text=open(os.path.join(work,log),encoding="latin-1").read().replace("\r","")
    if "succeeded" in text:
        print("build succeeded after %d line(s) neutralised"%k); break
    errs=roundtrip.compile_errors(text)
    if not errs: print("no line error:",text.strip()); break
    f,n,msg=errs[0]; p=os.path.join(work,f)
    i,ln=roundtrip.source_line(p,n)
    print("%s:%d: %s\n   | %s"%(f,i,msg,ln.strip()))
    raw=open(p,"rb").read().decode("latin-1").split("\r\n")
    s=raw[i-1].strip()
    if re.match(r"(?i)^(ElseIf\b.*Then|If\b.*\bThen)$",s):
        raw[i-1]=("ElseIf 0 Then" if s.lower().startswith("elseif") else "If 0 Then")+" '"+s
    elif re.match(r"(?i)^(For|Do|Loop|Next|Else|End|Select|Case|Wend|While|With|Sub|Function|Private|Public|Dim)\b",s):
        print("   (block or declaration line: stopping here)"); break
    else:
        raw[i-1]="' "+raw[i-1]
    open(p,"wb").write("\r\n".join(raw).encode("latin-1"))
