# Product tours: how they are made

The tours, practice tasks and help menu run from [`frontend/static/tour.js`](../static/tour.js), with the content in
[`frontend/static/tour/tours.json`](../static/tour/tours.json). Each tour step has three media files in
`frontend/static/tour/`, built by the two scripts here:

| File | Made by | What it is |
|---|---|---|
| `<step>.mp3` | `voice.py` | The voice-over (Gradium text-to-speech, voice "Brynn") |
| `<step>.mp4`, `<step>.jpg` | `capture.py` | Silent footage of the real app doing the feature, and its poster frame (light theme) |
| `<step>-dark.mp4`, `<step>-dark.jpg` | `capture.py --dark` | The same, recorded in the dark theme (the player picks by theme) |
| `media.json` | `capture.py` | Caption cues (from the voice's word timings) and each clip's length |
| `voice.json` (here) | `voice.py` | Every take's transcript, word error rate and word timings |

## Rebuild

From the repository root. `GRADIUM_KEY` must be in the git-ignored `.env`; it is read with the project's own loader and
never printed.

```bash
python frontend/tour/voice.py                      # 1. the voice: 2 takes per step, the one heard back best is kept
PORT=8765 python frontend/server.py                #    (another terminal) the app, with the AI model for the Build clips
python frontend/tour/capture.py                    # 2. the footage, every action timed to the word that describes it
python frontend/tour/capture.py --dark             #    and again in the dark theme
python frontend/tour/capture.py canvas-fix         #    or only some steps
python -m pytest frontend/tests/test_tour.py -q    # 3. media present, text short, docs links valid
```

Change a step's words in `tours.json`, then run both steps for that step id: the footage is cut to the new voice.

## How a clip is recorded

- Chrome's own screencast at 2× pixel density, 1024 × 640 CSS pixels, in the light and the dark theme.
- A **camera**: each action also logs a zoom and pan (`c.look(locator, 1.6)`), and every output frame is cropped from the
  screencast through that camera (eased, sub-pixel) and scaled to 1280 × 800 H.264, so the app's text is at its real
  size even in a small player. Needs Pillow.
- A cursor is drawn in the page (screencasts don't include the system one) with a pink ripple on every click; it is kept
  above dialogs.
- Each action waits for its narration word (`await c.at("fix")`), using the word timings Gradium's speech-to-text
  returned for the kept take, so the voice and the picture line up when the tour plays them together.
- Recording uses the user name `tour`, so nobody's saved cohorts change. Build clips ask the model once to warm its
  cache, then record the cached (fast) reply.

## Principles the tours follow

- **Offered, never forced.** First visit only, as a corner card the page keeps working around; a reload never shows it
  again. Help (?) has every tour, the practice tasks and the docs.
- **Complex features only.** Clarifying questions, double counts, verdicts, grain, verified SQL, comparisons; nothing a
  button label already explains.
- **Short.** At most five steps; a title of a few words and one or two short sentences per step (the tests enforce it).
- **Real footage, real numbers.** Every clip is the app on the frozen release.
- **Never in the way.** With a spotlight the player is a compact card beside the control (it follows the control if the
  page moves); clicking the spotlit control tries it for real. Focus always returns where it was.
- **Learn by doing.** Practice tasks get a coach card and a beacon on the control while they're under way.
- **Accessible.** Keyboard (← → Esc), captions, a mute switch, reduced motion (no autoplay), light and dark footage.

The flows were reviewed by an independent UX critique against Figma/Jira-style onboarding patterns, and re-checked
after the fixes.
