#!/usr/bin/env python3
"""2 枚の画像が画素単位で同一かを判定する（再現性チェック用）。

  same_image.py A.png B.png      # 同一なら "identical" で exit 0、違えば理由を出して exit 1

サイズ・モードを比べたうえで、全チャンネル（RGBA は alpha も）のバイト列を比較する。
（ImageChops.difference().getbbox() は RGBA で alpha 差が 0 だと RGB の差を見逃すことがあるので使わない）
"""
import sys

from PIL import Image


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = (Image.open(p) for p in sys.argv[1:])
    if a.size != b.size:
        print(f"size differs: {a.size} != {b.size}")
        sys.exit(1)
    if a.mode != b.mode:
        print(f"mode differs: {a.mode} != {b.mode}")
        sys.exit(1)
    ba, bb = a.tobytes(), b.tobytes()
    if ba == bb:
        print("identical")
        return
    diff = sum(x != y for x, y in zip(ba, bb))
    print(f"differs: {diff} / {len(ba)} bytes")
    sys.exit(1)


if __name__ == "__main__":
    main()
