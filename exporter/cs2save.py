"""cs2save.py — Pure-Python reader for Cities: Skylines II (.cok) save files.

Format reverse-engineered directly from binary .cok saves (no game DLLs required):
  .cok                 = ZIP container (stored):
                           <City>.SaveGameMetadata  (UTF-8 BOM JSON)
                           <City>.SaveGameData      (ECS binary stream)
                           <City>.Texture           (preview thumbnail)
                           <Map>.Prefab             (base map prefab)
  SaveGameData         = 18B header + u16 nTags + nTags*[u32 len, ascii] +
                         sequence of Zstandard blocks [u32 rawSize][u32 zSize][zstd frame]
  block 0              = component type table (name + flags)
                           flags: 1=tag 2=component 4=buffer 8=shared 16=enableable 32=delta+shuffled
  block 1              = system table (last nSys blocks hold per-system serialized data)
  block 2              = archetype table (entity counts + component type indices)
  blocks 3..3+nArch-1  = per-archetype segments [u32 len][bytes] for each non-pure-tag component
  Entity refs          = signed i32 global index across archetypes in order (-1 = null)
"""
import bisect
import glob
import itertools
import json
import os
import struct
import zipfile
from compression import zstd


def decode32(b: bytes, esz: int) -> bytes:
    """Decode flag-32 columns: continuous byte-wise delta (running sum) then byte-plane unshuffle."""
    if not b:
        return b""
    acc = bytes(itertools.accumulate(b, lambda a, x: (a + x) & 0xFF))
    n = len(acc) // esz
    out = bytearray(len(acc))
    for k in range(esz):
        out[k::esz] = acc[k * n : (k + 1) * n]
    return bytes(out)


def decode_buffer32(b: bytes, n_ents: int, esz: int):
    """Decode flag-36 (buffer + delta/shuffled) segments such as Game.Areas.Node.
    Layout: first 4*n_ents bytes = delta+shuffled i32 counts; remainder = delta+shuffled elements of size esz.
    """
    acc = bytes(itertools.accumulate(b, lambda a, x: (a + x) & 0xFF))
    c_raw = acc[: 4 * n_ents]
    c_unsh = bytearray(4 * n_ents)
    for k in range(4):
        c_unsh[k::4] = c_raw[k * n_ents : (k + 1) * n_ents]
    counts = struct.unpack(f"<{n_ents}i", c_unsh)

    tot = sum(counts)
    e_raw = acc[4 * n_ents :]
    assert len(e_raw) == tot * esz, (len(e_raw), tot, esz)
    e_unsh = bytearray(len(e_raw))
    for k in range(esz):
        e_unsh[k::esz] = e_raw[k * tot : (k + 1) * tot]
    return counts, bytes(e_unsh)


def read_utf8_chars(b: bytes, p: int, nchars: int):
    """Read `nchars` Unicode code points from UTF-8 bytes (Colossal Order writes C# string.Length)."""
    start = p
    for _ in range(nchars):
        c = b[p]
        if c < 0x80:
            p += 1
        elif c < 0xE0:
            p += 2
        elif c < 0xF0:
            p += 3
        else:
            p += 4
    return b[start:p].decode("utf-8", "replace"), p


def parse_path_elements(buf: bytes, n_ents: int):
    """Parse variable-length Game.Pathfind.PathElement buffer for `n_ents` entities.
    Returns list of lists of (lane_entity, start_t, end_t, flags).
    """
    q = 0
    out = []
    for _ in range(n_ents):
        cnt, = struct.unpack_from("<i", buf, q)
        q += 4
        elems = []
        for _ in range(cnt):
            ent, = struct.unpack_from("<i", buf, q)
            q += 4
            enc = buf[q]
            q += 1
            lo = enc & 0x0F
            hi = (enc >> 4) & 0x0F
            if lo == 0:
                tx = 0.0
            elif lo == 1:
                tx = 1.0
            elif lo == 2:
                tx, = struct.unpack_from("<f", buf, q)
                q += 4
            else:
                tx = 0.0
            if hi == 0:
                ty = 0.0
            elif hi == 1:
                ty = 1.0
            elif hi == 2:
                ty, = struct.unpack_from("<f", buf, q)
                q += 4
            else:
                ty = 1.0
            flags = buf[q]
            q += 1
            elems.append((ent, tx, ty, flags))
        out.append(elems)
    assert q == len(buf), (q, len(buf))
    return out


class Save:
    def __init__(self, cok_path: str = None, blocks_dir: str = None):
        self.cok_path = cok_path
        self.metadata = {}
        self.map_prefab_name = ""
        if cok_path and os.path.exists(cok_path):
            self.metadata, self.map_prefab_name = self._read_meta(cok_path)
        if blocks_dir and os.path.exists(os.path.join(blocks_dir, "0000.bin")):
            files = sorted(glob.glob(os.path.join(blocks_dir, "*.bin")))
            self.blocks = [open(f, "rb").read() for f in files]
        else:
            assert cok_path, "Provide cok_path or blocks_dir"
            self.blocks = self._read_cok(cok_path)
        self._parse_tables()

    @staticmethod
    def _read_meta(path: str):
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            meta = {}
            for n in names:
                if n.endswith(".SaveGameMetadata"):
                    meta = json.loads(z.read(n).decode("utf-8-sig"))
                    break
            map_name = ""
            for n in names:
                if n.endswith(".Prefab"):
                    map_name = n[:-7].replace("%2E", ".")
                    break
            return meta, map_name

    @staticmethod
    def _read_cok(path: str):
        with zipfile.ZipFile(path, "r") as z:
            name = [n for n in z.namelist() if n.endswith(".SaveGameData")][0]
            data = z.read(name)
        nt, = struct.unpack_from("<H", data, 18)
        p = 20
        for _ in range(nt):
            L, = struct.unpack_from("<I", data, p)
            p += 4 + L
        blocks = []
        N = len(data)
        while p < N:
            us, cs = struct.unpack_from("<II", data, p)
            blocks.append(b"" if cs == 0 else zstd.decompress(data[p + 8 : p + 8 + cs]))
            p += 8 + cs
        return blocks

    def _parse_tables(self):
        b = self.blocks[0]
        p = 4
        self.types = []
        for _ in range(struct.unpack_from("<I", b, 0)[0]):
            esz, = struct.unpack_from("<I", b, p)
            fl = b[p + 4]
            sl, = struct.unpack_from("<I", b, p + 5)
            self.types.append((b[p + 9 : p + 9 + sl].decode().split(",")[0], fl))
            p += 4 + esz
        self.tidx = {n: i for i, (n, _f) in enumerate(self.types)}

        b = self.blocks[1]
        p = 4
        self.systems = []
        for _ in range(struct.unpack_from("<I", b, 0)[0]):
            esz, sl = struct.unpack_from("<II", b, p)
            self.systems.append(b[p + 8 : p + 8 + sl].decode().split(",")[0])
            p += 4 + esz

        b = self.blocks[2]
        u = struct.unpack_from(f"<{len(b) // 4}I", b)
        p = 1
        self.archs = []
        base = 0
        for i in range(u[0]):
            a, cnt, nc = u[p : p + 3]
            comps = list(u[p + 3 : p + 3 + nc])
            p += 3 + nc
            self.archs.append({"i": i, "count": cnt, "base": base, "comps": comps, "a": a})
            base += cnt
        self.n_entities = base
        self._bases = [a["base"] for a in self.archs]
        self._seg_cache = {}

    def system_block(self, name: str):
        if name not in self.systems:
            return None
        return self.blocks[3 + len(self.archs) + self.systems.index(name)]

    def segments(self, ai: int) -> dict:
        if ai in self._seg_cache:
            return self._seg_cache[ai]
        ar = self.archs[ai]
        data = self.blocks[3 + ai]
        q = 0
        out = {}
        for c in ar["comps"]:
            n, fl = self.types[c]
            if (fl & 1) and not (fl & 16):
                continue
            L, = struct.unpack_from("<I", data, q)
            out[n] = data[q + 4 : q + 4 + L]
            q += 4 + L
        assert q == len(data), (ai, q, len(data))
        self._seg_cache[ai] = out
        return out

    def archs_with(self, *names, without=()):
        if any(n not in self.tidx for n in names):
            return []
        ids = [self.tidx[n] for n in names]
        ex = [self.tidx[n] for n in without if n in self.tidx]
        return [
            a
            for a in self.archs
            if a["count"] > 0 and all(i in a["comps"] for i in ids) and not any(i in a["comps"] for i in ex)
        ]

    def has(self, ar: dict, name: str) -> bool:
        return self.tidx.get(name, -1) in ar["comps"]

    def locate(self, ent: int):
        i = bisect.bisect_right(self._bases, ent) - 1
        while i > 0 and self.archs[i]["count"] == 0:
            i -= 1
        return self.archs[i], ent - self.archs[i]["base"]

    def read_names(self) -> dict:
        """Parse Game.UI.NameSystem -> {entity_id: custom_name}."""
        b = self.system_block("Game.UI.NameSystem")
        if not b or len(b) < 8:
            return {}
        _L, n = struct.unpack_from("<II", b, 0)
        p = 8
        out = {}
        for _ in range(n):
            if p + 8 > len(b):
                break
            ent, sl = struct.unpack_from("<iI", b, p)
            p += 8
            s, p = read_utf8_chars(b, p, sl)
            if ent >= 0:
                out[ent] = s
        return out

    def read_prefabs(self):
        """Parse Game.Prefabs.PrefabSystem + Game.Prefabs.PrefabData.
        Returns (prefab_list, entity_to_prefab_tuple).
        """
        b = self.system_block("Game.Prefabs.PrefabSystem")
        n, = struct.unpack_from("<I", b, 4)
        p = 12
        pref = []
        for _ in range(n):
            l1, = struct.unpack_from("<I", b, p)
            t = b[p + 4 : p + 4 + l1].decode("utf-8", "replace")
            p += 4 + l1
            l2, = struct.unpack_from("<I", b, p)
            nm = b[p + 4 : p + 4 + l2].decode("utf-8", "replace")
            p += 4 + l2
            p += 16  # guid
            pref.append((t, nm))
        ent_map = {}
        for a in self.archs_with("Game.Prefabs.PrefabData"):
            seg = self.segments(a["i"])["Game.Prefabs.PrefabData"]
            for j in range(a["count"]):
                idx, = struct.unpack_from("<i", seg, j * 4)
                if 0 <= idx < len(pref):
                    ent_map[a["base"] + j] = pref[idx]
        return pref, ent_map
