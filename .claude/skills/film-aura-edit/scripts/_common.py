"""Shared helpers for the film-aura-edit scripts (ffprobe, frame/audio decoding, contact sheets).

Only needs: ffmpeg/ffprobe on PATH, numpy, Pillow.
"""
import json
import os
import shutil
import subprocess
import sys

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.exit("numpy is required: pip install numpy pillow")


def need_binaries():
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            sys.exit(f"{b} not found on PATH. Install ffmpeg (apt install ffmpeg / brew install ffmpeg).")


def run(cmd, check=True, capture=True):
    """Run a command (list of args, never a shell string)."""
    r = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")[-2500:] if r.stderr else ""
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{err}")
    return r


def probe(path):
    """Return a flat dict of the facts the edit needs. Width/height are *display* size (rotation applied)."""
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    out = {"path": path, "has_audio": a is not None, "has_video": v is not None}
    dur = info.get("format", {}).get("duration")
    out["duration"] = float(dur) if dur not in (None, "N/A") else None
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(round(float(sd["rotation"])))
        rot = rot or int(v.get("tags", {}).get("rotate", 0) or 0)
        if abs(rot) % 180 == 90:
            w, h = h, w
        num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        if not fps:
            num, _, den = (v.get("r_frame_rate") or "30/1").partition("/")
            fps = float(num) / float(den or 1)
        out.update(width=w, height=h, fps=round(fps, 3), rotation=rot, vcodec=v.get("codec_name"),
                   aspect=round(w / h, 4))
        if out["duration"] is None and v.get("duration"):
            out["duration"] = float(v["duration"])
    if a:
        out["acodec"] = a.get("codec_name")
        out["sample_rate"] = int(a.get("sample_rate", 0) or 0)
    return out


def aspect_label(aspect):
    table = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "4:3": 4 / 3, "16:9": 16 / 9, "2.35:1": 2.35, "2.39:1": 2.39}
    best = min(table, key=lambda k: abs(table[k] - aspect))
    return best if abs(table[best] - aspect) / table[best] < 0.04 else f"{aspect:.2f}:1"


def read_frames(path, n, width, height, start=0.0, end=None, crop=None, duration=None):
    """Decode n evenly spaced RGB frames from [start, end].

    width/height: scale target (ignored when crop is given -> native-resolution centre crop (cw, ch)).
    Returns (frames uint8 array (n,H,W,3), times list).
    """
    if end is None:
        end = duration if duration is not None else probe(path)["duration"]
    span = max(end - start, 0.05)
    step = span / n
    vf = [f"fps={n / span:.6f}"]
    if crop:
        vf.append(f"crop={crop[0]}:{crop[1]}")
        width, height = crop
    else:
        vf.append(f"scale={width}:{height}:flags=area")
    vf.append("format=rgb24")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start + step / 2:.4f}", "-t", f"{span:.4f}", "-i", path,
           "-an", "-vf", ",".join(vf), "-frames:v", str(n), "-f", "rawvideo", "-"]
    raw = run(cmd).stdout
    fsize = width * height * 3
    got = len(raw) // fsize
    if got == 0:
        raise RuntimeError(f"could not decode frames from {path}")
    arr = np.frombuffer(raw[: got * fsize], dtype=np.uint8).reshape(got, height, width, 3)
    times = [round(start + step / 2 + i * step, 3) for i in range(got)]
    return arr, times


def decode_audio(path, sr=22050, start=0.0, duration=None):
    """Mono float32 samples at sr, or None if the file has no audio."""
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = run(cmd, check=False)
    if r.returncode != 0 or len(r.stdout) < 4 * sr // 10:
        return None
    return np.frombuffer(r.stdout, dtype="<f4").copy()


def frame_at(path, t, width):
    """Single RGB PIL image at time t scaled to given width (aspect preserved)."""
    from PIL import Image
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1", "-an",
           "-vf", f"scale={width}:-2:flags=area", "-f", "image2pipe", "-vcodec", "png", "-"]
    r = run(cmd, check=False)
    if r.returncode != 0 or not r.stdout:
        return None
    import io
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def contact_sheet(images, labels, cols, out_path, pad=6, label_h=18, bg=(18, 18, 18)):
    """Tile PIL images (same size assumed) into one JPEG with a timestamp label under each."""
    from PIL import Image, ImageDraw, ImageFont
    images = [(im, lb) for im, lb in zip(images, labels) if im is not None]
    if not images:
        raise RuntimeError("no frames for contact sheet")
    tw, th = images[0][0].size
    label_h = max(label_h, 15 * max(lb.count("\n") + 1 for _, lb in images) + 4)
    cols = max(1, min(cols, len(images)))
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label_h + pad) + pad), bg)
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=13)
    except TypeError:  # old Pillow
        font = ImageFont.load_default()
    for i, (im, lb) in enumerate(images):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + label_h + pad)
        sheet.paste(im.resize((tw, th)), (x, y))
        draw.text((x + 2, y + th + 2), lb, fill=(230, 230, 230), font=font)
    sheet.save(out_path, quality=88)
    return out_path


def fmt_t(t):
    m, s = divmod(float(t), 60)
    return f"{int(m)}:{s:05.2f}"


def read_gray_stream(path, width, height, fps=None, start=0.0, duration=None):
    """Decode every frame (optionally resampled to fps) as small grayscale. Returns (N,H,W) uint8."""
    vf = []
    if fps:
        vf.append(f"fps={fps}")
    vf += [f"scale={width}:{height}:flags=area", "format=gray"]
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-an", "-vf", ",".join(vf), "-f", "rawvideo", "-"]
    raw = run(cmd).stdout
    fs = width * height
    n = len(raw) // fs
    return np.frombuffer(raw[: n * fs], dtype=np.uint8).reshape(n, height, width)


def parse_srt(path):
    """Minimal SRT parser -> [{start, end, text}]."""
    import re
    txt = open(path, encoding="utf-8", errors="replace").read().replace("\r", "")
    out = []
    for block in re.split(r"\n\s*\n", txt.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        for i, l in enumerate(lines):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", l.strip())
            if m:
                g = [int(x) for x in m.groups()]
                st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                out.append({"start": st, "end": en, "text": " ".join(lines[i + 1:]).strip()})
                break
    return out
