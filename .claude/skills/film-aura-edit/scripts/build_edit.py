#!/usr/bin/env python3
"""Render a film aura edit from a JSON spec with ffmpeg.

  build_edit.py SPEC.json [--preview] [--out PATH] [--workdir DIR] [--clean] [--dry-run]

--preview renders half size with fast settings - use it for the "preview" step, then render the final without it.
Per-shot clips are cached in the workdir, so re-running after changing only text / look / audio is quick.
Spec format: references/edit-spec.md. Everything on the timeline is in seconds of the FINAL edit.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import need_binaries, probe, run

PRESETS = {
    "none": {"contrast": 1.0, "saturation": 1.0, "black_lift": 0, "s_curve": 0, "shadow_tint": 0, "highlight_tint": 0,
             "bloom": 0, "grain": 0, "vignette": 0, "lut": None},
    # the film's own grade stays; just a touch of punch for small screens
    "natural": {"contrast": 1.04, "saturation": 1.05, "black_lift": 0, "s_curve": 0.1, "shadow_tint": 0,
                "highlight_tint": 0, "bloom": 0, "grain": 0, "vignette": 0.15, "lut": None},
    # optional: film-print feel (use when the reference or the user asks for it)
    "cinematic": {"contrast": 1.08, "saturation": 0.95, "black_lift": 0.02, "s_curve": 0.3, "shadow_tint": 0.4,
                  "highlight_tint": 0.4, "bloom": 0.3, "grain": 8, "vignette": 0.3, "lut": None},
}
DEFAULT_SAFE = {"top": 0.14, "bottom": 0.24, "side": 0.08}      # fractions of the 9:16 frame (conservative UI margins)


def esc(path):
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def ease(name, u):
    u = min(max(u, 0.0), 1.0)
    if name == "out":
        return 1 - (1 - u) ** 2
    if name == "in":
        return u * u
    if name == "linear":
        return u
    return u * u * (3 - 2 * u)


# ----------------------------------------------------------------------------- speed ramps
def speed_at(shot, t):
    ramp = shot.get("ramp")
    if not ramp:
        return float(shot.get("speed", 1.0))
    ks = sorted(ramp, key=lambda k: k[0])
    if t <= ks[0][0]:
        return float(ks[0][1])
    for (t0, s0), (t1, s1) in zip(ks[:-1], ks[1:]):
        if t0 <= t <= t1:
            return s0 + (s1 - s0) * ease(shot.get("ramp_ease", "smooth"), (t - t0) / max(t1 - t0, 1e-6))
    return float(ks[-1][1])


def speed_plan(shot, dur, step=0.1):
    """-> (source seconds consumed, setpts expression or None, min speed). Time is mapped piecewise-linearly."""
    if not shot.get("ramp"):
        s = float(shot.get("speed", 1.0))
        return dur * s, None, s
    knots = [(0.0, 0.0)]
    a = t = 0.0
    smin = 99.0
    while t < dur - 1e-9:
        dt = min(step, dur - t)
        s = max(speed_at(shot, t + dt / 2), 0.05)
        smin = min(smin, s)
        a += s * dt
        t += dt
        knots.append((a, t))
    expr = f"{knots[-1][1]:.5f}+(T-{knots[-1][0]:.5f})"
    for (a0, o0), (a1, o1) in reversed(list(zip(knots[:-1], knots[1:]))):
        slope = (o1 - o0) / max(a1 - a0, 1e-9)
        expr = f"if(lt(T,{a1:.5f}),{o0:.5f}+(T-{a0:.5f})*{slope:.5f},{expr})"
    return a, expr.replace("T", "(PTS*TB)"), smin


def atempo_chain(s):
    out = []
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    out.append(f"atempo={s:.5f}")
    return ",".join(out)


# ----------------------------------------------------------------------------- per-shot graph
def zoom_expr(shot, D, shake):
    z0, z1 = (shot.get("zoom") or [1.0, 1.0])
    e = shot.get("zoom_ease", "out")
    base = f"{z0}"
    if z1 != z0:
        u = f"min(max(t/{D:.4f},0),1)"
        shape = {"linear": u, "in": f"({u})*({u})", "out": f"(1-(1-{u})*(1-{u}))"}.get(e, f"({u})*({u})*(3-2*{u})")
        base = f"({z0}+({z1}-{z0})*{shape})"
    terms = "".join(f"+{p.get('amount', 0.06)}*exp(-(t-{p.get('at', 0)})/{p.get('decay', 0.25)})*gte(t,{p.get('at', 0)})"
                    for p in shot.get("punch", []))
    z = f"({base}{terms})"
    if shake:
        z = f"({z}*{1 + 2 * shake + 0.004:.4f})"
    return z, bool(z1 != z0 or terms or shake)


def shake_conf(shot):
    sh = shot.get("shake") or 0
    if isinstance(sh, dict):
        return float(sh.get("amount", 0.004)), float(sh.get("decay", 0))
    return float(sh), 0.0


def video_graph(shot, nf, F, TW, TH, reframe, speed_expr, smin, interp):
    D = nf / F
    g = "[0:v]setsar=1,setpts=PTS-STARTPTS"
    if speed_expr:
        g += f",setpts='({speed_expr})/TB'"
    elif float(shot.get("speed", 1.0)) != 1.0:
        g += f",setpts=PTS/{float(shot.get('speed', 1.0))}"
    if smin < 0.95 and interp != "none":
        g += f",minterpolate=fps={F}:mi_mode={'mci' if interp == 'mci' else 'blend'}"
    else:
        g += f",fps={F}"
    mode = shot.get("reframe", reframe)
    px0, py0 = shot.get("pan", [0.5, 0.5])
    px1, py1 = shot.get("pan_to", [px0, py0])
    amp, decay = shake_conf(shot)
    z, dynamic = zoom_expr(shot, D, amp)
    if mode == "blur":
        g += (f",split=2[bg][fg];[bg]scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH},"
              f"scale={TW // 10}:-2,gblur=sigma=5,scale={TW}:{TH},eq=brightness=-0.12:saturation=1.15[bgb];"
              f"[fg]scale={TW}:-2:force_divisible_by=2[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2:shortest=1")
        px0 = px1 = py0 = py1 = 0.5
    else:
        g += f",scale={TW}:{TH}:force_original_aspect_ratio=increase:force_divisible_by=2"
    u = f"min(max(t/{D:.4f},0),1)"
    px = f"({px0}+({px1}-{px0})*{u})"
    py = f"({py0}+({py1}-{py0})*{u})"
    shake_x = shake_y = ""
    if amp:
        A = amp * TW
        fade = f"*exp(-t/{decay})" if decay else ""
        shake_x = f"+{A:.2f}*(sin(t*53+1)+0.6*sin(t*97+2.3))/1.6{fade}"
        shake_y = f"+{A:.2f}*(sin(t*61+4)+0.6*sin(t*89+0.7))/1.6{fade}"
    if dynamic:
        g += f",scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':eval=frame"
    g += f",crop={TW}:{TH}:x='(iw-{TW})*{px}{shake_x}':y='(ih-{TH})*{py}{shake_y}'"
    if shot.get("rgb_split"):
        rs = shot["rgb_split"]
        dur = rs if isinstance(rs, (int, float)) and rs > 0.01 and rs is not True else 0.15
        g += f",rgbashift=rh=-{max(2, round(TW * 0.008))}:bh={max(2, round(TW * 0.008))}:enable='lt(t,{dur})'"
    if shot.get("flash"):
        g += f",fade=t=in:st=0:d={float(shot['flash'])}:color=white"
    if shot.get("dip"):
        g += f",fade=t=out:st={max(0, D - float(shot['dip'])):.3f}:d={float(shot['dip'])}:color=black"
    g += ",format=yuv420p,setsar=1[v]"
    return g


def audio_graph(shot, nf, F, src_total, has_audio, speed, ramped, src_gain_db):
    D = nf / F
    dialogue = bool(shot.get("dialogue"))
    mode = shot.get("audio", "keep" if dialogue else "ambient")
    if mode == "mute" or not has_audio or (ramped and not dialogue):
        return f"anullsrc=r=48000:cl=stereo,atrim=duration={D:.5f}[a]"
    gain = 0.0 if mode == "keep" else src_gain_db
    chain = f"[0:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{src_total + 0.05:.4f},asetpts=PTS-STARTPTS"
    if speed != 1.0 and not ramped:
        chain += "," + atempo_chain(speed)
    return chain + f",volume={gain}dB,apad,atrim=duration={D:.5f}[a]"


# ----------------------------------------------------------------------------- final pass
def build_look(look, W, H):
    chain = []
    if look.get("lut"):
        chain.append(f"lut3d=file='{esc(look['lut'])}'")
    L, s = look["black_lift"], look["s_curve"]
    if L or s:
        pts = [(0, L), (0.25, 0.25 - 0.07 * s + 0.6 * L), (0.5, 0.5 + 0.2 * L), (0.75, 0.75 + 0.07 * s - 0.02 * L), (1, 1 - 0.01 * s)]
        lastv = -1
        clean = []
        for x, y in pts:
            y = min(max(y, 0), 1)
            y = max(y, lastv + 0.001)
            clean.append((x, min(y, 1)))
            lastv = y
        chain.append("curves=all='" + " ".join(f"{x}/{y:.4f}" for x, y in clean) + "'")
    sh, hi = look["shadow_tint"], look["highlight_tint"]
    if sh or hi:
        chain.append(f"colorbalance=rs={-0.08 * sh:.3f}:gs={0.02 * sh:.3f}:bs={0.10 * sh:.3f}:"
                     f"rh={0.08 * hi:.3f}:gh={0.01 * hi:.3f}:bh={-0.08 * hi:.3f}")
    if look["contrast"] != 1.0 or look["saturation"] != 1.0:
        chain.append(f"eq=contrast={look['contrast']}:saturation={look['saturation']}")
    g = "[vin]" + (",".join(chain) if chain else "null") + "[g1]"
    cur = "g1"
    if look["bloom"] > 0:
        sigma = max(3, int(H * 0.012 / 4))
        g += (f";[g1]split=2[ba][bb];[bb]curves=all='0/0 0.62/0 1/1',scale={W // 4}:{H // 4},gblur=sigma={sigma},"
              f"scale={W}:{H}[bg];[ba][bg]blend=all_mode=screen:all_opacity={min(look['bloom'], 0.7)}[g2]")
        cur = "g2"
    tail = []
    if look["grain"] > 0:
        tail.append(f"noise=alls={look['grain']}:allf=t+u")
    if look["vignette"] > 0:
        ang = look["vignette"] * math.pi / 3          # ffmpeg: larger angle = stronger vignette (0.6 ~ default PI/5)
        tail.append(f"vignette=angle={ang:.3f}")
    g += f";[{cur}]" + (",".join(tail) if tail else "null") + "[vlook]"
    return g


def find_font():
    for p in ("/usr/share/fonts/opentype/inter/Inter-Bold.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(p):
            return p
    r = run(["fc-match", "-f", "%{file}", "sans:bold"], check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def text_filters(texts, W, H, safe, workdir, warnings, font):
    parts = []
    top, bottom, side = safe["top"], safe["bottom"], safe["side"]
    for i, t in enumerate(texts):
        size = max(16, int(H * float(t.get("size", 0.032))))
        maxw = W * (1 - 2 * side)
        cpl = max(8, int(maxw / (size * 0.56)))
        body = t["text"].lower() if t.get("lowercase") else t["text"]
        wrapped = "\n".join(textwrap.wrap(body, cpl)) or body
        lines = wrapped.count("\n") + 1
        yb = float(t.get("y", 0.5))                                         # 0..1 inside the safe band
        yc = H * (top + yb * (1 - top - bottom))
        block = lines * size * 1.25
        yc = min(max(yc, H * top + block / 2), H * (1 - bottom) - block / 2)  # never leave the safe band
        if block > H * (1 - top - bottom):
            warnings.append(f"text #{i} is taller than the safe band")
        tf = os.path.join(workdir, f"text_{i}.txt")
        open(tf, "w", encoding="utf-8").write(wrapped)
        s, e, f = float(t["start"]), float(t["end"]), float(t.get("fade", 0.25))
        alpha = f"clip(min((t-{s})/{f},({e}-t)/{f}),0,1)"
        fp = f"fontfile='{esc(t.get('font') or font)}':" if (t.get("font") or font) else ""
        parts.append(f"drawtext={fp}textfile='{esc(tf)}':fontsize={size}:fontcolor={t.get('color', 'white')}:"
                     f"x=(w-text_w)/2:y={yc:.1f}-text_h/2:shadowcolor=black@0.65:shadowx=2:shadowy=2:"
                     f"line_spacing={int(size * 0.2)}:alpha='{alpha}':enable='between(t,{s},{e})'")
    return parts


def duck_expr(spans, duck_db, ramp=0.25):
    g = 10 ** (duck_db / 20)
    terms = [f"(1-(1-{g:.4f})*clip(min((t-({s}-{ramp}))/{ramp},(({e}+{ramp})-t)/{ramp}),0,1))" for s, e in spans]
    if not terms:
        return None
    expr = terms[0]
    for t in terms[1:]:
        expr = f"min({expr},{t})"
    return expr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    ap.add_argument("--preview", action="store_true", help="half-size, fast encode")
    ap.add_argument("--workdir")
    ap.add_argument("--clean", action="store_true", help="delete the workdir afterwards")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and ffmpeg commands, render nothing")
    args = ap.parse_args()
    need_binaries()

    spec = json.load(open(args.spec))
    base_dir = os.path.dirname(os.path.abspath(args.spec))
    o = spec["output"]
    scale = 0.5 if args.preview else 1.0
    TW, TH = int(o.get("width", 1080) * scale) // 2 * 2, int(o.get("height", 1920) * scale) // 2 * 2
    F = int(o.get("fps", 30))
    out_path = args.out or o.get("path", "film_aura_edit.mp4")
    if args.preview and not args.out:
        root, ext = os.path.splitext(out_path)
        out_path = f"{root}_preview{ext or '.mp4'}"
    if not os.path.isabs(out_path):
        out_path = os.path.join(base_dir, out_path)
    workdir = args.workdir or out_path + ".build"
    os.makedirs(workdir, exist_ok=True)
    warnings = []

    look = copy.deepcopy(PRESETS.get(spec.get("look", {}).get("preset", "natural"), PRESETS["natural"]))
    look.update({k: v for k, v in spec.get("look", {}).items() if k != "preset"})
    safe = {**DEFAULT_SAFE, **spec.get("safe_area", {})}
    audio = spec.get("audio", {})
    interp = spec.get("interp", "blend")
    shots = spec["shots"]

    # ---- timeline in whole frames (absolute cut times -> no drift)
    if all("at" in s for s in shots):
        fa = [round(s["at"] * F) for s in shots]
    else:
        fa, c = [], 0
        for s in shots:
            fa.append(c)
            c += round(float(s["dur"]) * F)
    D = float(o.get("duration") or 0)
    total = round(D * F) if D else (fa[-1] + round(float(shots[-1]["dur"]) * F))
    D = total / F
    fa.append(total)

    clips, spans, src_cache = [], [], {}
    print(f"{'#':>2} {'at':>6} {'dur':>5}  src range            speed  notes")
    for i, s in enumerate(shots):
        nf = fa[i + 1] - fa[i]
        if nf < 2:
            sys.exit(f"shot {i} is shorter than 2 frames")
        if not s.get("src") or s.get("start") is None:
            sys.exit(f"shot {i} has no src/start yet (spec skeleton still has TODO shots)")
        src = s["src"] if os.path.isabs(s["src"]) else os.path.join(base_dir, s["src"])
        if not os.path.exists(src):
            sys.exit(f"shot {i}: source not found: {src}")
        info = src_cache.setdefault(src, probe(src))
        dur = nf / F
        src_total, speed_expr, smin = speed_plan(s, dur)
        start = float(s["start"])
        if start + src_total > info["duration"] + 0.05:
            sys.exit(f"shot {i}: source range {start:.2f}-{start + src_total:.2f}s runs past the end of {os.path.basename(src)} "
                     f"({info['duration']:.2f}s). Lower start or speed.")
        speed = float(s.get("speed", 1.0))
        ramped = bool(s.get("ramp"))
        key = hashlib.sha1(json.dumps([s, nf, F, TW, TH, spec.get("reframe"), interp, os.path.getmtime(src),
                                       os.path.getsize(src), audio.get("source_gain_db", -18)], sort_keys=True,
                                      default=str).encode()).hexdigest()[:10]
        clip = os.path.join(workdir, f"shot_{i:03d}_{key}.mkv")
        vg = video_graph(s, nf, F, TW, TH, spec.get("reframe", "cover"), speed_expr, smin, interp)
        ag = audio_graph(s, nf, F, src_total, info["has_audio"], speed, ramped, audio.get("source_gain_db", -18))
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.4f}", "-t", f"{src_total + 0.4:.4f}",
               "-i", src, "-filter_complex", vg + ";" + ag, "-map", "[v]", "-map", "[a]", "-frames:v", str(nf),
               "-t", f"{dur:.5f}", "-r", str(F), "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
               "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", clip]
        note = " ".join(x for x in (s.get("role", ""), "dialogue" if s.get("dialogue") else "",
                                    "ramp" if ramped else "", f"zoom{s['zoom']}" if s.get("zoom") else "",
                                    "flash" if s.get("flash") else "") if x)
        print(f"{i:>2} {fa[i] / F:>6.2f} {dur:>5.2f}  {start:>7.2f}-{start + src_total:<7.2f}   {'ramp' if ramped else speed:>5}  {note}")
        if s.get("dialogue"):
            spans.append((round(fa[i] / F, 3), round(fa[i + 1] / F, 3)))
        if args.dry_run:
            print("   ", " ".join(cmd[:8]), "...")
        elif not os.path.exists(clip):
            run(cmd)
        clips.append(clip)

    # ---- music grid report
    grid = spec.get("grid")
    if grid:
        T = 60.0 / grid["bpm"]
        dev = []
        for f in fa[1:-1]:
            t = f / F
            k = round((t - grid["offset"]) / T)
            dev.append(round((t - (grid["offset"] + k * T)) * 1000))
        bad = [d for d in dev if abs(d) > 45]
        print(f"cuts vs beat grid (ms): {dev}" + (f"   <- {len(bad)} cuts off-beat by >45 ms" if bad else "   all on beat"))

    # ---- final pass
    listing = os.path.join(workdir, "concat.txt")
    with open(listing, "w") as fh:
        for c in clips:
            fh.write(f"file '{os.path.abspath(c)}'\n")
    vg = build_look(look, TW, TH)
    tfs = text_filters(spec.get("text", []), TW, TH, safe, workdir, warnings, find_font())
    last = "[vlook]"
    if tfs:
        vg += f";[vlook]" + ",".join(tfs) + "[vout]"
        last = "[vout]"
    vg = vg.replace("[vin]", "[0:v]", 1)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing]
    ag = "[0:a]aresample=48000,volume=0dB[src]"
    music = audio.get("music")
    if music:
        mp = music if os.path.isabs(music) else os.path.join(base_dir, music)
        if not os.path.exists(mp):
            sys.exit(f"music not found: {mp}")
        cmd += ["-i", mp]
        ms = float(audio.get("music_start", 0))
        m = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=start={ms}:duration={D + 0.5:.3f},asetpts=PTS-STARTPTS"
        if audio.get("bass_boost_db"):
            m += f",bass=g={audio['bass_boost_db']}:f=90"
        if audio.get("fade_in"):
            m += f",afade=t=in:st=0:d={audio['fade_in']}"
        fo = audio.get("fade_out", 1.0)
        if fo:
            m += f",afade=t=out:st={max(0, D - fo):.3f}:d={fo}"
        m += f",volume={audio.get('music_gain_db', 0)}dB"
        de = duck_expr(spans, audio.get("duck_db", -14)) if spans else None
        if de:
            m += f",volume='{de}':eval=frame"
        ag += ";" + m + "[mus];[src][mus]amix=inputs=2:duration=first:normalize=0[mix]"
    else:
        ag += ";[src]anull[mix]"
    ag += f";[mix]loudnorm=I={audio.get('loudness', -14)}:TP=-1.5:LRA=11,alimiter=limit=0.95[aout]"
    cmd += ["-filter_complex", vg + ";" + ag, "-map", last, "-map", "[aout]", "-t", f"{D:.4f}", "-r", str(F),
            "-c:v", "libx264", "-preset", "veryfast" if args.preview else "slow", "-crf", str(o.get("crf", 26 if args.preview else 18)),
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out_path]
    for w in warnings:
        print("WARNING:", w)
    if args.dry_run:
        print("final:", " ".join(cmd[:12]), "...")
        return
    run(cmd)
    info = probe(out_path)
    print(f"\nrendered {out_path}\n  {info['width']}x{info['height']} {info['fps']}fps {info['duration']:.2f}s "
          f"audio={'yes' if info['has_audio'] else 'NO'}  ({os.path.getsize(out_path) / 1e6:.1f} MB)")
    if args.clean:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
