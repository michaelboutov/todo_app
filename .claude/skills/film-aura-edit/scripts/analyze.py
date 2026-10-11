#!/usr/bin/env python3
"""Analyze a reference edit, a music track, a source film clip, or a finished render.

  analyze.py INPUT --out DIR [--role reference|music|source|output] [--compare REF_ANALYSIS.json]

Writes DIR/analysis.json (+ DIR/contact_sheet.jpg for video) and prints a short report.

What it measures (all heuristics - treat numbers as evidence, then look at the contact sheet):
  pacing       cut times, shot-length stats, cut density curve, hook/outro length, acceleration
  transitions  per cut: hard cut / white flash / dip to black (+ "soft" gradual changes, flagged uncertain)
  audio        tempo (BPM), beat grid (offset), drop candidates, loudness, how many cuts land on a beat
  look         luma/contrast/saturation/warmth/grain/vignette/letterbox (only used when matching a look)
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (np, need_binaries, probe, aspect_label, read_frames, read_gray_stream, decode_audio,
                     frame_at, contact_sheet, fmt_t)

SR = 22050
HOP = 256
NFFT = 1024
ONSET_LATENCY = 0.03  # s: STFT window latency, calibrated on click tracks with known beat times


# ----------------------------------------------------------------------------- audio
def stft_bands(y):
    """Spectral-flux onset envelope plus bass and full-band energy, all at hop rate."""
    win = np.hanning(NFFT).astype(np.float32)
    n = 1 + (len(y) - NFFT) // HOP
    if n < 16:
        return None
    flux = np.zeros(n, np.float32)
    flux_low = np.zeros(n, np.float32)
    bass = np.zeros(n, np.float32)
    full = np.zeros(n, np.float32)
    bass_bins = max(2, int(150 / (SR / NFFT)))
    low_bins = max(3, int(220 / (SR / NFFT)))
    prev = None
    CH = 2048
    for s in range(0, n, CH):
        e = min(n, s + CH)
        idx = np.arange(NFFT)[None, :] + HOP * np.arange(s, e)[:, None]
        S = np.abs(np.fft.rfft(y[idx] * win, axis=1)).astype(np.float32)
        L = np.log1p(10 * S)
        stack = np.vstack([prev if prev is not None else L[:1], L])
        dpos = np.maximum(0, np.diff(stack, axis=0))
        flux[s:e] = dpos.sum(axis=1)
        flux_low[s:e] = dpos[:, :low_bins].sum(axis=1)
        prev = L[-1:]
        bass[s:e] = np.sqrt((S[:, :bass_bins] ** 2).sum(axis=1))
        full[s:e] = np.sqrt((S ** 2).sum(axis=1))
    return flux, flux_low, bass, full


def smooth(x, k):
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def estimate_tempo(flux, fr, lo=55.0, hi=200.0):
    e = flux - smooth(flux, int(fr))          # remove slow trend
    e = np.maximum(e, 0)
    n = len(e)
    ac = np.fft.irfft(np.abs(np.fft.rfft(e, 2 * n)) ** 2)[:n]
    best, best_s = None, -1
    for lag in range(int(fr * 60 / hi), int(fr * 60 / lo) + 1):
        if lag >= n // 2:
            break
        bpm = 60.0 * fr / lag
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        s = ac[lag] * prior
        if s > best_s:
            best, best_s = bpm, s
    return best


def refine_grid(flux, fr, bpm0, dur):
    """Search tempo +-3% and phase for the beat grid that sits on the strongest onsets."""
    e = smooth(flux, 3)
    t = np.arange(len(e)) / fr + ONSET_LATENCY
    best = (-1, bpm0, 0.0)
    for bpm in np.arange(bpm0 * 0.97, bpm0 * 1.03, bpm0 * 0.0008):
        T = 60.0 / bpm
        for ph in np.linspace(0, T, 40, endpoint=False):
            times = np.arange(ph, dur, T)
            if len(times) < 4:
                continue
            sc = np.interp(times, t, e).mean()
            if sc > best[0]:
                best = (sc, bpm, ph)
    sc, bpm, ph = best
    T = 60.0 / bpm
    for ph2 in np.linspace(ph - T / 40, ph + T / 40, 21):
        times = np.arange(ph2 % T, dur, T)
        s2 = np.interp(times, t, e).mean()
        if s2 > sc:
            sc, ph = s2, ph2 % T
    conf = sc / (e.mean() + 1e-9)
    return bpm, ph % T, conf


def find_drops(full, bass, fr, beat_t, bpm, top=3):
    """Drop = biggest sustained jump in energy (full band + bass) between the 3 s before and the 3 s after."""
    per = max(1, int(fr * 0.1))
    n = len(full) // per
    if n < 40:
        return []
    F = full[: n * per].reshape(n, per).mean(1)
    B = bass[: n * per].reshape(n, per).mean(1)
    cF = np.concatenate([[0], np.cumsum(F)])
    cB = np.concatenate([[0], np.cumsum(B)])
    w = 30
    out = []
    for i in range(8, n - 8):
        a, b = max(0, i - w), min(n, i + w)
        bf = (cF[i] - cF[a]) / (i - a)
        af = (cF[b] - cF[i]) / (b - i)
        bb = (cB[i] - cB[a]) / (i - a)
        ab = (cB[b] - cB[i]) / (b - i)
        s = 0.5 * min(af / (bf + 1e-6) - 1, 6) + 0.5 * min(ab / (bb + 1e-6) - 1, 6)
        out.append((s, i * per / fr + NFFT / 2 / SR))
    out.sort(reverse=True)
    picked = []
    for s, t in out:
        if s < 0.25:
            break
        if all(abs(t - p["t"]) > 4.0 for p in picked):
            if beat_t is not None and bpm:
                T = 60.0 / bpm
                k = round((t - beat_t) / T)
                tb = beat_t + k * T
                if abs(tb - t) < 0.35:
                    t = tb
            picked.append({"t": round(float(t), 3), "score": round(float(s), 2)})
        if len(picked) >= top:
            break
    return picked


def analyze_audio(path, dur, cuts=None):
    y = decode_audio(path, SR)
    if y is None:
        return {"present": False}
    res = {"present": True}
    rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)) + 1e-12)
    res["loudness_dbfs_rms"] = round(20 * math.log10(rms), 1)
    res["peak_dbfs"] = round(20 * math.log10(float(np.max(np.abs(y))) + 1e-12), 1)
    bands = stft_bands(y)
    if bands is None:
        return res
    flux, flux_low, bass, full = bands
    fr = SR / HOP
    # kick-weighted onset envelope: off-beat hats must not win the phase over the kick/bass
    onset = flux / (flux.std() + 1e-9) * 0.4 + flux_low / (flux_low.std() + 1e-9) * 0.6
    a_dur = len(y) / SR
    bpm0 = estimate_tempo(onset, fr)
    if bpm0:
        bpm, ph, conf = refine_grid(onset, fr, bpm0, a_dur)
        T = 60.0 / bpm
        beats = [round(float(x), 3) for x in np.arange(ph, a_dur, T)]
        res.update(bpm=round(float(bpm), 2), bpm_alternatives=[round(float(bpm) / 2, 2), round(float(bpm) * 2, 2)],
                   beat_offset=round(float(ph), 3), beat_confidence=round(float(conf), 2), beats=beats)
        res["drops"] = find_drops(full, bass, fr, ph, bpm)
        if cuts:
            tol = 0.08
            near = [min(abs(c - b) for b in beats) for c in cuts]
            res["cut_on_beat_ratio"] = round(sum(d <= tol for d in near) / len(near), 2)
            res["cut_on_beat_chance"] = round(min(1.0, 2 * tol / T), 2)
    else:
        res["drops"] = []
    # coarse energy curve (1 value / second, dB) so the report can describe build and drop
    per = int(fr)
    n = len(full) // per
    if n:
        curve = full[: n * per].reshape(n, per).mean(1)
        res["energy_db_per_s"] = [round(20 * math.log10(float(v) + 1e-6), 1) for v in curve]
    res["duration"] = round(a_dur, 3)
    return res


# ----------------------------------------------------------------------------- pacing / transitions
def analyze_pacing(path, info, max_seconds):
    dur = min(info["duration"], max_seconds)
    fps = min(info["fps"] or 30, 30)
    w = 64
    h = max(8, int(round(w / info["aspect"] / 2) * 2))
    g = read_gray_stream(path, w, h, fps=fps, duration=dur).astype(np.float32)
    n = len(g)
    if n < 4:
        return {"cuts": [], "shot_lengths": [dur]}
    d = np.abs(g[1:] - g[:-1]).mean(axis=(1, 2))           # d[i]: change between frame i and i+1
    lum = g.mean(axis=(1, 2))
    cand = []
    for i in range(len(d)):
        lo, hi = max(0, i - 8), min(len(d), i + 9)
        nb = np.concatenate([d[lo:i], d[i + 1:hi]])
        base = float(np.median(nb)) if len(nb) else 0.0
        if d[i] > max(12.0, 4.0 * base + 6.0):
            cand.append(i)
    cuts_i = []
    for i in cand:                                          # keep the strongest of any run within 3 frames
        if cuts_i and i - cuts_i[-1] <= 3:
            if d[i] > d[cuts_i[-1]]:
                cuts_i[-1] = i
        else:
            cuts_i.append(i)
    cuts = []
    for i in cuts_i:
        a, b = max(0, i - 8), min(n, i + 9)
        pre = float(np.median(lum[max(0, i - 8):max(1, i - 2)]))
        post = float(np.median(lum[min(n - 1, i + 3):b])) if i + 3 < n else pre
        win = lum[max(0, i - 3):min(n, i + 5)]
        kind = "cut"
        if win.max() - max(pre, post) > 25:
            kind = "flash"
        elif min(pre, post) - win.min() > 25 and win.min() < 45:
            kind = "dip_black"
        t_cut = round((i + 1) / fps, 3)
        if cuts and t_cut - cuts[-1]["t"] < 0.25:           # same event (e.g. flash fade-out) -> keep the first
            if kind != "cut" and cuts[-1]["type"] == "cut":
                cuts[-1]["type"] = kind
            continue
        cuts.append({"t": t_cut, "type": kind, "strength": round(float(d[i]), 1)})
    # gradual transitions (crossfade-like) - uncertain: fast pans look the same, so flagged as "soft"
    wdw = max(4, int(0.25 * fps))
    hard = {c["t"] for c in cuts}
    soft, i = [], 0
    while i < n - wdw - 1:
        seg = d[i:i + wdw]
        D = float(np.abs(g[i + wdw] - g[i]).mean())
        t0 = (i + 1) / fps
        near_cut = any(abs(t0 - h_) < 0.5 for h_ in hard)
        if (D > 25 and not near_cut and seg.max() < 0.5 * D and seg.std() / (seg.mean() + 1e-6) < 0.6):
            soft.append({"t": round(t0 + wdw / fps / 2, 3), "type": "soft", "strength": round(D, 1)})
            i += wdw * 2
        else:
            i += 1
    cuts_all = sorted(cuts + soft, key=lambda c: c["t"])
    hard_t = [c["t"] for c in cuts]
    edges = [0.0] + hard_t + [dur]
    lens = [round(b - a, 3) for a, b in zip(edges[:-1], edges[1:]) if b - a > 0.05]
    res = {
        "analyzed_seconds": round(dur, 2),
        "cuts": cuts_all,
        "shot_count": len(lens),
        "shot_lengths": lens,
    }
    if lens:
        arr = np.array(lens)
        res["shot_len"] = {"mean": round(float(arr.mean()), 2), "median": round(float(np.median(arr)), 2),
                           "min": round(float(arr.min()), 2), "max": round(float(arr.max()), 2)}
        res["hook_len"] = lens[0]
        res["outro_len"] = lens[-1]
        half = len(lens) // 2
        if half >= 2:
            first, second = float(np.mean(lens[:half])), float(np.mean(lens[half:]))
            res["rhythm_trend"] = ("accelerating" if second < 0.8 * first else
                                   "decelerating" if second > 1.25 * first else "steady")
            res["half_means"] = [round(first, 2), round(second, 2)]
    bins = np.zeros(int(math.ceil(dur / 2.0)) + 1, int)
    for t in hard_t:
        bins[int(t // 2)] += 1
    res["cuts_per_2s"] = bins.tolist()
    types = [c["type"] for c in cuts_all]
    res["transition_counts"] = {k: types.count(k) for k in ("cut", "flash", "dip_black", "soft") if types.count(k)}
    return res


# ----------------------------------------------------------------------------- look
def analyze_look(path, info, dur):
    W = 320
    H = max(8, int(round(W / info["aspect"] / 2) * 2))
    fr, _ = read_frames(path, 24, W, H, duration=dur)
    f = fr.astype(np.float32)
    Y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    rowmax = Y.max(axis=(0, 2))
    colmax = Y.max(axis=(0, 1))
    top = bottom = left = right = 0
    while top < H // 2 and rowmax[top] < 12:
        top += 1
    while bottom < H // 2 and rowmax[H - 1 - bottom] < 12:
        bottom += 1
    while left < W // 2 and colmax[left] < 12:
        left += 1
    while right < W // 2 and colmax[W - 1 - right] < 12:
        right += 1
    bars = {"top": round(top / H, 3), "bottom": round(bottom / H, 3), "left": round(left / W, 3),
            "right": round(right / W, 3)}
    f = f[:, top:H - bottom, left:W - right]
    Y = Y[:, top:H - bottom, left:W - right]
    ih, iw = Y.shape[1], Y.shape[2]
    out = {"bars": bars}
    if (top + bottom) / H > 0.04:
        out["letterbox_inside_aspect"] = round(iw / ih, 3)
    p = np.percentile(Y, [1, 5, 50, 95, 99])
    out.update(luma_p1=round(float(p[0]), 1), luma_p5=round(float(p[1]), 1), luma_p50=round(float(p[2]), 1),
               luma_p95=round(float(p[3]), 1), luma_mean=round(float(Y.mean()), 1),
               contrast_range=round(float(p[3] - p[1]), 1))
    mx, mn = f.max(axis=-1), f.min(axis=-1)
    m = Y > 16
    out["saturation_mean"] = round(float(((mx - mn) / (mx + 1e-6))[m].mean()), 3) if m.any() else 0.0
    lo_m, hi_m = Y < np.percentile(Y, 25), Y > np.percentile(Y, 75)
    out["shadow_cool"] = round(float((f[..., 2][lo_m] - f[..., 0][lo_m]).mean()), 1)       # +blue / -red in shadows
    out["highlight_warm"] = round(float((f[..., 0][hi_m] - f[..., 2][hi_m]).mean()), 1)    # +red / -blue in highlights
    ch, cw = max(2, int(ih * 0.15)), max(2, int(iw * 0.15))
    corners = np.concatenate([Y[:, :ch, :cw].ravel(), Y[:, :ch, -cw:].ravel(), Y[:, -ch:, :cw].ravel(),
                              Y[:, -ch:, -cw:].ravel()])
    center = Y[:, ih // 2 - ih // 6: ih // 2 + ih // 6, iw // 2 - iw // 6: iw // 2 + iw // 6]
    out["corner_to_center"] = round(float(corners.mean() / (center.mean() + 1e-6)), 2)
    # grain: robust high-frequency residual on native-resolution centre crops
    try:
        cs = (min(256, info["width"] - 2) // 2 * 2, min(256, info["height"] - 2) // 2 * 2)
        nat, _ = read_frames(path, 6, cs[0], cs[1], crop=cs, duration=dur)
        L = (0.2126 * nat[..., 0] + 0.7152 * nat[..., 1] + 0.0722 * nat[..., 2]).astype(np.float32)
        pad = np.pad(L, ((0, 0), (1, 1), (1, 1)), mode="edge")
        blur = sum(pad[:, i:i + L.shape[1], j:j + L.shape[2]] for i in range(3) for j in range(3)) / 9.0
        resid = L - blur
        out["grain_sigma"] = round(float(1.4826 * np.median(np.abs(resid - np.median(resid)))), 2)
    except Exception:
        out["grain_sigma"] = None
    return out


def make_sheet(path, info, pacing, out_dir, max_frames=24):
    dur = pacing.get("analyzed_seconds", info["duration"])
    cuts = [c["t"] for c in pacing["cuts"] if c["type"] != "soft"]
    edges = [0.0] + cuts + [dur]
    mids = [((a + b) / 2, b - a) for a, b in zip(edges[:-1], edges[1:]) if b - a > 0.05]
    if len(mids) > max_frames:
        idx = np.linspace(0, len(mids) - 1, max_frames).round().astype(int)
        mids = [mids[i] for i in idx]
    tw = 200 if info["aspect"] < 1 else 280
    imgs = [frame_at(path, t, tw) for t, _ in mids]
    labels = [f"{fmt_t(t)}  ({l:.1f}s)" for t, l in mids]
    cols = 8 if info["aspect"] < 1 else 4
    out = os.path.join(out_dir, "contact_sheet.jpg")
    contact_sheet(imgs, labels, cols, out)
    return out


# ----------------------------------------------------------------------------- compare / report
def compare(cur, ref):
    rows = []

    def add(name, a, b, hint=""):
        if a is None or b is None:
            return
        rows.append((name, b, a, round(a - b, 3), hint))

    cp, rp = cur.get("pacing", {}), ref.get("pacing", {})
    if cp and rp:
        add("shot_count", cp.get("shot_count"), rp.get("shot_count"), "more/fewer cuts than reference")
        add("shot_len_mean", cp.get("shot_len", {}).get("mean"), rp.get("shot_len", {}).get("mean"),
            "output cuts slower than reference" if cp.get("shot_len", {}).get("mean", 0) > rp.get("shot_len", {}).get("mean", 0)
            else "output cuts faster than reference")
        add("hook_len", cp.get("hook_len"), rp.get("hook_len"))
        add("outro_len", cp.get("outro_len"), rp.get("outro_len"))
    ca, ra = cur.get("audio", {}), ref.get("audio", {})
    add("cut_on_beat_ratio", ca.get("cut_on_beat_ratio"), ra.get("cut_on_beat_ratio"),
        "reference is beat-locked; output should be too" if (ra.get("cut_on_beat_ratio") or 0) > 0.6 else "")
    add("loudness_dbfs_rms", ca.get("loudness_dbfs_rms"), ra.get("loudness_dbfs_rms"), "raise/lower master gain")
    cl, rl = cur.get("look", {}), ref.get("look", {})
    for k, hint in (("luma_mean", "brightness / gamma"), ("contrast_range", "contrast / s_curve"),
                    ("saturation_mean", "look.saturation"), ("shadow_cool", "look.shadow_tint"),
                    ("highlight_warm", "look.highlight_tint"), ("grain_sigma", "look.grain"),
                    ("corner_to_center", "look.vignette"), ("luma_p1", "look.black_lift")):
        add(k, cl.get(k), rl.get(k), hint)
    return rows


def print_report(a, role):
    v, p, au, lk = a.get("video"), a.get("pacing"), a.get("audio"), a.get("look")
    print(f"\n=== {role.upper()} :: {os.path.basename(a['file'])} ===")
    if v:
        print(f"video   {v['width']}x{v['height']} ({aspect_label(v['aspect'])})  {v['fps']} fps  {v['duration']:.1f}s  "
              f"audio={'yes' if v['has_audio'] else 'no'}")
    if p and p.get("shot_lengths"):
        sl = p["shot_len"]
        print(f"pacing  {p['shot_count']} shots | mean {sl['mean']}s  median {sl['median']}s  min {sl['min']}s  max {sl['max']}s"
              f" | hook {p['hook_len']}s  outro {p['outro_len']}s | trend: {p.get('rhythm_trend', 'n/a')} {p.get('half_means', '')}")
        print(f"        transitions: {p['transition_counts'] or 'none detected'}")
        print(f"        cuts per 2s: {p['cuts_per_2s']}")
        print(f"        shot lengths: {p['shot_lengths']}")
    if au and au.get("present"):
        if au.get("bpm"):
            print(f"audio   {au['bpm']} BPM (alt {au['bpm_alternatives']}) beat_offset {au['beat_offset']}s "
                  f"confidence {au['beat_confidence']} | loudness {au['loudness_dbfs_rms']} dBFS rms, peak {au['peak_dbfs']}")
            print(f"        drops: {au['drops'] or 'none found'}")
            if "cut_on_beat_ratio" in au:
                print(f"        cuts on beat: {au['cut_on_beat_ratio']:.0%} (chance level {au['cut_on_beat_chance']:.0%})")
    elif au is not None:
        print("audio   none")
    if lk:
        print(f"look    luma mean {lk['luma_mean']} (p5 {lk['luma_p5']}, p95 {lk['luma_p95']})  sat {lk['saturation_mean']}  "
              f"shadow_cool {lk['shadow_cool']}  highlight_warm {lk['highlight_warm']}  grain {lk['grain_sigma']}  "
              f"corner/center {lk['corner_to_center']}  bars {lk['bars']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", required=True, help="directory for analysis.json and contact_sheet.jpg")
    ap.add_argument("--role", default="reference", choices=["reference", "music", "source", "output"])
    ap.add_argument("--max-seconds", type=float, default=120, help="analyze at most this much (cuts/look)")
    ap.add_argument("--compare", help="analysis.json of the reference to diff against (use on a render)")
    ap.add_argument("--no-sheet", action="store_true")
    args = ap.parse_args()

    need_binaries()
    os.makedirs(args.out, exist_ok=True)
    info = probe(args.input)
    a = {"file": os.path.abspath(args.input), "role": args.role}
    if info["has_video"]:
        a["video"] = {k: info[k] for k in ("width", "height", "fps", "duration", "aspect", "has_audio")}
        a["video"]["aspect_label"] = aspect_label(info["aspect"])
    cuts_for_beats = None
    if info["has_video"] and args.role != "music":
        a["pacing"] = analyze_pacing(args.input, info, args.max_seconds)
        cuts_for_beats = [c["t"] for c in a["pacing"]["cuts"] if c["type"] != "soft"]
    if info["has_audio"]:
        a["audio"] = analyze_audio(args.input, info["duration"], cuts_for_beats)
    elif args.role == "music":
        sys.exit("no audio stream found in the music file")
    if info["has_video"] and args.role in ("reference", "source", "output"):
        a["look"] = analyze_look(args.input, info, min(info["duration"], args.max_seconds))
        if not args.no_sheet:
            a["contact_sheet"] = make_sheet(args.input, info, a["pacing"], args.out)
    # position of the strongest drop inside the reference (for mapping its structure onto the new music)
    drops = (a.get("audio") or {}).get("drops") or []
    if drops and info.get("duration"):
        a["audio"]["main_drop_fraction"] = round(drops[0]["t"] / info["duration"], 3)
    with open(os.path.join(args.out, "analysis.json"), "w") as fh:
        json.dump(a, fh, indent=1)
    print_report(a, args.role)
    if a.get("contact_sheet"):
        print(f"\ncontact sheet: {a['contact_sheet']}  <- open it with the Read tool and look at the shots")
    if args.compare:
        ref = json.load(open(args.compare))
        rows = compare(a, ref)
        print(f"\n--- compare vs {os.path.basename(ref['file'])} (reference | this | delta) ---")
        for name, rv, cv, dv, hint in rows:
            print(f"{name:20s} {rv!s:>9} {cv!s:>9} {dv:+9.3f}  {hint}")
    print(f"\nsaved: {os.path.join(args.out, 'analysis.json')}")


if __name__ == "__main__":
    main()
