"""可选 DGX CUDA 语义排序；没有真实本地模型/GPU 时绝不伪报成功。"""
import os
import time
from functools import lru_cache
from pathlib import Path


class SemanticUnavailable(Exception):
    pass


MODEL_ID = 'intfloat/multilingual-e5-small'


def _model_path() -> Path:
    raw = os.environ.get('SKILLPULSE_EMBEDDING_MODEL_DIR', '').strip()
    if not raw:
        raise SemanticUnavailable('未配置本地向量模型目录')
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise SemanticUnavailable('本地向量模型目录不存在')
    return path


def _cuda():
    try:
        import torch
    except ImportError:
        raise SemanticUnavailable('当前环境未安装 CUDA 版 PyTorch') from None
    if not torch.cuda.is_available():
        raise SemanticUnavailable('当前 Python 环境不可使用 CUDA GPU')
    return torch


def status() -> dict:
    try:
        path = _model_path()
        torch = _cuda()
        _load_model(str(path))
        return {'available': True, 'device': torch.cuda.get_device_name(0),
                'model': path.name, 'recommended_model': MODEL_ID, 'mode': 'local-cuda-embedding'}
    except (SemanticUnavailable, ImportError, OSError, RuntimeError, ValueError) as exc:
        return {'available': False, 'device': None, 'model': MODEL_ID,
                'mode': 'unavailable', 'reason': str(exc) if isinstance(exc, SemanticUnavailable) else '本地 CUDA 模型未能加载'}


@lru_cache(maxsize=1)
def _load_model(path: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(path, device='cuda', local_files_only=True, trust_remote_code=False)


def rank(query: str, candidates: list[dict]) -> dict:
    path = _model_path()
    torch = _cuda()
    if not candidates:
        return {'results': [], 'model': path.name, 'device': torch.cuda.get_device_name(0), 'elapsed_ms': 0}
    try:
        model = _load_model(str(path))
        passages = ['passage: ' + ' '.join([item['name'], item.get('use', ''), item.get('summary', '')])
                    for item in candidates]
        started = time.monotonic()
        vectors = model.encode(['query: ' + query] + passages, normalize_embeddings=True,
                               convert_to_numpy=True, show_progress_bar=False)
        torch.cuda.synchronize()
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        query_vector = vectors[0]
        ranked = sorted(((item, float(query_vector @ vectors[index + 1]))
                         for index, item in enumerate(candidates)), key=lambda pair: pair[1], reverse=True)
    except (OSError, RuntimeError, ValueError, ImportError):
        raise SemanticUnavailable('本地 CUDA 向量模型加载或推理失败；请检查模型文件和驱动') from None
    return {'results': [{'name': item['name'], 'score': round(score, 4)} for item, score in ranked],
            'model': path.name, 'recommended_model': MODEL_ID,
            'device': torch.cuda.get_device_name(0), 'elapsed_ms': elapsed_ms,
            'mode': 'local-cuda-embedding'}
