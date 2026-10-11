#!/usr/bin/env python3
"""Shortlist candidate moments in a long source (film, episode, podcast, stream).

  find_moments.py SOURCE --out DIR [--start S] [--end E] [--count 10] [--len 5] [--srt film.srt]

Scores every 0.25 s on motion, loudness and speech, then returns the best non-overlapping windows with
the film's own hard cuts listed, so you can keep every source range inside a single film shot.
With --srt each subtitle line becomes a candidate (strong short lines score higher) and its text is
reported, which is how you find "a strong line, action, or reaction" without listening.

Writes DIR/moments.json and DIR/moments_sheet.jpg (3 frames per moment). Look at the sheet before choosing:
the numbers find energy, they cannot judge who the character is or whether the shot is good.
"""
import argparse
import json
import textwrap
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (np, need_binaries, probe, read_gray_stream, decode_audio, frame_at, contact_sheet, fmt_t,
                     parse_srt)

BIN = 0.25  # seconds per scoring bin


def robust_z(x):
    x = np.asarray(x, dtype=np.float64)
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    return np.clip((x - med) / (mad + 1e-6 * (abs(med) + 1)), -3, 6)


def per_bin_features(path, start, dur):
    nb = int(dur / BIN)
    g = read_gray_stream(path, 64, 36, fps=1 / BIN, start=start, duration=dur).astype(np.float32)
    motion = np.zeros(nb)
    cut = np.zeros(nb, bool)
    if len(g) > 1:
        d = np.abs(g[1:] - g[:-1]).mean(axis=(1, 2))
        m = min(len(d), nb - 1)
        motion[1:m + 1] = d[:m]
        # at 4 fps a hard cut is a big step; real motion rarely exceeds ~25 grey levels at this scale
        cut[1:m + 1] = d[:m] > max(28.0, np.median(d) * 4 + 12)
    y = decode_audio(path, 16000, start=start, duration=dur)
    rms = np.full(nb, -90.0)
    speech = np.zeros(nb)
    if y is not None:
        n = 4000
        freqs = np.fft.rfftfreq(n, 1 / 16000)
        band = (freqs >= 300) & (freqs <= 3400)
        for i in range(min(nb, len(y) // n)):
            seg = y[i * n:(i + 1) * n]
            r = float(np.sqrt(np.mean(seg ** 2)) + 1e-9)
            rms[i] = 20 * np.log10(r)
            spec = np.abs(np.fft.rfft(seg * np.hanning(n))) ** 2
            speech[i] = spec[band].sum() / (spec.sum() + 1e-9)
        gate = rms > (np.median(rms) - 6)          # ignore near-silent bins for the speech ratio
        speech = speech * gate
    return motion, cut, rms, speech


def window_stats(s, motion, rms, speech, cut, i0, i1):
    sl = slice(i0, i1)
    return {
        "score": float(s[sl].mean() + 0.5 * s[sl].max() - 0.4 * cut[sl].sum()),
        "motion": float(motion[sl].mean()),
        "loudness_db": float(rms[sl].max()),
        "speech_ratio": float(speech[sl].mean()),
        "cuts_inside": int(cut[sl].sum()),
    }


def line_bonus(text):
    words = len(re.findall(r"\w+", text))
    b = 0.0
    if 3 <= words <= 14:
        b += 0.6
    elif words > 25:
        b -= 0.6
    if "!" in text or "?" in text:
        b += 0.3
    if re.search(r"\b(never|always|nothing|everything|kill|die|love|remember|promise|run|now)\b", text, re.I):
        b += 0.3
    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0, help="analyze from here (seconds) - narrow it for long films")
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--len", type=float, default=5.0, help="window length for non-subtitle candidates")
    ap.add_argument("--srt", help="subtitle file for the source (extract: ffmpeg -i film.mkv -map 0:s:0 film.srt)")
    ap.add_argument("--max-seconds", type=float, default=7200)
    args = ap.parse_args()

    need_binaries()
    os.makedirs(args.out, exist_ok=True)
    info = probe(args.source)
    end = min(args.end or info["duration"], info["duration"], args.start + args.max_seconds)
    dur = end - args.start
    motion, cut, rms, speech = per_bin_features(args.source, args.start, dur)
    nb = len(motion)
    s = 0.45 * robust_z(motion) + 0.35 * robust_z(rms) + 0.25 * robust_z(speech)
    s[cut] = np.minimum(s[cut], 0)                        # a cut spike is not "motion"
    cuts = [round(args.start + i * BIN, 2) for i in np.nonzero(cut)[0]]

    cands = []
    L = int(args.len / BIN)
    for i0 in range(0, max(1, nb - L), 2):                 # action / audio windows (always)
        st = window_stats(s, motion, rms, speech, cut, i0, i0 + L)
        cands.append({"start": round(args.start + i0 * BIN, 2), "end": round(args.start + (i0 + L) * BIN, 2),
                      "kind": "action/audio", **st})
    if args.srt:                                           # plus every subtitle line as its own candidate
        lines = [l for l in parse_srt(args.srt) if args.start <= l["start"] <= end]
        merged = []
        for l in lines:                                    # merge back-to-back lines of one exchange (<0.8 s gap)
            if merged and l["start"] - merged[-1]["end"] < 0.8 and l["end"] - merged[-1]["start"] < 9:
                merged[-1]["end"] = l["end"]
                merged[-1]["text"] += " / " + l["text"]
            else:
                merged.append(dict(l))
        for l in merged:
            a, b = max(args.start, l["start"] - 0.4), min(end, l["end"] + 0.7)
            i0 = int((a - args.start) / BIN)
            i1 = max(int((b - args.start) / BIN), i0 + 1)
            st = window_stats(s, motion, rms, speech, cut, i0, min(i1, nb))
            st["score"] += line_bonus(l["text"]) + 1.0     # a spoken line is worth more than a bare window
            cands.append({"start": round(a, 2), "end": round(b, 2), "kind": "line", "text": l["text"], **st})
    cands.sort(key=lambda c: -c["score"])
    picked = []
    for c in cands:
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            c["cuts_in_window"] = [t for t in cuts if c["start"] < t < c["end"]]
            picked.append(c)
        if len(picked) >= args.count:
            break
    for i, c in enumerate(picked, 1):
        c["id"] = i
        for k in ("score", "motion", "loudness_db", "speech_ratio"):
            c[k] = round(c[k], 2)

    imgs, labels = [], []
    for c in picked:
        span = c["end"] - c["start"]
        for f, tag in ((0.15, "in"), (0.5, "mid"), (0.85, "out")):
            imgs.append(frame_at(args.source, c["start"] + span * f, 200))
            lab = f"#{c['id']} {tag} {fmt_t(c['start'] + span * f)}"
            if tag == "in" and "text" in c:
                lab += "\n" + "\n".join(textwrap.wrap(c["text"], 26)[:2])
            labels.append(lab)
    sheet = os.path.join(args.out, "moments_sheet.jpg")
    if imgs:
        contact_sheet(imgs, labels, 6, sheet)
    res = {"source": os.path.abspath(args.source), "range": [args.start, end], "film_cuts": cuts, "moments": picked}
    with open(os.path.join(args.out, "moments.json"), "w") as fh:
        json.dump(res, fh, indent=1)

    print(f"{len(picked)} candidate moments in {fmt_t(args.start)}-{fmt_t(end)}   ({len(cuts)} hard cuts found in range)")
    for c in picked:
        extra = f'  "{c["text"][:70]}"' if "text" in c else ""
        print(f"#{c['id']:>2} {fmt_t(c['start'])}-{fmt_t(c['end'])}  score {c['score']:>5}  speech {c['speech_ratio']:.2f}  "
              f"motion {c['motion']:>5}  peak {c['loudness_db']:>6} dB  cuts {c['cuts_inside']}{extra}")
    print(f"\nsheet: {sheet}   <- open with the Read tool and judge the frames yourself")
    print(f"saved: {os.path.join(args.out, 'moments.json')}")


if __name__ == "__main__":
    main()
