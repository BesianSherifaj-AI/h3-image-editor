# Quality status · 1.6.1-rc.1

Recorded 12 November 2026. This is a preview release with unfinished GPU quality
validation. Passing code tests or an AI critic's verdict does not certify visual quality.

## Changes prompted by actual output inspection

- Video selection previews and the first processed frame now share a decoder. The
  previous timestamp conversion could select a later pose, misaligning the initial mask.
- Video tint preserves source luminance and alpha instead of applying a flat color
  overlay that suppressed the jacket's shading and texture.
- Automatic object removal can include nearby shadow space and separately segment
  foreground objects to protect. The Director exposes the operation and repair margin.
  User-painted masks are not expanded. The new automatic path awaits a GPU quality rerun.
- Game interruptions remain cancellations through child jobs, Director runs and video
  runs, preserving their reason and saved attempts rather than reporting a quality failure.
- AI review prompts now explicitly check material changes, invented details and removal
  ghosts. These prompts still require independent visual review.

## Completed checks

Actual MiniMax H3 image editing, Qwen vision planning/review and SAM object selection
were exercised locally. The test set included object removal and recoloring a leather
jacket. The AI critic passed a jacket result that independent inspection rejected for
fabric-like texture; a more precise prompt improved the result without preserving all
original grain and highlights.

Two CPU video tint attempts used a completed SAM image selection on moving, H3-generated
cafe footage. Each preview contained 24 frames at 12 fps over two seconds. Every edited
frame was visually inspected. The refined tint preserved shading and color more faithfully.

Technical checks on the refined preview:

- First processed frame exactly matched the selection preview.
- Pixels outside the processing masks remained unchanged in lossless frames.
- Full FFmpeg video/audio decode passed, and exported PNG frames matched the stored outputs.
- Original and exported audio had a measured correlation of 0.9983.
- Muted browser playback reached the end without a media error.

All 107 backend regression tests passed locally, along with the existing frontend test
scripts and JavaScript syntax check. These checks use isolated data and do not load GPU
models. CI checks CPU behavior on Linux; local visual observations concern the tested
Windows environment. No Linux GPU quality claim is made.

## Remaining defects and validation

- Optical-flow tracking left small mask holes during hand movement. The refined CPU
  video remains marked as needing work despite its technical checks passing.
- H3 recoloring can change material detail. Exact preservation of texture inside an
  edited region is not guaranteed.
- Baseline object removal left a pale ghost because the compositing mask retained shadow
  context. The expanded repair/protection change is covered by regressions, but its actual
  model rerun was interrupted by the game guard and has not been visually accepted.
- The planned eight-frame SAM/H3 moving-video run and a SAM-tracked tint comparison
  remain unverified. No claim is made that the new changes fix all tracking or flicker defects.

Before a stable release, rerun protected automatic removal, inspect all masks and edited
frames from the SAM/H3 video case, compare SAM-tracked tint for color-only changes, and
repeat decode, audio, lossless export and playback checks on those outputs. Keep every
attempt and record independent findings separately from the model critic's verdict.
