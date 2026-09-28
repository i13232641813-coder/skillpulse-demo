"""可选 CUDA 计算冒烟测试：只证明一次真实 GPU 矩阵计算，不是模型推理。"""
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    try:
        import torch
    except ImportError:
        print(json.dumps(cuda_smoke(), ensure_ascii=False))
        return
    if not torch.cuda.is_available():
        print(json.dumps({'ok': False, 'reason': 'PyTorch 未检测到可用 CUDA 设备'}))
        return

    # 固定规模与种子，防止证据采集占用共享节点大量显存。
    torch.manual_seed(7)
    device = torch.device('cuda:0')
    left = torch.rand((1024, 1024), device=device)
    right = torch.rand((1024, 1024), device=device)
    torch.cuda.synchronize(device)
    started = time.perf_counter()
    result = left @ right
    torch.cuda.synchronize(device)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    checksum = round(float(result[:8, :8].sum().item()), 5)
    print(json.dumps({
        'ok': True,
        'device': torch.cuda.get_device_name(device),
        'torch_version': torch.__version__,
        'matrix_shape': [1024, 1024],
        'elapsed_ms': elapsed_ms,
        'result_checksum': checksum,
        'kind': 'CUDA matrix multiplication; not model inference',
    }, ensure_ascii=False))


def cuda_smoke():
    """PyTorch 不可用时，使用节点现有 CUDA Toolkit；不安装系统软件。"""
    compiler = shutil.which('nvcc')
    if not compiler:
        return {'ok': False, 'reason': '当前 Python 无 PyTorch，系统也没有 nvcc'}
    source = Path(__file__).with_name('cuda_smoke.cu')
    if not source.is_file():
        return {'ok': False, 'reason': '缺少 CUDA 冒烟测试源码'}
    try:
        query = subprocess.run(
            ['nvidia-smi', '--query-gpu=compute_cap', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=10, check=False,
        )
        match = re.fullmatch(r'\s*(\d+)\.(\d+)\s*', query.stdout.splitlines()[0]) if query.stdout else None
        if query.returncode != 0 or not match:
            return {'ok': False, 'reason': '无法确认 CUDA 设备计算能力'}
        arch = f'sm_{match.group(1)}{match.group(2)}'
        with tempfile.TemporaryDirectory(prefix='skillpulse-cuda-') as folder:
            executable = str(Path(folder) / 'cuda-smoke')
            build = subprocess.run(
                [compiler, '-O2', '-arch=' + arch, str(source), '-o', executable],
                capture_output=True, text=True, timeout=75, check=False,
            )
            if build.returncode != 0:
                return {'ok': False, 'reason': 'CUDA 编译失败', 'compiler_exit': build.returncode}
            execution = subprocess.run([executable], capture_output=True, text=True, timeout=30, check=False)
            if execution.returncode != 0:
                return {'ok': False, 'reason': 'CUDA 运算或结果校验失败', 'execution_exit': execution.returncode}
            result = json.loads(execution.stdout.strip())
            return result if isinstance(result, dict) and result.get('ok') is True else {'ok': False, 'reason': 'CUDA 输出无效'}
    except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
        return {'ok': False, 'reason': 'CUDA 冒烟测试不可用或超时'}


if __name__ == '__main__':
    main()
