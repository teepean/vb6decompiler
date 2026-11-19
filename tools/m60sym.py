"""COFF symbols of msvbvm60.dbg mapped through OMAP_FROM_SRC to the final DLL; writes rva name(undecorated)"""
import sys,re,struct,bisect
sys.dont_write_bytecode=True
import pefile
DLL,DBG,PUB,OUT=sys.argv[1:5]
d=open(DBG,'rb').read()
nsec=struct.unpack_from('<I',d,0x18)[0]
exp,dbgsz=struct.unpack_from('<II',d,0x1c)
off=0x30+40*nsec+exp
dirs={}
for i in range(dbgsz//28):
    c,ts,ma,mi,t,sz,rva,ptr=struct.unpack_from('<IIHHIIII',d,off); dirs[t]=(ptr,sz); off+=28
def omap(t):
    ptr,sz=dirs[t]
    return [struct.unpack_from('<II',d,ptr+8*i) for i in range(sz//8)]
tab=omap(8); keys=[a for a,_ in tab]
def m8(r):
    i=bisect.bisect_right(keys,r)-1
    if i<0: return None
    a,b=tab[i]
    return None if b==0 else b+(r-a)
und={}
for ln in open(PUB,encoding='latin-1'):
    m=re.match(r'([0-9a-f]+) ([0-9a-f]+):([0-9a-f]+) (\S+)\((.*)\)\s*$',ln.rstrip('\n'))
    if m: und[m[4]]=m[5]
pe=pefile.PE(DLL,fast_load=True); pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_EXPORT']])
exports={e.name.decode():e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
ptr,sz=dirs[1]
nsym,lvasym=struct.unpack_from('<2I',d,ptr)
symoff=ptr+lvasym; strtab=symoff+18*nsym
rows=[]; i=0
while i<nsym:
    raw=d[symoff+18*i:symoff+18*i+18]
    if raw[:4]==b'\0\0\0\0':
        o=struct.unpack_from('<I',raw,4)[0]; e=d.index(b'\0',strtab+o); name=d[strtab+o:e].decode('latin-1')
    else:
        name=raw[:8].rstrip(b'\0').decode('latin-1')
    val,sec,typ,cl,aux=struct.unpack_from('<IhHBB',raw,8)
    if sec>0 and cl in (2,3,6) and not name.startswith('.'):
        rows.append((val,sec,cl,name))
    i+=1+aux
ok=bad=0; badl=[]
seen=set()
with open(OUT,'w',encoding='latin-1',errors='replace') as f:
    for val,sec,cl,name in rows:
        r=m8(val)
        if r is None: r=0
        f.write('%08x %d %s(%s)\n'%(r,cl,name,und.get(name,'')))
        n=re.sub(r'^[_@]+','',name); n=re.sub(r'@\d+$','',n)
        for c in ('__'+n,n):
            if c in exports and c not in seen:
                seen.add(c)
                if exports[c]==r: ok+=1
                else: bad+=1; badl.append((c,hex(exports[c]),hex(r)))
                break
print('exports matched',ok,'mismatch',bad,badl[:12],'unmatched exports',len(exports)-len(seen))
print(sorted(set(exports)-seen)[:40])
print(len(rows),'symbols')
