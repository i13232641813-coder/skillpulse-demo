"""固定上游脚本的受控运行适配；只开放 Skill Card 标记校验这一个动作。

此处不是任意第三方 Skill 的执行入口，也不是生产级沙箱或发布者验签。
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


ACTION_NAME = 'nvidia-skill-card-validator'
SOURCE_REPO = 'NVIDIA/skills'
SOURCE_COMMIT = 'd8519c57da6db5d9bea274ec1724a4a7a56a3dee'
SOURCE_PATH = 'skills/skill-card-generator/scripts/validate_submission.py'
SOURCE_SHA256 = '27df79512570edc4965830a74c4082a5604d89f7fac0ec48c7230fb5fa7cdfb9'
IMAGE_ID = 'sha256:45c9fe0b487403c02a757ba69fbcb70476e3cf6d0d6e6943049b240cfe4ce5f2'
IMAGE_TAG = 'skillpulse/empty-python-host:20260928'
MAX_CARD_BYTES = 32 * 1024
TIMEOUT_SECONDS = 10


def _source_path() -> Path:
    root = Path(os.environ.get('SKILLPULSE_OUTBOX_DIR', str(Path(__file__).resolve().parents[2] / 'workspace' / 'outbox')))
    return root / 'curated' / 'validate_submission.py'


def _safe_env() -> dict[str, str]:
    return {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'DOCKER_CONFIG': '/nonexistent'}


def availability() -> tuple[bool, str]:
    """检查固定脚本及固定镜像；不能用文件存在或标签冒充验证。"""
    if sys.platform != 'linux':
        return False, '仅已验证的 Linux/DGX 容器环境可执行；Windows 本地不运行外部脚本'
    if shutil.which('docker') is None:
        return False, 'Docker 不可用'
    source = _source_path()
    try:
        data = source.read_bytes()
    except OSError:
        return False, '固定来源脚本未准备；请先运行受控安装步骤'
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        return False, '固定来源脚本 SHA-256 不匹配，禁止执行'
    try:
        image = subprocess.run(
            ['docker', 'image', 'inspect', IMAGE_TAG, '--format', '{{.Id}}'],
            capture_output=True, text=True, timeout=5, env=_safe_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, '无法检查固定容器镜像'
    if image.returncode != 0 or image.stdout.strip() != IMAGE_ID:
        return False, '容器镜像 ID 不匹配或不存在，禁止执行'
    return True, '固定脚本 SHA-256 与容器镜像 ID 均匹配；仅允许受控动作'


def run_card_validator(inputs: dict) -> dict:
    """使用无网络、无密钥、只读挂载、非特权用户及资源限制运行固定脚本。"""
    started = time.monotonic()
    card = inputs.get('card') if isinstance(inputs, dict) else None
    if not isinstance(card, str) or not card.strip():
        return _failure('card 必须是非空 Markdown 文本', started)
    if len(card.encode('utf-8')) > MAX_CARD_BYTES:
        return _failure('card 超过 32 KiB 限制', started)
    ready, reason = availability()
    if not ready:
        return _failure(reason, started)
    try:
        source_bytes = _source_path().read_bytes()
    except OSError:
        return _failure('固定来源脚本在检查后不可读，禁止执行', started)
    # 防止检查与挂载之间来源文件被替换。
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        return _failure('固定来源脚本在检查后发生变化，禁止执行', started)
    name = 'skillpulse-card-' + uuid.uuid4().hex[:16]
    with tempfile.TemporaryDirectory(prefix='skillpulse-card-') as temp:
        folder = Path(temp)
        script = folder / 'validator.py'
        document = folder / 'card.md'
        script.write_bytes(source_bytes)
        document.write_text(card, encoding='utf-8')
        folder.chmod(0o755)
        script.chmod(0o644)
        document.chmod(0o644)
        command = [
            'docker', 'run', '--rm', '--pull', 'never', '--name', name,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--pids-limit', '16',
            '--memory', '256m', '--cpus', '0.5', '--ulimit', 'nofile=64:64',
            '--user', '65534:65534',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m',
            '--mount', 'type=bind,src=/usr,dst=/usr,readonly',
            '--mount', 'type=bind,src=/lib,dst=/lib,readonly',
            '--mount', f'type=bind,src={script},dst=/skill/validator.py,readonly',
            '--mount', f'type=bind,src={document},dst=/input/card.md,readonly',
            IMAGE_ID, '/usr/bin/python3', '-I', '-S',
            '/skill/validator.py', '/input/card.md',
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=TIMEOUT_SECONDS + 2, env=_safe_env(), check=False,
            )
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True,
                               timeout=5, env=_safe_env(), check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return _failure('固定脚本运行超时；已尝试强制终止容器，需检查残留容器状态', started)
        except OSError:
            return _failure('无法启动固定容器', started)
    if completed.returncode != 0:
        if completed.returncode == 1:
            return _failure('Skill Card 仍有待人工处理标记：' + completed.stderr.strip()[:1000], started)
        return _failure('容器或固定脚本执行失败（退出码 ' + str(completed.returncode) + '）', started)
    return {
        'ok': True, 'skill': ACTION_NAME, 'status': 'completed',
        'outputs': {'validation': 'PASS', 'source_commit': SOURCE_COMMIT},
        'artifacts': [], 'elapsed_ms': int((time.monotonic() - started) * 1000),
        'source': {'repository': SOURCE_REPO, 'commit': SOURCE_COMMIT,
                   'path': SOURCE_PATH, 'sha256': SOURCE_SHA256,
                   'publisher_signature_verified': False},
        'resource': {'network': 'none', 'memory_mib': 256, 'cpu': 0.5,
                     'timeout_seconds': TIMEOUT_SECONDS,
                     'isolation': 'bounded Linux container prototype; not a production sandbox'},
    }


def _failure(message: str, started: float) -> dict:
    return {'ok': False, 'skill': ACTION_NAME, 'status': 'failed', 'outputs': {},
            'artifacts': [], 'error': message,
            'elapsed_ms': int((time.monotonic() - started) * 1000),
            'source': {'repository': SOURCE_REPO, 'commit': SOURCE_COMMIT,
                       'path': SOURCE_PATH, 'sha256': SOURCE_SHA256,
                       'publisher_signature_verified': False}}
