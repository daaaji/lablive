"""PyInstaller onedir exe의 PYZ 안 lablive.* 모듈만 새 소스로 교체 — Windows/wine 없이 Linux에서 설치본 갱신용.

사용: python3.12 repyz.py <소스 루트(lablive/ 가 있는 폴더)> <기존 exe> <새 exe>
  (PyInstaller 6.22.3 필요 — 기존 exe를 만든 버전과 같아야 함, Python 3.12 — 번들 파이썬과 바이트코드 버전 같아야 함)
조건: lablive에 새 모듈이 생기지 않았고, 새 외부 import가 없을 것 (스크립트가 확인). 그 외에는 build_windows.bat로 정식 빌드.
"""
import importlib.util, marshal, os, struct, sys, zlib
from PyInstaller.archive.writers import CArchiveWriter

src, exe_in, exe_out = sys.argv[1:4]
COOKIE = struct.Struct('!8sIIII64s')
data = open(exe_in, "rb").read()
magic, alen, toc_off, toc_len, pyvers, pylib = COOKIE.unpack(data[-COOKIE.size:])
pkg_start = len(data) - alen
pkg = data[pkg_start:]


def raw_toc(pkg_bytes, toc_offset, toc_length):
    """CArchive TOC를 원본 순서·중복 그대로 (CArchiveReader는 'o' 옵션 항목을 toc dict에서 빼므로 쓰지 않음 —
    'pyi-contents-directory _internal' 옵션이 빠지면 부트로더가 exe 옆에서 python312.dll을 찾다 실패)."""
    t = pkg_bytes[toc_offset:toc_offset + toc_length]
    out, i = [], 0
    while i < len(t):
        (elen,) = struct.unpack('!I', t[i:i + 4])
        off, clen, ulen, cflag, typ = struct.unpack('!IIIBc', t[i + 4:i + 18])
        name = t[i + 18:i + elen].rstrip(b'\0').decode('utf-8')
        out.append((name, off, clen, ulen, cflag, typ.decode()))
        i += elen
    return out


entries = raw_toc(pkg, toc_off, toc_len)   # (name, off, clen, ulen, cflag, typ)

def blob(e):
    return pkg[e[1]:e[1] + e[2]]

# ---- PYZ
pyz_e = [e for e in entries if e[5] == 'z']
assert len(pyz_e) == 1
pyz_raw = blob(pyz_e[0])
if pyz_e[0][4]:
    pyz_raw = zlib.decompress(pyz_raw)
assert pyz_raw[:4] == b'PYZ\0'
bc_magic = pyz_raw[4:8]
assert bc_magic == importlib.util.MAGIC_NUMBER, (bc_magic, importlib.util.MAGIC_NUMBER)
(toc_offset,) = struct.unpack('!i', pyz_raw[8:12])
ptoc = marshal.loads(pyz_raw[toc_offset:])
names = {n for n, _ in ptoc}
mods = {}
for root, _, files in os.walk(os.path.join(src, "lablive")):
    for f in files:
        if f.endswith(".py"):
            rel = os.path.relpath(os.path.join(root, f), src)
            mod = rel[:-3].replace(os.sep, ".")
            mod = mod[:-9] if mod.endswith(".__init__") else mod
            mods[mod] = rel
new_mods = set(mods) - names
missing = {n for n in names if n == "lablive" or n.startswith("lablive.")} - set(mods)
assert not missing, f"PYZ에 있는데 소스에 없음: {missing}"
# 이 exe가 원래 안 쓰던 lablive 모듈은 넣지 않음 (분석 결과 그대로)
out = bytearray(b'\0' * 17)
new_toc = []
replaced = []
for n, (tc, off, ln) in ptoc:
    if n in mods:
        path = os.path.join(src, mods[n])
        co = compile(open(path, encoding="utf-8").read(), mods[n].replace("/", "\\"), "exec", dont_inherit=True, optimize=0)
        b = zlib.compress(marshal.dumps(co), 6)
        replaced.append(n)
    else:
        b = pyz_raw[off:off + ln]
    new_toc.append((n, (tc, len(out), len(b))))
    out += b
toc_pos = len(out)
out += marshal.dumps(new_toc)
out[0:4] = b'PYZ\0'
out[4:8] = bc_magic
out[8:12] = struct.pack('!i', toc_pos)
new_pyz = bytes(out)

# ---- CArchive (같은 순서, PYZ만 교체)
pk = bytearray()
toc = []
for (name, off, clen, ulen, cflag, typ) in entries:
    if typ == 'z':
        b, ul, cf = new_pyz, len(new_pyz), 0
    else:
        b, ul, cf = blob((name, off, clen)), ulen, cflag
    toc.append((len(pk), len(b), ul, cf, typ, name))
    pk += b
toc_data = CArchiveWriter._serialize_toc(toc)
toc_off2 = len(pk)
pk += toc_data
alen2 = toc_off2 + len(toc_data) + COOKIE.size
pk += COOKIE.pack(magic, alen2, toc_off2, len(toc_data), pyvers, pylib)
# 교체한 모듈이 import하는 최상위 모듈이 번들 안에 있는지 (PYZ 또는 _internal/base_library.zip, 내장)
import dis, zipfile
base = os.path.join(os.path.dirname(exe_in), "_internal", "base_library.zip")
have = names | {os.path.splitext(x)[0].replace("/", ".").removesuffix(".__init__") for x in zipfile.ZipFile(base).namelist()}
have |= set(sys.builtin_module_names) | {"_io", "marshal", "nt", "winreg", "msvcrt", "_winapi", "_overlapped"}
def imports(co, pkg):
    out = set()
    ins = list(dis.get_instructions(co))
    for i, x in enumerate(ins):
        if x.opname == "IMPORT_NAME":
            lvl = ins[i - 2].argval if i >= 2 else 0
            frm = ins[i - 1].argval
            name = x.argval
            if lvl:
                base_pkg = pkg.rsplit(".", lvl - 1)[0] if lvl > 1 else pkg
                full = f"{base_pkg}.{name}" if name else base_pkg
                out.add(full)
                for f in (frm or ()):
                    out.add(f"{full}.{f}")
            else:
                out.add(name)
    for c in co.co_consts:
        if hasattr(c, "co_code"):
            out |= imports(c, pkg)
    return out
bad = set()
for n in replaced:
    co = compile(open(os.path.join(src, mods[n]), encoding="utf-8").read(), "x", "exec")
    pkg = n if mods[n].endswith("__init__.py") else n.rsplit(".", 1)[0]
    for m in imports(co, pkg):
        top_ok = any(m == h or m.startswith(h + ".") or h.startswith(m + ".") for h in have)
        if m.startswith("lablive") and m not in have and m.rsplit(".", 1)[0] in have:
            continue          # from .x import 이름 (모듈이 아니라 속성)
        if not top_ok and m.split(".")[0] not in ("tkinter",) and m not in have:
            bad.add((n, m))
print("import 확인:", "OK" if not bad else sorted(bad))
open(exe_out, "wb").write(data[:pkg_start] + bytes(pk))
# 검증: 새 exe의 TOC가 이름·순서·종류까지 원본과 같은지 (PYZ 크기만 다름)
nd = open(exe_out, "rb").read()
m2, al2, to2, tl2, pv2, pl2 = COOKIE.unpack(nd[-COOKIE.size:])
new_entries = raw_toc(nd[len(nd) - al2:], to2, tl2)
assert [(e[0], e[5]) for e in new_entries] == [(e[0], e[5]) for e in entries], "TOC 불일치"
assert (pv2, pl2) == (pyvers, pylib)
print(f"{os.path.basename(exe_out)}: TOC {len(new_entries)}개 항목 원본과 일치 ("
      + ", ".join(e[0] for e in new_entries if e[5] == 'o') + ")")
print(f"{os.path.basename(exe_out)}: 교체 {len(replaced)}개 모듈, PYZ {len(pyz_raw)}→{len(new_pyz)} B, pyvers {pyvers}")
