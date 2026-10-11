# Film Aura Edit - the recipe and where it comes from

## Source of the recipe
Higgsfield's guide *"How to make $3000/month on clipping videos with AI"* (Stage 3, "1. Film Aura Edit") describes the job
that Higgsfield Katana does. The user shared two screenshots of it; the page itself was not reachable from the sandbox.
What the screenshots state:

* **What it does:** a 25-30 s **vertical** edit around **one character**, with a clear opening and the **strongest visual
  moment timed to the music**.
* **Three separate inputs:** source footage (a film, podcast, stream, original recording - own or permitted), an editing
  **reference** (sets pacing, shot rhythm and transitions - "use footage from our film, not from the reference"), and a
  **music track** attached separately.
* **The prompt:** open with a moment that makes sense without watching the full film; use the reference for pacing, shot
  rhythm, transitions; cut to the music track; **build toward the drop and place the strongest visual moment on it**;
  **keep dialogue clear**; deliver **9:16** with **readable text inside the platform's safe areas**; give a **short report
  after analysis, the edit plan and the preview** (so the user can steer before more versions).
* The guide's other edits (podcast aura edit, vertical reel from everyday footage, a batch of 10) reuse the same three
  inputs; reusing the same reference + music direction keeps a batch consistent.

Everything below that is not in that list is this skill's own craft judgement, informed by general "aura edit" conventions
(slowed phonk-style music, slow-motion on the character, glow/flash accents, speed ramps - see the Pippit and Dreamina aura
templates) - and it is subordinate to the reference: **if the reference does something different, copy the reference.**

## Structure of a 28 s edit (default proportions)
| part | share | job |
|---|---|---|
| hook | 0-3 s | one self-explanatory beat: a strong line, action or reaction of the character. Dialogue allowed (`dialogue: true`). Must work for someone who has not seen the film. |
| build | to ~60% | cuts follow the reference rhythm on the beat grid, usually tightening toward the drop. Different angles/moments of the same character; keep identity and direction of look consistent. |
| payoff | starts exactly on the drop, ≥ 2 beats | the strongest visual moment of the character. Common treatments: slow-mo ramp into the hit, zoom punch, flash - only those the reference uses. |
| afterglow | rest | release; cuts relax; can carry a closing line or a held reaction. |
| outro | last shot | hold or fade; music fades out over ~1 s. |

`make_plan.py` produces this skeleton from the reference's own shot-length curve, snapped to the music beats, with a cut
forced onto the drop.

## Picking moments (why these rules)
* Prefer shots where the character is **large in frame and the face is readable at phone size** - the edit is about
  presence ("aura"), not plot.
* Start source ranges at a **line's start minus ~0.15 s** and end after the last word, so speech is never clipped.
* Keep each source range **inside one film shot** (`find_moments.py` lists the film's cuts); crossing a cut inside a slot
  looks like an accidental extra cut and breaks the rhythm the reference set.
* The payoff must be the best-looking, highest-energy moment you have; if two candidates compete, put the better one on the
  drop and use the other in the build.
* Wide/scope footage: a 9:16 crop of 2.39:1 keeps only ~23% of the width. Check the character survives the crop (use `pan`);
  otherwise use `reframe: "blur"` for those shots.

## Music
* The drop (largest sustained energy jump, `analyze.py` → `audio.drops`, score ≥ ~2 = real) is the anchor; cuts sit on beats,
  the payoff cut sits on the drop.
* Dialogue shots duck the music (-14 dB by default, 0.25 s ramps). Keep dialogue shots short and early, so the music
  can carry the build and the drop stays loud.
* If the track has no clear drop, say so in the report and place the payoff on a strong downbeat at ~60%.

## Text
* Optional and short (≤ ~6 words, one or two lines): a name, a quoted line, or a one-word hook. Skip it if the reference
  has none.
* Always inside the safe band (renderer enforces it); verify with `contact_sheet.py --safe`.

## Look
Default `natural`: the film's own grade is part of its aura. Switch to `cinematic` or tune values only when the reference
or the user wants a graded/film-print look - `analyze.py --compare` on a preview shows luma/contrast/saturation/tint/grain
deltas against the reference with the spec field to adjust.

## Research notes (for context, not requirements)
* "Aura" edits are slow-motion, main-character clips over slowed phonk-style audio with glow and speed effects.
* Existing Claude Code video skills (ffmpeg-based editors, Remotion) edit by transcript/script or generate motion graphics;
  none found copies a reference edit's pacing - hence the reference-analysis + beat-grid planning scripts here.
* Remotion renders React compositions (generated graphics), ffmpeg edits existing footage deterministically - this skill uses ffmpeg.
