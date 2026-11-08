"""Project-local runtime selection; never install or download a model implicitly."""
import json
import os
from pathlib import Path


MODEL = 'mlx-community/whisper-large-v3-turbo'
CONFIG = Path(os.environ.get('VIDEO_WORKFLOW_CONFIG', Path(__file__).resolve().parents[3] / 'local.json'))


def load_runtime():
    if not CONFIG.is_file():
        raise ValueError('可选 MLX 转写尚未初始化；可选择其他转写工具，或通过安装包执行 init --local --prepare；剪辑无需此环境')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))['transcription']
    if config.get('engine') != 'mlx_whisper':
        raise ValueError('内置识别命令仅支持 mlx_whisper；其他转写工具请直接提供产物')
    python = Path(config['python']).expanduser().absolute()
    # Keep the venv path: resolving its python symlink would lose that environment.
    if not python.is_file():
        raise ValueError('现有 MLX Python 环境已移动，请更新 video-shared/local.json')
    model = Path(config['model_path']).expanduser().resolve()
    if not all((model / name).is_file() for name in ('config.json', 'weights.safetensors')):
        raise ValueError('本地 MLX Turbo 模型不完整；不自动下载，请检查 local.json')
    return {**config, 'python': str(python), 'model_path': str(model)}
