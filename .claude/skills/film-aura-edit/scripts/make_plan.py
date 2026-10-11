#!/usr/bin/env python3
"""Turn (reference analysis + music analysis) into an edit-plan skeleton on the music's beat grid.

  make_plan.py --ref REF/analysis.json --music MUSIC/analysis.json --music-file track.mp3 \
               [--duration 28] [--drop-at SECONDS_IN_MUSIC] [--drop-pos 0.62] --out plan.json [--emit-spec spec.json]

What it does (this is the "reference sets pacing, shot rhythm and transitions; music sets the drop" step):
  1. picks the drop in the music and chooses where the music starts, so the drop lands at --drop-pos of the edit
  2. takes the reference's shot-length sequence as fractions of its total length, rescales it to --duration
  3. snaps every cut to the music's beat grid, forces a cut exactly on the drop and gives the payoff shot room
  4. copies the reference's transition style (hard cut / flash / dip) onto the matching positions
  5. labels every slot: hook, build, payoff, afterglow, outro

The output is a skeleton: you still choose WHICH moment of the film goes into each slot.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FALLBACK_BEATS = [1, 1, 1, 1, 2, 2, 2, 4, 4, 6]       # used (from the drop backwards) when there is no usable reference


def nearest_idx(vals, x):
    return min(range(len(vals)), key=lambda i: abs(vals[i] - x))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", help="analysis.json of the reference edit (optional; falls back to a generic rhythm)")
    ap.add_argument("--music", required=True, help="analysis.json of the music track")
    ap.add_argument("--music-file", required=True, help="path to the music file (stored in the plan/spec)")
    ap.add_argument("--duration", type=float, default=28.0, help="edit length in seconds (the article uses 25-30)")
    ap.add_argument("--drop-at", type=float, help="time of the drop inside the music file (default: strongest detected)")
    ap.add_argument("--drop-pos", type=float, help="where the drop lands in the edit, 0-1 (default: as in the reference, else 0.62)")
    ap.add_argument("--snap", type=float, choices=[0.5, 1.0, 2.0], help="cut grid in beats (default: auto)")
    ap.add_argument("--min-shot", type=float, default=0.4, help="shortest allowed shot in seconds")
    ap.add_argument("--out", required=True)
    ap.add_argument("--emit-spec", help="also write a build_edit.py spec skeleton here")
    ap.add_argument("--source", default="SOURCE_FILM.mp4", help="placeholder path used in the spec skeleton")
    args = ap.parse_args()

    music = json.load(open(args.music))
    mu = music.get("audio") or {}
    if not mu.get("bpm"):
        sys.exit("music analysis has no BPM - run analyze.py --role music on the track first")
    warnings = []
    bpm, spb = mu["bpm"], 60.0 / mu["bpm"]
    m_dur = mu.get("duration") or music["video"]["duration"]
    D = args.duration

    # ---- 1. drop and music start
    drops = mu.get("drops") or []
    strong = [d for d in drops if d["score"] >= 2.0]
    if args.drop_at is not None:
        drop_music = args.drop_at
    elif strong:
        drop_music = strong[0]["t"]
    else:
        drop_music = None
        warnings.append("no clear drop found in the music (energy never jumps); the payoff is placed on a beat at "
                        "--drop-pos. Pass --drop-at if you know where the drop is.")
    ref = json.load(open(args.ref)) if args.ref else None
    rp = (ref or {}).get("pacing") or {}
    ra = (ref or {}).get("audio") or {}
    if args.drop_pos is not None:
        drop_pos = args.drop_pos
    elif ra.get("main_drop_fraction") and (ra.get("drops") or [{}])[0].get("score", 0) >= 2.0:
        drop_pos = ra["main_drop_fraction"]
    else:
        drop_pos = 0.62
    drop_pos = min(max(drop_pos, 0.3), 0.85)
    t_drop = D * drop_pos
    if drop_music is None:
        drop_music = mu["beat_offset"] + spb * round((min(m_dur * 0.5, 20) - mu["beat_offset"]) / spb)
    m_start = drop_music - t_drop
    if m_start < 0:
        warnings.append(f"the drop is only {drop_music:.1f}s into the music, earlier than the planned {t_drop:.1f}s; "
                        "starting the music at 0 and moving the drop earlier in the edit.")
        m_start, t_drop = 0.0, drop_music
    if m_start + D > m_dur:
        new_start = max(0.0, m_dur - D)
        warnings.append(f"music is too short for {D:.0f}s from {m_start:.1f}s; shifted start to {new_start:.1f}s "
                        "(drop moves later in the edit). Trim D with --duration if that is wrong.")
        t_drop = drop_music - new_start
        m_start = new_start
        D = min(D, m_dur - m_start)
    beats_music = mu["beats"]
    grid = [b - m_start for b in beats_music if m_start <= b <= m_start + D + spb]
    if not grid:
        sys.exit("no beats inside the chosen window")
    # exact drop position on the grid
    gi = nearest_idx(grid, t_drop)
    if abs(grid[gi] - t_drop) < 0.4:
        t_drop = grid[gi]
    g0 = grid[0]

    # ---- 2. reference rhythm -> fractions
    lens = rp.get("shot_lengths") or []
    use_ref = len(lens) >= 4
    if use_ref:
        R = sum(lens)
        cuts = []
        acc = 0.0
        for l in lens[:-1]:
            acc += l
            cuts.append(acc / R * D)
        ref_cut_events = [c for c in rp.get("cuts", []) if c["type"] != "soft"]
        ref_cut_frac = [c["t"] / R for c in ref_cut_events]
        ref_cut_type = [c["type"] for c in ref_cut_events]
    else:
        warnings.append("no usable reference rhythm (fewer than 4 shots): using a generic rhythm that tightens "
                        "toward the drop.")
        cuts, t = [], t_drop
        for b in FALLBACK_BEATS:
            t -= b * spb
            if t <= 1.5:
                break
            cuts.append(t)
        cuts = sorted(cuts)
        t = t_drop
        for b in (4, 2, 2, 4):
            t += b * spb
            if t < D - 2 * spb:
                cuts.append(t)
        ref_cut_frac, ref_cut_type = [], []

    # ---- 3. snap to grid
    avg = (D / (len(cuts) + 1)) / spb                       # average shot length in beats
    snap = args.snap or (0.5 if avg < 1.6 else 1.0 if avg < 5 else 2.0)
    step = snap * spb

    def snap_t(x):
        return g0 + round((x - g0) / step) * step

    snapped = []
    for c in cuts:
        s = snap_t(c)
        if 0.2 < s < D - 0.2:
            snapped.append(s)
    snapped = sorted(set(round(x, 3) for x in snapped))
    # force a cut on the drop; remove a neighbour that would crowd the payoff
    if t_drop not in snapped:
        near = [i for i, c in enumerate(snapped) if abs(c - t_drop) <= 1.5 * spb]
        if near:
            i = min(near, key=lambda i: abs(snapped[i] - t_drop))
            snapped[i] = round(t_drop, 3)
        else:
            snapped.append(round(t_drop, 3))
        snapped = sorted(set(snapped))
    payoff_min = max(2 * spb, 1.2)
    snapped = [c for c in snapped if not (t_drop < c < t_drop + payoff_min)]
    # drop shots shorter than min-shot (keep the drop cut)
    cleaned, last = [], 0.0
    for c in snapped:
        if c - last >= args.min_shot or c == round(t_drop, 3):
            if c == round(t_drop, 3) and cleaned and c - cleaned[-1] < args.min_shot:
                cleaned.pop()
            cleaned.append(c)
            last = c
    if cleaned and D - cleaned[-1] < args.min_shot:
        cleaned.pop()
    # a hook shorter than 1.5 s has no time to stand on its own
    if cleaned and cleaned[0] < 1.5 and cleaned[0] != round(t_drop, 3):
        cleaned.pop(0)
    edges = [0.0] + cleaned + [round(D, 3)]

    # ---- 4. transitions + roles
    slots = []
    n = len(edges) - 1
    for i in range(n):
        a, b = edges[i], edges[i + 1]
        if i == 0:
            role, tr = "hook", "start"
        else:
            if abs(a - t_drop) < 1e-3:
                role = "payoff"
            elif i == n - 1:
                role = "outro"
            elif a > t_drop:
                role = "afterglow"
            else:
                role = "build"
            tr = "cut"
            if ref_cut_frac:
                j = nearest_idx(ref_cut_frac, a / D)
                if abs(ref_cut_frac[j] - a / D) < 0.05:
                    tr = ref_cut_type[j]
            if role == "payoff" and tr == "cut" and "flash" in (rp.get("transition_counts") or {}):
                tr = "flash"
        slots.append({"i": i, "start": round(a, 3), "end": round(b, 3), "dur": round(b - a, 3),
                      "beats": round((b - a) / spb, 2), "role": role, "transition_in": tr})
    if n < 6:
        warnings.append(f"only {n} shots - the reference may be too slow/long for this duration; check pacing numbers.")
    plan = {
        "duration": round(D, 3),
        "music": {"file": os.path.abspath(args.music_file), "start": round(m_start, 3), "bpm": bpm,
                  "drop_in_music": round(drop_music, 3), "drop_in_edit": round(t_drop, 3)},
        "grid": {"bpm": bpm, "offset": round(g0, 3), "snap_beats": snap},
        "reference": {"file": (ref or {}).get("file"), "shots": rp.get("shot_count"),
                      "mean_shot_len": (rp.get("shot_len") or {}).get("mean"), "trend": rp.get("rhythm_trend"),
                      "transitions": rp.get("transition_counts"), "used": use_ref},
        "slots": slots,
        "warnings": warnings,
    }
    with open(args.out, "w") as fh:
        json.dump(plan, fh, indent=1)

    print(f"edit {D:.1f}s | music from {m_start:.2f}s | {bpm} BPM | drop at {t_drop:.2f}s in the edit "
          f"({drop_pos:.0%}) | cut grid {snap} beat | reference {'used' if use_ref else 'NOT used (generic rhythm)'}")
    print(f"{'#':>2} {'start':>6} {'end':>6} {'dur':>5} {'beats':>5}  {'role':<10} transition")
    for s in slots:
        print(f"{s['i']:>2} {s['start']:>6.2f} {s['end']:>6.2f} {s['dur']:>5.2f} {s['beats']:>5}  {s['role']:<10} {s['transition_in']}")
    for w in warnings:
        print("WARNING:", w)
    print("saved:", args.out)

    if args.emit_spec:
        shots = []
        for s in slots:
            shot = {"at": s["start"], "src": args.source, "start": None, "speed": 1.0,
                    "role": s["role"], "transition_in": s["transition_in"], "pan": [0.5, 0.5], "dialogue": False}
            if s["transition_in"] == "flash":
                shot["flash"] = 0.12
            if s["role"] == "payoff":
                shot["zoom"] = [1.0, 1.1]
                shot["punch"] = [{"at": 0.0, "amount": 0.07, "decay": 0.3}]
            shots.append(shot)
        spec = {
            "output": {"path": "film_aura_edit.mp4", "width": 1080, "height": 1920, "fps": 30, "duration": plan["duration"]},
            "reframe": "cover",
            "look": {"preset": "natural"},
            "shots": shots,
            "text": [],
            "audio": {"music": plan["music"]["file"], "music_start": plan["music"]["start"], "fade_out": 1.2,
                      "duck_db": -14, "source_gain_db": -18},
            "grid": plan["grid"],
        }
        with open(args.emit_spec, "w") as fh:
            json.dump(spec, fh, indent=1)
        print("spec skeleton:", args.emit_spec, "(fill in src/start/pan per shot, mark dialogue shots, add text)")


if __name__ == "__main__":
    main()
