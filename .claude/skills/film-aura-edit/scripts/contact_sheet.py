#!/usr/bin/env python3
"""Make a contact sheet of a video so you can SEE it (open the JPG with the Read tool).

  contact_sheet.py VIDEO --out sheet.jpg [--count 24 | --step 0.5 --start 10 --end 14 | --times 1.0,2.5,9]
                         [--cols 8] [--width 200] [--safe]

--safe draws the 9:16 platform safe area (top/bottom UI margins) so you can check text placement.
Use --step 0.1 around a speed ramp or a transition to check it frame by frame.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import need_binaries, probe, frame_at, contact_sheet, fmt_t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--step", type=float)
    ap.add_argument("--times", help="comma separated seconds")
    ap.add_argument("--cols", type=int)
    ap.add_argument("--width", type=int, default=200)
    ap.add_argument("--safe", action="store_true", help="overlay the 9:16 safe area")
    args = ap.parse_args()
    need_binaries()
    info = probe(args.video)
    end = min(args.end or info["duration"], info["duration"])
    if args.times:
        times = [float(x) for x in args.times.split(",")]
    elif args.step:
        n = int((end - args.start) / args.step) + 1
        times = [args.start + i * args.step for i in range(min(n, 60))]
    else:
        n = max(1, args.count)
        times = [args.start + (i + 0.5) * (end - args.start) / n for i in range(n)]
    imgs = [frame_at(args.video, min(t, info["duration"] - 0.05), args.width) for t in times]
    if args.safe:
        from PIL import ImageDraw
        for im in imgs:
            if im is None:
                continue
            d = ImageDraw.Draw(im)
            w, h = im.size
            d.rectangle([w * 0.08, h * 0.14, w * 0.92, h * 0.76], outline=(0, 255, 120))
    cols = args.cols or (8 if info["aspect"] < 1 else 4)
    contact_sheet(imgs, [fmt_t(t) for t in times], cols, args.out)
    print(f"{len(imgs)} frames -> {args.out}")


if __name__ == "__main__":
    main()
