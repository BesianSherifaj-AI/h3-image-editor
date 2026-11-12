# Atelier · H3 Image Editor

A local image and short-video editing workspace for ComfyUI, LM Studio and CPU tools.
Version 1.6.1-rc.1. The interface runs at **http://127.0.0.1:8790**.

**Preview release:** quality fixes are included, but final GPU validation of automatic
removal and moving-video edits is unfinished. Read the [quality status](docs/QUALITY-STATUS.md)
for observed defects, completed checks and remaining work.

## Start on Windows

Install Python 3.11 or 3.12 and FFmpeg (with `ffmpeg` and `ffprobe` on PATH).
Run `Setup.ps1`, then `Launch.ps1 -OpenBrowser`. The launcher starts quietly at below-normal
priority. It never downloads model weights. The CPU editor works without a GPU model.

For AI features, copy `studio-config.example.json` to `studio-config.json`, then set your
ComfyUI source, its Python environment, and model directories. Use a ComfyUI revision
that includes the native MiniMax H3 Image, Qwen 2.1, Qwen Layered, SAM 3.1 and SeedVR2 nodes
needed by your chosen workflow. This app does not install arbitrary custom nodes.
See [model setup and compatibility](docs/MODELS.md) for publishers and the tested runtime.
Model filenames are listed in `backend/workflows.py` and `backend/helpers.py`.
Weights and their respective licenses are obtained separately from their publishers.

The dedicated renderer uses port 8014 and this app's `runtime` folder. It does not attach
to or unload another application's renderer. An existing `model-paths.yaml` is preserved;
otherwise a file is generated from your configured model roots.

## Start on Linux

Install Python 3.12, FFmpeg and OpenCV system dependencies (Ubuntu: `ffmpeg libgl1`).
Create `.venv`, install `requirements.lock.txt`, then run `bash launch.sh`.
Linux starts in CPU-only mode. GPU rendering on Linux is not part of this release's
hardware validation. Set `STUDIO_CPU_ONLY=false` only after configuring and validating
your ComfyUI GPU environment. Use `.venv/bin/python` for `comfy_python` on Linux.

## Choose a task

- **All tools / Ctrl+K:** searchable catalog with CPU/GPU requirements and plain descriptions.
- **Image editor:** generate, heal, remove, replace, recolor, outpaint, ControlNet and layers.
- **Select:** brush, rectangle, lasso/polygon, color wand, SAM points or object description.
- **Enhance:** photo adjustments, crop, resize and optional SeedVR2 AI upscale.
- **AI Director:** plan, automatically select, edit, review, and optionally make one corrected attempt.
- **Video lab:** trim or track and blur/tint/edit short clips; preserve audio; export frame/mask evidence.
- **Library / Versions:** reuse settings, compare before/after and restore a separate project copy.
- **Connections:** inspect models, pause work, free only this Studio's idle VRAM, and manage its CPU vision model.

Layer transforms, blend modes, transparent cutouts, selection refinement, recipes,
autosave, PNG/JPEG/WebP export and editable project ZIPs are included. Advanced controls
use collapsible panels; comfortable and compact layouts are available.

AI Director now has an explicit **Remove an object and its nearby shadow** operation.
Automatic removal can expand the repair area and protect nearby foreground objects;
this path is experimental pending its GPU quality rerun. Painted selections retain
their original boundaries, so include the shadow when painting a removal area.

## H3 video companion

Use [H3 Prompt Studio](https://github.com/BesianSherifaj-AI/h3-prompt-studio) 1.7.1 or newer
at localhost:8766. **H3 video** sends the current composition into a new video draft.
The transfer is idempotent, preserves existing projects and does not start a render.
Open the returned draft link to continue. To bring a video frame back, import its MP4
in Video lab and load the desired frame into the image editor.

## Local AI and automation

ComfyUI runs native graphs from `backend/workflows.py`; LM Studio provides optional
vision through its local HTTP API. `mcp_server.py` exposes bounded Studio operations;
install `requirements-mcp.lock.txt` in a separate environment to use it. There is no
arbitrary Python execution endpoint or automatic cloud upload.

GPU work waits when Fortnite or Rocket League is detected. This is best-effort process
detection, not a hardware resource reservation. Use **Connections → Pause new work**
before other demanding games. A render already in progress may take time to release VRAM.
Only the Studio's own idle models are unloaded.

## Limits and verification

H3 image edits preserve pixels outside a supplied mask in lossless image outputs.
AI judgments can miss defects; inspect edges and play video results yourself.
Qwen Layered is an approximate decomposition, not guaranteed exact reconstruction.
H3 video edits are frame-based previews, up to 8 frames / 3 seconds. CPU previews are
bounded to 120 frames / 12 seconds; SAM tracking is bounded to 24 frames. Optical flow
can drift; frame-based diffusion can flicker. MP4 compression does not preserve exact pixels.
Long-form coherent video replacement is not claimed.

Run `python -m unittest discover -s tests -q`, then `node tests/test_frontend_save.cjs`,
`node tests/test_frontend_tools.cjs` and `node tests/test_frontend_video.cjs`.
Unit tests use isolated temporary data and placeholder model filenames; they do not prove
model output quality or download weights. Linux and Windows CPU regression checks are
separate from local hardware render evidence. See `docs/WORKSPACE-DESIGN.txt` for design sources.

Private data, runtime files, model weights and machine-specific configuration are excluded
from this repository. Back up your `data` directory and export projects before moving machines.
