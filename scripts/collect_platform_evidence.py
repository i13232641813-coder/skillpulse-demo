"""记录实际设备与本地双节点检查；不会把普通 GPU 伪称为 DGX Spark。"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from engine.run_local import run_skill  # noqa: E402
from gpu_semantic import SemanticUnavailable, rank as semantic_rank  # noqa: E402


def command_output(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'ok': False, 'output': str(exc)}
    return {'ok': result.returncode == 0, 'output': (result.stdout or result.stderr).strip()[:3000]}


def gpu_smoke_result():
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'gpu_smoke.py')],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0:
            return {'ok': False, 'reason': 'GPU 冒烟测试子进程失败', 'exit_code': result.returncode}
        payload = json.loads(result.stdout.strip())
        return payload if isinstance(payload, dict) and isinstance(payload.get('ok'), bool) else {'ok': False, 'reason': 'GPU 冒烟测试输出无效'}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {'ok': False, 'reason': 'GPU 冒烟测试不可用或超时'}


def main():
    parser = argparse.ArgumentParser(description='Capture observed hardware and a real local SkillPulse check')
    parser.add_argument('--target', default='rag-blueprint', help='Existing local Skill directory name')
    parser.add_argument('--output', required=True, type=Path, help='New JSON evidence file; never overwritten')
    parser.add_argument('--gpu-smoke', action='store_true', help='Run one bounded PyTorch CUDA matrix multiplication if available')
    parser.add_argument('--semantic-query', default='', help='Execute real local CUDA embedding inference and record its ranking')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出文件已存在；请选择新文件名，避免覆盖历史证据')
    semantic = None
    if args.semantic_query:
        catalog = json.loads((ROOT / 'backend' / 'catalog' / 'skills.json').read_text(encoding='utf-8'))
        candidates = [item for item in catalog if item.get('source') in {'local_project', 'local_sample'}
                      and (ROOT / 'workspace' / 'skills' / item['name']).is_dir()]
        try:
            measured = semantic_rank(args.semantic_query, candidates)
        except SemanticUnavailable as exc:
            parser.error('语义模型未能在 CUDA 上实际运行：' + str(exc))
        model_dir = Path(os.environ['SKILLPULSE_EMBEDDING_MODEL_DIR'])
        config_path = model_dir / 'config.json'
        semantic = {**measured,
                    'query_sha256': hashlib.sha256(args.semantic_query.encode('utf-8')).hexdigest(),
                    'model_config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.is_file() else None}
    doctor = run_skill('skill-doctor', {'query': args.target})
    if not doctor.get('ok'):
        parser.error('skill-doctor 运行失败：' + str(doctor.get('error')))
    report = run_skill('skill-report', {'result': doctor['outputs']['result']})
    if not report.get('ok'):
        parser.error('skill-report 运行失败：' + str(report.get('error')))
    measured = json.loads(doctor['outputs']['result'])
    gpu = command_output(['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'])
    gpu_test = gpu_smoke_result() if args.gpu_smoke else {'ok': False, 'reason': '未请求 GPU 冒烟测试'}
    evidence = {
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'device_observed': {'system': platform.platform(), 'machine': platform.machine(), 'gpu_query': gpu},
        'dgx_spark_verified': False,
        'dgx_spark_note': '此脚本只记录观察到的设备，不独立认证机器型号。请另附 DGX Spark 设备与部署证据。',
        'gpu_workload_executed': bool(gpu_test.get('ok')),
        'gpu_smoke': gpu_test,
        'gpu_note': '安检本身使用 CPU；CUDA 冒烟不是模型推理。semantic_search 非空时才表示另行执行了本地向量模型推理。',
        'semantic_inference_executed': semantic is not None,
        'semantic_search': semantic,
        'workflow': ['skill-doctor', 'skill-report'],
        'target_skill': args.target,
        'target_status': measured['status'],
        'target_risk': measured['risk'],
        'checks': measured['checks'],
        'doctor_elapsed_ms': doctor.get('elapsed_ms'),
        'report_elapsed_ms': report.get('elapsed_ms'),
        'report_sha256': hashlib.sha256(report['outputs']['report'].encode('utf-8')).hexdigest(),
        'limitations': ['本地 SHA-256 allowlist 不是公钥数字签名', 'smoke case 不证明生产能力',
                        '不是安全沙箱或第三方合规认证'],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Evidence saved: {args.output}")
    print(f"Observed GPU: {gpu['output'] or 'unavailable'}")
    print(f"Target: {args.target} | {measured['status']} | {measured['risk']}")


if __name__ == '__main__':
    main()
