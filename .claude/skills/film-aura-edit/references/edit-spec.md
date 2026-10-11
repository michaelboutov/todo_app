# Edit spec (input of `scripts/build_edit.py`)

JSON. All times are seconds on the **final edit's** timeline unless a field says "source". `make_plan.py --emit-spec`
writes a skeleton with the `at` times already on the beat grid; fill in `src`, `start`, `pan` and the extras.

```json
{
  "output": {"path": "film_aura_edit.mp4", "width": 1080, "height": 1920, "fps": 30, "duration": 28.0, "crf": 18},
  "reframe": "cover",
  "look": {"preset": "natural"},
  "interp": "blend",
  "safe_area": {"top": 0.14, "bottom": 0.24, "side": 0.08},
  "shots": [ ... ],
  "text":  [ ... ],
  "audio": { ... },
  "grid":  {"bpm": 100, "offset": 0.6}
}
```

Relative paths resolve against the spec file's folder. `output.path` is where the render goes (`--preview` adds `_preview`).

## shots[]

| field | meaning |
|---|---|
| `at` | cut time in the edit. Give it on **every** shot (a shot lasts until the next `at`; the last until `output.duration`). Without `at`, give `dur` instead and shots are laid back to back. |
| `src`, `start` | source file and the **source** second where the shot starts. The shot uses `start … start + dur × speed`. |
| `speed` | constant playback speed (default 1). 0.5 = half speed. Audio is time-stretched with it. |
| `ramp` | speed curve instead of `speed`: `[[t, speed], ...]`, `t` = seconds into the shot (edit time), speed eased between keys (`ramp_ease`: `smooth`/`linear`). Example: `[[0,1.0],[0.4,0.3]]` = normal → slow-mo. Source audio is muted on ramped shots unless `dialogue`. |
| `pan` / `pan_to` | where the vertical crop sits in the source frame, `[x, y]` in 0-1 (0 = left/top). `[0.5,0.5]` = centre. `pan_to` makes the crop drift to a second position over the shot. Put the character inside the crop: look at a frame first. |
| `reframe` | per shot override of the top-level `reframe`: `cover` (crop to 9:16, uses `pan`) or `blur` (whole frame kept, sharp, centred over a blurred copy of itself - for wide/scope footage where a crop would cut the character out). |
| `zoom` | `[from, to]` scale over the shot (≥1), `zoom_ease` = `out` (default) / `in` / `smooth` / `linear`. |
| `punch` | beat hits: `[{"at": 0.0, "amount": 0.07, "decay": 0.3}]` - extra zoom that snaps in at `at` (shot time) and relaxes. |
| `shake` | camera shake amplitude as a fraction of frame width (`0.004`), or `{"amount": 0.006, "decay": 0.4}` to fade out like an impact. |
| `flash` | seconds of white at the start of the shot, fading in (a hit/flash transition). |
| `dip` | seconds of fade to black at the end of the shot. |
| `rgb_split` | `true` or seconds: red/blue channel split at the start of the shot (impact accent). |
| `dialogue` | `true` = this shot's speech must be heard: source audio at full level and the music ducks under it. Everything else gets only quiet ambient source audio (`audio.source_gain_db`). |
| `audio` | `keep` / `ambient` / `mute` override. |
| `role`, `note`, `transition_in` | ignored by the renderer; kept so the spec documents the plan. |

## text[]
`{"text": "...", "start": 0.6, "end": 3.0, "y": 0.9, "size": 0.032, "color": "white", "fade": 0.25, "lowercase": false}`

* `y` is 0-1 **inside the safe band** (0 = top of the band, 1 = bottom), not of the whole frame; the renderer clamps the
  block so it can never leave the band. `size` is a fraction of frame height. Long text wraps to the safe width.
* Default safe margins (fractions of the frame): top 0.14, bottom 0.24, sides 0.08 - conservative values that keep text
  clear of the usual Reels/Shorts/TikTok interface on a 1080x1920 frame. Override with `safe_area` if the user knows better.

## look
`preset`: `natural` (default - keeps the film's own grade, tiny punch + faint vignette), `cinematic` (lifted blacks, teal/orange
tint, light bloom, fine grain, vignette), `none`. Override any of: `contrast`, `saturation`, `black_lift` (0-0.1),
`s_curve` (0-1), `shadow_tint`, `highlight_tint` (0-1), `bloom` (0-0.7), `grain` (0-30), `vignette` (0-1), `lut` (path to a `.cube`).
Grain inflates file size a lot (the `cinematic` preset makes ~3 MB/s at crf 18): keep `grain` ≤ 8 or raise `output.crf` to 21-23 if the file must stay small.
Use the look only when the reference/user asks for it - the article's recipe takes **pacing, rhythm and transitions** from the reference.

## audio
`music` (path), `music_start` (seconds into the file where the edit starts - from `make_plan.py`), `fade_in`, `fade_out`
(default 1.0), `music_gain_db`, `duck_db` (how far the music drops under `dialogue` shots, default -14), `source_gain_db`
(ambient level of the film audio, default -18), `bass_boost_db`, `loudness` (LUFS target, default -14).

## grid
`{"bpm": ..., "offset": ...}` from `make_plan.py`. Only used to print how far each cut is from a beat (ms) - aim for under ~45 ms.

## interp
How slow-mo frames are created when a shot plays slower than 0.95x: `blend` (fast, default), `mci` (motion-compensated, smoother, slower, can warp), `none` (frame repeat).
