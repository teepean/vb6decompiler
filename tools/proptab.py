"""Dump pointer tables of a DLL (MSVBVM60's property and method tables, say) by public symbol name: each
entry, the symbol it points to and its first bytes.  SYMBOLS is the list tools/m60sym.py writes.

usage: proptab.py DLL SYMBOLS NAME...
"""
import sys,re,struct
sys.dont_write_bytecode=True
import pefile
DLL,PUB=sys.argv[1:3]
pe=pefile.PE(DLL,fast_load=True); base=pe.OPTIONAL_HEADER.ImageBase; mem=pe.get_memory_mapped_image()
syms={}; byname={}
for ln in open(PUB,encoding='latin-1'):
    m=re.match(r'([0-9a-f]{8}) (\d) (\S+)\((.*)\)\s*$',ln.rstrip('\n'))
    if m and int(m[1],16):
        va=base+int(m[1],16)
        n=m[3]
        s=re.sub(r'^\?','',n); s=re.sub(r'@@3.*','',s)
        syms.setdefault(va,s); byname[s]=va
want=sys.argv[3:]
addrs=sorted(syms)
import bisect
for w in want:
    va=byname.get(w)
    if va is None:
        print(w,'not found'); continue
    i=bisect.bisect_right(addrs,va); end=addrs[i]
    n=(end-va)//4
    print('===',w,hex(va),'entries',n)
    for k in range(n):
        p=struct.unpack_from('<I',mem,va-base+4*k)[0]
        nm=syms.get(p)
        extra=''
        if p and base<=p<base+len(mem):
            raw=mem[p-base:p-base+24]
            extra=raw.hex()
        print('  %3d 0x%02x  %-34s %s'%(k,k,nm or hex(p),extra))
