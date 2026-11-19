"""Minimal reader for Windows 9x registry files (CREG: SYSTEM.DAT/USER.DAT)."""
import struct, sys


class Reg:
    def __init__(self, path):
        d = self.d = open(path, "rb").read()
        assert d[:4] == b"CREG"
        self.rgkn = 0x20
        assert d[0x20:0x24] == b"RGKN"
        self.root = self.rgkn + struct.unpack_from("<I", d, 0x28)[0]
        self.recs = {}
        pos, blk = struct.unpack_from("<I", d, 8)[0], 0
        while pos + 0x20 <= len(d) and d[pos:pos + 4] == b"RGDB":
            size = struct.unpack_from("<I", d, pos + 4)[0]
            q, end = pos + 0x20, pos + size
            while q + 20 <= end:
                rsize, rid, used, nlen, nval, _ = struct.unpack_from("<IIIHHI", d, q)
                if rsize < 20:
                    break
                if rid != 0xffffffff:
                    self.recs[(rid >> 16, rid & 0xffff)] = q
                q += rsize
            pos, blk = end, blk + 1

    def node(self, off):
        f = struct.unpack_from("<IIIIIIHH", self.d, off)
        return {"parent": f[3], "child": f[4], "next": f[5], "keyno": f[6], "block": f[7]}

    def record(self, n):
        q = self.recs.get((n["block"], n["keyno"]))
        if q is None:
            return None, []
        d = self.d
        rsize, rid, used, nlen, nval, _ = struct.unpack_from("<IIIHHI", d, q)
        name = d[q + 20:q + 20 + nlen].decode("latin-1")
        p, vals = q + 20 + nlen, []
        for _ in range(nval):
            vt, _u, vnl, vdl = struct.unpack_from("<IIHH", d, p)
            vals.append((d[p + 12:p + 12 + vnl].decode("latin-1"), vt, d[p + 12 + vnl:p + 12 + vnl + vdl]))
            p += 12 + vnl + vdl
        return name, vals

    def children(self, off):
        n = self.node(off)
        c = n["child"]
        while c != 0xffffffff:
            yield self.rgkn + c
            c = self.node(self.rgkn + c)["next"]

    def find(self, path):
        off = self.root
        for part in path.split("\\"):
            for c in self.children(off):
                if (self.record(self.node(c))[0] or "").lower() == part.lower():
                    off = c
                    break
            else:
                return None
        return off

    def export(self, off, prefix):
        """REGEDIT4 text for the key at off and everything below it."""
        name, vals = self.record(self.node(off))
        out = ["[%s]" % prefix]
        for vn, vt, vd in vals:
            key = "@" if not vn else '"%s"' % vn
            if vt == 1:
                s = vd.decode("latin-1").rstrip("\0").replace("\\", "\\\\").replace('"', '\\"')
                out.append('%s="%s"' % (key, s))
            elif vt == 4 and len(vd) == 4:
                out.append("%s=dword:%08x" % (key, struct.unpack("<I", vd)[0]))
            else:
                out.append("%s=hex:%s" % (key, ",".join("%02x" % b for b in vd)))
        out.append("")
        for c in self.children(off):
            out += self.export(c, prefix + "\\" + self.record(self.node(c))[0])
        return out


if __name__ == "__main__":
    r = Reg(sys.argv[1])
    for path in sys.argv[2:]:
        off = r.find(path)
        print("; %s %s" % (path, "found" if off else "missing"), file=sys.stderr)
