"""受限子进程 Skill runner。注意：不是 OS 级安全沙箱。"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / 'workspace' / 'skills'
RUNNER_TIMEOUT_SECONDS = max(1, int(os.environ.get('SKILLPULSE_RUNNER_TIMEOUT', '10')))
RUNNER_MEMORY_MB = int(os.environ.get('SKILLPULSE_RUNNER_MEMORY_MB', '256'))  # 仅展示配置；Windows 未强制内存上限。
WORKER_PATH = Path(__file__).with_name('runner_worker.py')


def _validate_ports(meta: Dict[str, Any]) -> Dict[str, Any]:
    bad = []
    for item in meta.get('inputs', []) + meta.get('outputs', []):
        if item.get('type') not in {'string', 'image', 'json', 'file', 'text'}:
            bad.append(item.get('name'))
    return {'ok': not bool(bad), 'bad_ports': bad}


def run_skill(skill_name: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    started = time.monotonic()
    skill_dir = (SKILLS_DIR / skill_name).resolve()
    if skill_dir.parent != SKILLS_DIR.resolve() or not skill_dir.is_dir():
        return _failure(skill_name, inputs, 'missing or invalid skill directory', started)
    if skill_name == 'nvidia-skill-card-validator':
        # 唯一经人工界定的外部动作；不允许将任意上传代码送入此分支。
        from engine.curated_card_validator import run_card_validator
        return run_card_validator(inputs)
    runner = skill_dir / 'run_local.py'
    if not runner.is_file():
        return _failure(skill_name, inputs, 'missing local runner', started)

    # 不向 Skill 继承 API Key 等父进程变量；只保留 Python 在 Windows 启动子进程所需的环境。
    env = {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
    for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'):
        if os.environ.get(key):
            env[key] = os.environ[key]
    request = json.dumps({'skill_dir': str(skill_dir), 'inputs': inputs if isinstance(inputs, dict) else {}}, ensure_ascii=False)

    try:
        with tempfile.TemporaryDirectory(prefix='skillpulse-run-') as workdir:
            completed = subprocess.run(
                [sys.executable, '-I', '-X', 'utf8', str(WORKER_PATH)],
                input=request,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                cwd=workdir,
                env=env,
                timeout=RUNNER_TIMEOUT_SECONDS,
                check=False,
            )
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-1200:] or f'runner exited with code {completed.returncode}'
            return _failure(skill_name, inputs, detail, started)
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            return _failure(skill_name, inputs, 'runner returned invalid JSON object', started)
        result.setdefault('skill', skill_name)
        result.setdefault('inputs', inputs)
        result.setdefault('outputs', {})
        result.setdefault('artifacts', [])
        result.setdefault('elapsed_ms', int((time.monotonic() - started) * 1000))
        result['resource'] = {
            'timeout_seconds': RUNNER_TIMEOUT_SECONDS,
            'memory_limit_enforced': False,
            'isolation': 'restricted subprocess; not an OS security sandbox',
        }
        return result
    except subprocess.TimeoutExpired:
        return _failure(skill_name, inputs, f'runner timed out after {RUNNER_TIMEOUT_SECONDS}s', started)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return _failure(skill_name, inputs, str(exc), started)


def _failure(skill_name: str, inputs: Any, error: str, started: float) -> Dict[str, Any]:
    return {
        'ok': False,
        'skill': skill_name,
        'status': 'failed',
        'inputs': inputs,
        'outputs': {},
        'artifacts': [],
        'error': error,
        'elapsed_ms': int((time.monotonic() - started) * 1000),
        'resource': {
            'timeout_seconds': RUNNER_TIMEOUT_SECONDS,
            'memory_limit_enforced': False,
            'isolation': 'restricted subprocess; not an OS security sandbox',
        },
    }
