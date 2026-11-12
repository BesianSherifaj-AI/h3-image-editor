"""Portable, local-only configuration. No model downloads at import time."""
import json, os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
_path = Path(os.getenv('STUDIO_CONFIG', str(ROOT / 'studio-config.json')))
SETTINGS = json.loads(_path.read_text('utf-8-sig')) if _path.exists() else {}

def value(name, default):
    return os.getenv('STUDIO_' + name.upper(), SETTINGS.get(name, default))

def local_url(name, default):
    result = str(value(name, default)).rstrip('/')
    url = urlsplit(result)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError(name + ' must be a plain HTTP loopback URL.')
    return result

COMFY_PORT = int(value('comfy_port', 8014))
PORT = int(value('port', 8790))
if not 1024 <= COMFY_PORT <= 65535:
    raise ValueError('comfy_port must be between 1024 and 65535.')
if not 1024 <= PORT <= 65535:
    raise ValueError('port must be between 1024 and 65535.')
COMFY = 'http://127.0.0.1:' + str(COMFY_PORT)
COMFY_HOME = Path.home() / 'ComfyUI-Installs' / 'H3-Image'
COMFY_SRC = Path(value('comfy_source', str(COMFY_HOME / 'ComfyUI'))).expanduser()
PYTHON = Path(value('comfy_python', str(COMFY_HOME / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))).expanduser()
LMSTUDIO = local_url('lmstudio_url', 'http://127.0.0.1:1234')
H3_STUDIO = local_url('h3_url', 'http://127.0.0.1:8766')
PROCESS_FLAGS = 0x08000000 | 0x00004000 if os.name == 'nt' else 0
CPU_ONLY = str(value('cpu_only', 'false')).lower() in ('true', '1', 'yes')
LMS = Path(value('lms_path', str(Path.home() / '.lmstudio/bin' / ('lms.exe' if os.name == 'nt' else 'lms')))).expanduser()
_roots = value('model_roots', [str(ROOT / 'models'), str(Path.home() / 'ComfyUI-Shared/models'), str(Path.home() / 'Documents/ComfyUI/models')])
MODEL_ROOTS = [Path(p).expanduser() for p in (_roots.split(os.pathsep) if isinstance(_roots, str) else _roots)]

def model_paths_file():
    """Preserve a user's existing YAML, otherwise generate explicit model roots."""
    custom = ROOT / 'model-paths.yaml'
    if custom.exists():
        return custom
    target = ROOT / 'runtime' / 'model-paths.generated.yaml'
    target.parent.mkdir(parents=True, exist_ok=True)
    categories = ('diffusion_models', 'text_encoders', 'vae', 'model_patches', 'checkpoints', 'upscale_models')
    blocks = []
    for i, root in enumerate(MODEL_ROOTS):
        blocks.append(f'atelier_{i}:\n  base_path: ' + json.dumps(str(root.resolve()).replace('\\', '/')) + '\n' + ''.join(f'  {key}: {key}\n' for key in categories))
    target.write_text('\n'.join(blocks), encoding='utf-8')
    return target
