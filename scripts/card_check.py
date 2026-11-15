#!/usr/bin/env python3
"""
Read-only check of a mounted Garmin data card against the layout GADM writes
(ARCHITECTURE.md, "Writing a Card the Way GADM Does").

    sudo python3 scripts/card_check.py /path/to/mounted/card [--vol-id XXXX-XXXX] [--fast]

Standard library only; nothing on the card is modified. The report is safe to
share: the volume serial and avionics System IDs are only ever shown as
match / mismatch / zero, never printed.

Checks:
  * files present, sizes, FAT attributes (hidden/system/read-only)
  * .evidf.dat against the card's volume serial
  * every feat_unlk.dat slot: block CRCs, security id, feature bit, volume
    serial, System ID consistency, and the signed CRC against the file on the card
  * every .sff license: size / CRC / SHA-256 / SHA-512 against the file it signs,
    product id and System ID against feat_unlk.dat
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

SLOT = 913
C1, C2 = 0x55, 0x338
SEC_ID_OFFSET = 191
FAT_IOCTL_GET_ATTRIBUTES = 0x80047210

# slot index -> (name, expected feature bit, candidate files)
SLOTS = {
    0: ("NAVIGATION", 0, ["ldr_sys/avtn_db.bin", "avtn_db.bin"]),
    2: ("TERRAIN", 3, ["terrain_9as.tdb", "trn.dat"]),
    3: ("OBSTACLE", 4, ["terrain.odb", "obstacle.odb"]),
    4: ("APT_TERRAIN", 5, ["terrain.adb"]),
    6: ("SAFETAXI", 7, ["safetaxi.bin", "safetaxi.img"]),
    7: ("FLITE_CHARTS", 8, ["fc_tpc/fc_tpc.dat", "fc_tpc.dat"]),
    8: ("BASEMAP", 10, ["bmap.bin"]),
    9: ("AIRPORT_DIR", 10, ["apt_dir.gca", "fbo.gpi"]),
    12: ("SECTIONALS", 10, ["rasters/rasters.xml", "rasters.xml"]),
    14: ("NAV_DB2", 10, ["ldr_sys/nav_db2.bin", "nav_db2.bin"]),
}
# What GADM leaves on a card (H=hidden S=system R=read-only)
DATA_ATTRS, META_ATTRS, PLAIN = "HSR", "HR", ""

problems: list[str] = []


def bad(msg: str) -> str:
    problems.append(msg)
    return f"  !! {msg}"


def crc(data: bytes, value: int = 0xFFFFFFFF) -> int:
    """feat_unlk CRC: reflected CRC-32 register without the final inversion."""
    return (~zlib.crc32(data, (~value) & 0xFFFFFFFF)) & 0xFFFFFFFF


def encode_vol(v: int) -> int:
    return (~(((v << 31) & 0xFFFFFFFF) | (v >> 1))) & 0xFFFFFFFF


def trunc_sysid(s: int) -> int:
    return ((s & 0xFFFFFFFF) + (s >> 32)) & 0xFFFFFFFF


def fat_attrs(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            buf = fcntl.ioctl(fd, FAT_IOCTL_GET_ATTRIBUTES, b"\0\0\0\0")
        finally:
            os.close(fd)
    except OSError:
        return None
    a = struct.unpack("<I", buf)[0]
    return ("H" if a & 2 else "") + ("S" if a & 4 else "") + ("R" if a & 1 else "")


def volume_serial(card: Path) -> int | None:
    try:
        dev = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "--target", str(card)],
                             capture_output=True, text=True, timeout=5).stdout.split()[0]
        uuid = subprocess.run(["blkid", "-s", "UUID", "-o", "value", dev],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        return int(uuid.replace("-", ""), 16)
    except Exception:
        return None


def digest_file(path: Path, fast: bool) -> dict:
    """Size, last 4 bytes, feat_unlk CRC over the whole file, zlib CRC, SHA-256/512."""
    out = {"size": path.stat().st_size}
    with open(path, "rb") as f:
        if out["size"] >= 4:
            f.seek(-4, 2)
            out["tail"] = struct.unpack("<I", f.read(4))[0]
            f.seek(0)
        out["preview"] = f.read(146)[129:146]
        f.seek(0)
        if fast:
            return out
        reg, z, h256, h512 = 0xFFFFFFFF, 0, hashlib.sha256(), hashlib.sha512()
        while chunk := f.read(1 << 20):
            reg = crc(chunk, reg)
            z = zlib.crc32(chunk, z)
            h256.update(chunk)
            h512.update(chunk)
    out.update(register=reg, zcrc=z, sha256=h256.digest(), sha512=h512.digest())
    return out


def der_items(data: bytes, pos: int = 0, end: int | None = None, depth: int = 0):
    """Yield (tag, value_bytes) for primitive DER items; stop at the first malformed one."""
    end = len(data) if end is None else end
    while pos + 2 <= end:
        tag, ln = data[pos], data[pos + 1]
        pos += 2
        if ln & 0x80:
            n = ln & 0x7F
            if n == 0 or n > 4 or pos + n > end:
                return
            ln = int.from_bytes(data[pos:pos + n], "big")
            pos += n
        if pos + ln > end:
            return
        if tag & 0x20:
            yield from der_items(data, pos, pos + ln, depth + 1)
        else:
            yield tag, data[pos:pos + ln]
        pos += ln
        if depth == 0:
            return  # one top-level SEQUENCE; the signature follows it


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("card", type=Path)
    ap.add_argument("--vol-id", help="volume serial (XXXX-XXXX) if it cannot be read from the device")
    ap.add_argument("--fast", action="store_true", help="skip whole-file CRC/hash checks")
    args = ap.parse_args()
    card: Path = args.card
    if not card.is_dir():
        print(f"{card}: not a directory")
        return 2

    digests: dict[Path, dict] = {}

    def dg(p: Path) -> dict:
        if p not in digests:
            digests[p] = digest_file(p, args.fast)
        return digests[p]

    # ---- volume serial / .evidf.dat ---------------------------------------
    vol = int(args.vol_id.replace("-", ""), 16) if args.vol_id else volume_serial(card)
    print("== volume serial ==")
    print("  read from device:", "yes" if vol is not None else "NO (pass --vol-id; run with sudo)")
    evidf = card / ".evidf.dat"
    enc_card = encode_vol(vol) if vol is not None else None
    if evidf.exists():
        raw = evidf.read_bytes()
        enc_evidf = struct.unpack("<I", raw[:4])[0] if len(raw) >= 4 else None
        print(f"  .evidf.dat: {len(raw)} bytes")
        if len(raw) != 4:
            print(bad(".evidf.dat is not 4 bytes"))
        elif enc_card is not None and enc_evidf != enc_card:
            print(bad(".evidf.dat does not match the card's volume serial"))
        elif enc_card is not None:
            print("  .evidf.dat matches the volume serial")
        if enc_card is None:
            enc_card = enc_evidf  # best reference available
    else:
        print(bad(".evidf.dat missing"))

    # ---- file listing ------------------------------------------------------
    print("\n== files (root, ldr_sys/, any .sff) ==")
    listed = sorted({p for p in card.iterdir() if p.is_file()}
                    | {p for p in (card / "ldr_sys").glob("*") if p.is_file()}
                    | set(card.rglob("*.sff")))
    slot_files = {f for _, _, fs in SLOTS.values() for f in fs}
    for p in listed:
        rel = p.relative_to(card).as_posix()
        attrs = fat_attrs(p)
        if rel in slot_files or rel == "feat_unlk.dat":
            want = DATA_ATTRS
        elif rel == ".gadm.meta":
            want = META_ATTRS
        elif rel == ".evidf.dat" or rel.endswith((".sff", ".zip")):
            want = PLAIN
        else:
            want = None
        line = f"  {p.stat().st_size:>13,}  [{'n/a' if attrs is None else attrs or '-':>3}]  {rel}"
        if attrs is not None and want is not None and attrs != want:
            line += f"   !! GADM leaves this [{want or '-'}]"
            problems.append(f"{rel}: attributes [{attrs or '-'}], GADM leaves [{want or '-'}]")
        print(line)
    if (card / "basemap.bin").exists():
        print(bad("stray basemap.bin (nav_db2 under the wrong name, left by tool 1.14/1.15)"))

    # ---- feat_unlk.dat -----------------------------------------------------
    print("\n== feat_unlk.dat ==")
    fu = card / "feat_unlk.dat"
    slot_sysids: dict[int, int] = {}
    slot_secids: dict[int, int] = {}
    if not fu.exists():
        print(bad("feat_unlk.dat missing"))
        blob = b""
    else:
        blob = fu.read_bytes()
        print(f"  size {len(blob)} = {len(blob) / SLOT:.2f} slots")
    for i in range(len(blob) // SLOT + (1 if len(blob) % SLOT else 0)):
        s = blob[i * SLOT:(i + 1) * SLOT]
        if not any(s):
            continue
        name, want_bit, files = SLOTS.get(i, (f"UNKNOWN", None, []))
        print(f"  slot {i} ({name}), offset {i * SLOT}")
        if len(s) < SLOT:
            print("  " + bad(f"slot {i} truncated ({len(s)} bytes)"))
            continue
        magic1, sec, magic2, bits, _res, enc = struct.unpack_from("<HHIIII", s, 0)
        off = 20
        if i == 0:
            magic3 = struct.unpack_from("<H", s, off)[0]
            off += 2
            if magic3 != 0x6501:
                print("  " + bad(f"slot 0 magic3 0x{magic3:04X}, expected 0x6501"))
        fcrc = struct.unpack_from("<I", s, off)[0]
        preview = s[off + 4:off + 21]
        chk1 = struct.unpack_from("<I", s, C1 - 4)[0]
        units, sysid = struct.unpack_from("<II", s, C1)
        chk2 = struct.unpack_from("<I", s, C1 + C2 - 4)[0]
        chk3 = struct.unpack_from("<I", s, SLOT - 4)[0]
        secid = (sec + SEC_ID_OFFSET) & 0xFFFF
        slot_sysids[i], slot_secids[i] = sysid, secid
        bitstr = ",".join(str(b) for b in range(32) if bits >> b & 1)
        print(f"    security id 0x{secid:04X}  feature bit(s) {bitstr}  unit_count {units}  file CRC 0x{fcrc:08X}")
        if magic1 != 1 or magic2 != 0x7648329A:
            print("  " + bad(f"slot {i}: bad magic"))
        if want_bit is not None and bits != 1 << want_bit:
            print("  " + bad(f"slot {i}: feature bits {bitstr}, tool writes bit {want_bit}"))
        ok1 = chk1 == crc(s[:C1 - 4])
        ok2 = chk2 == crc(s[C1:C1 + C2 - 4])
        ok3 = chk3 == crc(s[:SLOT - 4])
        print(f"    block CRCs: content1 {'ok' if ok1 else 'BAD'}, content2 {'ok' if ok2 else 'BAD'}, overall {'ok' if ok3 else 'BAD'}")
        if not (ok1 and ok2 and ok3):
            problems.append(f"slot {i} ({name}): block CRC invalid")
        if enc_card is not None:
            if enc == enc_card:
                print("    volume serial: matches card")
            else:
                print("  " + bad(f"slot {i} ({name}): signed for a different volume serial"))
        if sysid == 0:
            print("  " + bad(f"slot {i} ({name}): System ID is 0"))
        else:
            print("    System ID: set")
        present = [card / f for f in files if (card / f).exists()]
        if not present:
            print("  " + bad(f"slot {i} ({name}): no matching file on card ({', '.join(files) or '?'})"))
            continue
        f = present[0]
        d = dg(f)
        rel = f.relative_to(card).as_posix()
        if d.get("tail") == fcrc:
            print(f"    {rel}: trailing CRC matches slot")
        else:
            print("  " + bad(f"slot {i} ({name}): CRC 0x{fcrc:08X} != {rel} trailing CRC 0x{d.get('tail', 0):08X}"))
        if "register" in d:
            if d["register"] == 0:
                print(f"    {rel}: embedded checksum valid (file intact)")
            else:
                print("  " + bad(f"{rel}: embedded checksum INVALID (file corrupt or truncated)"))
        if i == 0 and preview != d["preview"]:
            print("  " + bad("slot 0: preview bytes differ from avtn_db.bin[129:146]"))
    ids = {v for v in slot_sysids.values() if v}
    if len(ids) > 1:
        groups = {}
        for k, v in slot_sysids.items():
            groups.setdefault(v, []).append(k)
        print("  System IDs differ between slots (normal if two avionics share the card): "
              + " | ".join(str(g) for g in groups.values()))
    for need in (0, 2, 14):
        if blob and need not in slot_sysids and any((card / f).exists() for f in SLOTS[need][2]):
            print(bad(f"slot {need} ({SLOTS[need][0]}) is empty but its file is on the card"))

    # ---- .sff licenses -----------------------------------------------------
    print("\n== .sff licenses ==")
    sffs = sorted(card.rglob("*.sff"))
    if not sffs:
        print(bad("no .sff license files on the card"))
    for sff in sffs:
        rel = sff.relative_to(card).as_posix()
        raw = sff.read_bytes()
        print(f"  {rel} ({len(raw)} bytes; GADM's are 1039)")
        hdr = raw[:128]
        text = [t.decode("ascii", "replace") for t in hdr[4:].split(b"\0") if t]
        print(f"    header: {' | '.join(text)}")
        if len(raw) < 200 or raw[0x80] != 0x30:
            print("  " + bad(f"{rel}: no DER structure at 0x80 (not a Garmin license?)"))
            continue
        items = list(der_items(raw, 0x80))
        ints = [int.from_bytes(v, "big") for t, v in items if t == 0x02]
        octs = [v for t, v in items if t == 0x04]
        # INTs in order: 1, 1, productID, systemID, 0, file size
        prod = ints[2] if len(ints) > 2 else None
        sysid = ints[3] if len(ints) > 3 else None
        size = ints[-1] if ints else None
        c4 = next((o for o in octs if len(o) == 4), None)
        s256 = next((o for o in octs if len(o) == 32), None)
        s512 = next((o for o in octs if len(o) == 64), None)
        print(f"    product id 0x{prod:04X}" if prod is not None else "    product id ?")
        target_name = sff.name[:-4]
        cands = [p for p in (sff.with_name(target_name), card / target_name, card / "ldr_sys" / target_name)
                 if p.exists()]
        slot = next((k for k, (_, _, fs) in SLOTS.items()
                     if any(Path(f).name == target_name for f in fs)), None)
        if slot is not None and slot in slot_sysids and sysid is not None:
            same = sysid == slot_sysids[slot] or trunc_sysid(sysid) == slot_sysids[slot]
            if same:
                print(f"    System ID: same unit as feat_unlk slot {slot}")
            else:
                print("  " + bad(f"{rel}: licensed to a different System ID than feat_unlk slot {slot}"))
            if prod is not None and prod != slot_secids[slot]:
                print("  " + bad(f"{rel}: product id 0x{prod:04X} != slot {slot} security id 0x{slot_secids[slot]:04X}"))
        elif slot is not None and slot not in slot_sysids:
            print("  " + bad(f"{rel}: feat_unlk slot {slot} is empty"))
        if not cands:
            print("  " + bad(f"{rel}: the file it licenses ({target_name}) is not on the card"))
            continue
        f = cands[0]
        d = dg(f)
        frel = f.relative_to(card).as_posix()
        if sff.parent != f.parent:
            print(f"    note: license is in {sff.parent.relative_to(card).as_posix() or '/'} but {frel} is elsewhere")
        if size == d["size"]:
            print(f"    {frel}: size matches license ({size:,})")
        else:
            print("  " + bad(f"{rel}: licensed size {size:,} != {frel} size {d['size']:,}"))
        if c4 is not None:
            lic = struct.unpack("<I", c4)[0]
            if lic in (d.get("tail"), d.get("zcrc")):
                print(f"    {frel}: CRC matches license")
            else:
                print("  " + bad(f"{rel}: licensed CRC 0x{lic:08X} != {frel} (trailing 0x{d.get('tail', 0):08X})"))
        if "sha256" in d:
            for label, lic_h, got in (("SHA-256", s256, d["sha256"]), ("SHA-512", s512, d["sha512"])):
                if lic_h is None:
                    print(f"    {label}: not found in license")
                elif lic_h == got:
                    print(f"    {frel}: {label} matches license")
                else:
                    print("  " + bad(f"{rel}: {label} of {frel} does NOT match the license"))
    for _, (name, _, files) in SLOTS.items():
        for fname in files:
            p = card / fname
            if name in ("NAVIGATION", "NAV_DB2") or fname == "trn.dat":
                if p.exists() and not any(s.name == p.name + ".sff" for s in sffs):
                    print(bad(f"{fname} has no {p.name}.sff license"))
    if not (card / "ldr_sys" / "grm_feat_key.zip").exists():
        print(bad("ldr_sys/grm_feat_key.zip missing"))

    meta = card / ".gadm.meta"
    if meta.exists():
        print("\n== .gadm.meta (database names/cycles only) ==")
        try:
            import json
            j = json.loads(meta.read_text(errors="replace"))
            def walk(o, path=""):
                if isinstance(o, dict):
                    for k, v in o.items():
                        walk(v, f"{path}.{k}" if path else k)
                elif isinstance(o, list):
                    for n, v in enumerate(o):
                        walk(v, f"{path}[{n}]")
                elif any(w in path.lower() for w in ("name", "cycle", "version", "issue", "installed", "type")) \
                        and not any(w in path.lower() for w in ("serial", "device", "system", "tail", "user", "account")):
                    print(f"  {path} = {o}")
            walk(j)
        except Exception as e:
            print(f"  (not JSON: {e})")

    print("\n== summary ==")
    if problems:
        for p in problems:
            print(f"  - {p}")
    else:
        print("  no differences from the GADM layout found")
    if args.fast:
        print("  (--fast: whole-file CRC/hash checks skipped)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
