"""检查字体文件的族名、字重，诊断 Qt 加载与 QSS 匹配问题。"""
import struct
import os
import sys

FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")

WEIGHT_MAP = {
    100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Normal",
    500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
}


def parse_ttf(path):
    with open(path, "rb") as f:
        data = f.read()

    version, num_tables = struct.unpack_from(">IH", data, 0)
    names = {}
    weight_class = None

    for i in range(num_tables):
        off = 12 + i * 16
        tag = data[off:off + 4].decode("ascii", errors="replace")
        table_offset = struct.unpack_from(">I", data, off + 8)[0]

        if tag == "name":
            fmt, count, string_offset = struct.unpack_from(">HHH", data, table_offset)
            for j in range(count):
                rec = table_offset + 6 + j * 12
                pid, eid, lid, nid, length, soff = struct.unpack_from(">HHHHHH", data, rec)
                actual = table_offset + string_offset + soff
                raw = data[actual:actual + length]
                if pid in (0, 3):
                    try:
                        s = raw.decode("utf-16-be")
                    except Exception:
                        s = repr(raw)
                elif pid == 1:
                    try:
                        s = raw.decode("mac-roman")
                    except Exception:
                        s = repr(raw)
                else:
                    s = repr(raw)
                if nid not in names:
                    names[nid] = s

        elif tag == "OS/2":
            weight_class = struct.unpack_from(">H", data, table_offset + 4)[0]

    return names, weight_class


def main():
    files = sorted(f for f in os.listdir(FONT_DIR) if f.endswith((".ttf", ".otf")))
    print(f"扫描目录: {FONT_DIR}\n")

    for fname in files:
        path = os.path.join(FONT_DIR, fname)
        names, weight = parse_ttf(path)

        print(f"=== {fname} ===")
        print(f"  NameID1  (Family):     {names.get(1, 'N/A')}")
        print(f"  NameID2  (Subfamily):  {names.get(2, 'N/A')}")
        print(f"  NameID4  (Full):       {names.get(4, 'N/A')}")
        print(f"  NameID16 (TypoFamily): {names.get(16, 'N/A')}")
        print(f"  NameID17 (TypoSub):    {names.get(17, 'N/A')}")
        if weight:
            print(f"  OS/2 weight class:     {weight} ({WEIGHT_MAP.get(weight, '?')})")

        # 判断 Qt 加载后的族名
        family = names.get(16) or names.get(1) or "?"
        print(f"  -> Qt 注册族名(优先Typo): {family}")
        print()

    # 汇总 QSS 匹配分析
    print("=" * 60)
    print("QSS 字体匹配分析")
    print("=" * 60)
    qss_families = ["HarmonyOS Sans SC", "HarmonyOS Sans"]
    for qf in qss_families:
        found = False
        for fname in files:
            path = os.path.join(FONT_DIR, fname)
            names, _ = parse_ttf(path)
            fam = names.get(16) or names.get(1) or ""
            if fam == qf:
                found = True
                print(f"  QSS '{qf}' -> 匹配文件: {fname}")
                break
        if not found:
            print(f"  QSS '{qf}' -> 未找到精确匹配！将回退系统字体")

    print()
    print("字重覆盖分析 (QSS 使用了 500/600/700/800):")
    weights_found = {}
    for fname in files:
        path = os.path.join(FONT_DIR, fname)
        names, weight = parse_ttf(path)
        fam = names.get(16) or names.get(1) or "?"
        if "SC" in fam and weight:
            weights_found.setdefault(fam, []).append((weight, fname))
    for fam, wlist in sorted(weights_found.items()):
        print(f"  {fam}: {sorted(w for w, _ in wlist)}")


if __name__ == "__main__":
    main()
