# Optional local models

The CPU editor works without model weights. AI tools use a separately installed
ComfyUI runtime; install only the engines you need. The Connections panel reports
missing files. Exact filenames and workflow nodes are defined in
`backend/workflows.py` and `backend/helpers.py`.

- [MiniMax H3 Image](https://www.minimax.io/blog/minimax-h3-image): generation,
  reference edits and masked repair. This is distinct from H3 video generation.
- [Z-Image Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo): fast generation.
  [Union 2.1 ControlNet](https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1)
  adds visual guidance.
- [Qwen Image 2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1): reference editing
  and transparency workflows. Check its qwen-research license; do not assume
  unrestricted commercial use.
- [Qwen Image Layered](https://huggingface.co/Comfy-Org/Qwen-Image-Layered): approximate
  editable RGBA decomposition; reconstruction is output index zero.
- [SAM 3.1](https://huggingface.co/Comfy-Org/sam3.1): object selection and tracking.
  Check the upstream SAM license.
- Qwen3VL vision through ComfyUI: planning and bounded result critique. A compatible
  loaded vision model in [LM Studio](https://lmstudio.ai/docs/developer/rest) is an
  optional planning backend.
- [SeedVR2](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/utility_seedvr2_3b_int8_upscale_image.json):
  optional AI enhancement using native Comfy nodes.

The local Windows hardware checks used ComfyUI 0.45.0 at commit
`0228a9d73cd7c2daf8f61fe13d56b12efe307171`, Torch 2.9.1+cu130 and an RTX 4090.
Other hardware, revisions and quantizations need their own validation. No weights
are distributed here, and application licensing does not grant model rights.
