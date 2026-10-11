---
name: film-aura-edit
description: Make a "Film Aura Edit" - a 25-30 second vertical 9:16 character edit cut from a film, series, podcast or any long video, using a reference edit for pacing / shot rhythm / transitions and a separate music track, built to the drop with the strongest visual moment on it, dialogue kept clear, text inside the platform safe areas. Use this skill whenever the user says "film aura edit", "aura edit", "character edit", "clip/teaser/reel from this film", "edit it like this reference", or hands over source footage plus a reference video (and usually a music track) and wants an edit made - even if they never say "aura". Works locally with ffmpeg inside Claude Code (analyzes the reference and music, plans cuts on the beat grid, renders a preview, then the final). Also fits podcast/reel variants and batches that reuse one reference + music.
---

# Film Aura Edit

You turn **source footage + an editing reference + a music track** into a short vertical edit. This reproduces the workflow
Higgsfield Katana describes ("Film Aura Edit") locally: you analyze, plan, render a preview, report, and refine. Background
and the exact rules from that guide: `references/aura-recipe.md`. Spec format: `references/edit-spec.md`.

`$SK` below = the folder this SKILL.md is in (`scripts/` is inside it). Work in a scratch folder such as `./aura_work/<project>/`;
never write into the user's footage folders. Requirements: `ffmpeg`/`ffprobe`, `python3` with `numpy` and `Pillow`
(`pip install numpy pillow`). Check with `ffmpeg -version` first.

## The three inputs (keep them distinct)

| input | what you take from it | what you must NOT do |
|---|---|---|
| **Source footage** (the film) | every shot in the edit | - |
| **Reference edit** | pacing, shot rhythm, transitions (and look only if the user asks) | put any of its footage or audio in the result |
| **Music track** | tempo, beat grid, the drop, the audio bed | use the reference's soundtrack instead |

If the reference or the music is missing, ask once, briefly. If the user would rather go on, use the generic rhythm
(`make_plan.py` without `--ref`) and say so in the report. Optional extras: an `.srt` for the film (best way to find a
strong line - extract one with `ffmpeg -i film.mkv -map 0:s:0 film.srt`), target length (default 28 s), who the
character is, any on-screen text. The user is responsible for having the right to use the footage and music.

## Workflow

### 1. Analyze (reference, music)
```bash
python3 $SK/scripts/analyze.py REFERENCE.mp4 --out work/ref   --role reference
python3 $SK/scripts/analyze.py MUSIC.mp3     --out work/music --role music
```
Then **open `work/ref/contact_sheet.jpg` with the Read tool** and look: what kinds of shots (close-ups? wide?), how
they transition, whether there are slow-motion holds, flashes, text, a grade. The numbers tell you rhythm; only your eyes
tell you style. Key outputs: reference shot lengths, rhythm trend (accelerating?), transition counts, % of cuts on beat;
music BPM, `drops` (score ≥ ~2 is a real drop; ~0.3-1 is noise), beat grid. If the reference is not beat-locked
(`cut_on_beat` near chance level) say so, but still snap to the music - it is the new music that sets the beat.

### 2. Find the moments in the film
```bash
python3 $SK/scripts/find_moments.py FILM.mp4 --out work/moments --srt film.srt --count 12 [--start S --end E]
```
Narrow `--start/--end` to the stretch where the character appears (full films are slow to scan). **Read
`work/moments/moments_sheet.jpg`** and choose with judgement: one character throughout; a **hook** that makes sense without
the film (a strong line / action / reaction); the **single best visual moment** for the drop. `moments.json` lists the film's
own hard cuts - keep each source range inside one film shot. Use
`python3 $SK/scripts/contact_sheet.py FILM.mp4 --out s.jpg --times 61.0,61.5,62.0` to inspect candidate frames and read the
character's horizontal position for `pan`.

### 3. Plan on the beat grid
```bash
python3 $SK/scripts/make_plan.py --ref work/ref/analysis.json --music work/music/analysis.json \
    --music-file MUSIC.mp3 --duration 28 --out work/plan.json --emit-spec work/spec.json --source FILM.mp4
```
This maps the reference's shot-length curve onto the music's beats, forces a cut on the drop, chooses `music_start` so the
drop lands at the reference's proportion (else ~62%), copies the reference's transition style and labels each slot (hook /
build / payoff / afterglow / outro). Read its warnings. Then **fill `work/spec.json`**: for every shot set `src`, `start`
(source seconds), `pan`, and as needed `dialogue: true` (hook line), a `ramp` or `zoom`/`punch`/`flash` on the payoff (only
what the reference does), optional `text`. Field reference: `references/edit-spec.md`. You may adjust the plan - merge slots,
move a cut by a beat - but keep cuts on the grid and the payoff on the drop.

### 4. Preview and check
```bash
python3 $SK/scripts/build_edit.py work/spec.json --preview
python3 $SK/scripts/contact_sheet.py work/..._preview.mp4 --out work/sheet.jpg --safe --count 24
python3 $SK/scripts/contact_sheet.py work/..._preview.mp4 --out work/drop.jpg --start DROP-0.3 --end DROP+0.9 --step 0.1
python3 $SK/scripts/analyze.py work/..._preview.mp4 --out work/qa --role output --compare work/ref/analysis.json
```
Check, and fix before showing the user:
- [ ] "cuts vs beat grid" printed by the renderer: all within ~45 ms; the payoff cut sits on the drop.
- [ ] Sheet: the character is in frame in every vertical crop; no film cut hiding inside a slot; text inside the green safe box.
- [ ] Drop sheet: the strongest moment is visible at the drop (not a black frame / mid-blink / empty room).
- [ ] Hook shot works alone; dialogue not clipped (start ~0.15 s before the first word); `dialogue: true` set where speech matters.
- [ ] Compare table: shot count / mean shot length / cuts-on-beat in the same range as the reference. Deltas on look
      (luma, saturation, tint, grain) matter only if the user wants the reference's look - the table names the spec field.
- [ ] Peak level not above about -1 dBFS in the report, loudness near -14 LUFS (renderer normalizes), audio present.

### 5. Report, then iterate / finalize
Give the user a **short report**: (a) analysis - reference rhythm/transitions, music BPM and drop, (b) edit plan - table of
slots with time, role, which film moment, (c) the preview file. Then ask what to change ("tighter build? different payoff
shot? text?"). Re-render quickly after tweaks (shot clips are cached). Final render = same command without `--preview`
(slower, full 1080x1920, crf 18). Deliver the final path; if the user wants a batch or a podcast/reel variant, **reuse the
same `work/ref` and `work/music` analysis** so every edit shares the direction, and only redo steps 2-4.

## Craft rules (and why)
- **Reference sets rhythm, music sets the beat.** Scale the reference's shot-length curve to the new length, snap to the new
  beats. Copying exact seconds from the reference would put cuts off the new music.
- **One character, strongest moment on the drop.** The drop is the one place every viewer looks; spending the best shot elsewhere wastes it.
- **Dialogue first.** Mark speech shots `dialogue: true` so the music ducks; keep them short and early; never cut mid-word.
- **Use only what the reference uses.** Flash, zoom punch, slow-mo ramp, shake, RGB split are available, but if the reference
  is clean hard cuts, keep it clean. Restraint reads as confidence.
- **Look = the film's own grade** (`natural`) unless asked. Don't stack bloom/grain/vignette by default.
- **Text only inside the safe band**, short, legible for ≥ 1.5 s. The renderer clamps and wraps, but check the sheet.
- **Wide footage:** if a 9:16 crop loses the character, use `reframe: "blur"` for that shot instead of fighting `pan`.

## Known limits (be honest with the user)
- Cut/transition detection is heuristic (hard cut, flash, dip to black; "soft" = possible dissolve or fast pan). Speed ramps,
  text styling and grade are judged by eye from the contact sheet.
- No face tracking and no transcription: pick `pan` by looking at frames; use an `.srt` (or the sheet) to find lines.
- HDR / log sources are not tone-mapped; if colors look washed out, convert the clip to SDR first
  (`ffmpeg -vf zscale=t=linear,tonemap=hable,zscale=t=bt709:m=bt709:r=tv`).
- Does not generate AI footage (Higgsfield generation is out of scope); it edits what the user provides.
- Scripts accept absolute paths; run with `python3` from any folder. If a script fails, read its message - most are
  "source range runs past end of file" or a missing path.
