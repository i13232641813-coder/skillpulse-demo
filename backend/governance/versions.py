"""本地 Skill 源码快照。内容哈希用于检错，不等同发布者数字签名。"""
import hashlib
import io
import os
import re
import zipfile
from pathlib import Path

MAX_SOURCE_BYTES = 20 * 1024 * 1024


def build_snapshot(skill_dir: Path) -> tuple[bytes, str, list[dict]]:
    files = []
    total = 0
    root = skill_dir.resolve()
    for path in sorted(skill_dir.rglob('*')):
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError(f'拒绝快照符号链接: {path.name}')
        if not path.is_file():
            continue
        if path.resolve().is_relative_to(root) is False:
            raise ValueError('快照文件超出 Skill 目录')
        lower_name = path.name.lower()
        if lower_name.startswith('.env') or lower_name in {'credentials.json', 'secrets.json', 'token.json'} or path.suffix.lower() in {'.key', '.pem', '.p12', '.pfx'}:
            raise ValueError(f'疑似凭证文件不允许进入快照: {path.name}')
        total += path.stat().st_size
        if total > MAX_SOURCE_BYTES:
            raise ValueError('Skill 源码快照超过 20 MiB 上限')
        files.append(path)
    if not files:
        raise ValueError('Skill 目录没有可快照的源码文件')
    buffer = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            data = path.read_bytes()
            relative = path.relative_to(skill_dir).as_posix()
            item = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o644 << 16
            archive.writestr(item, data)
            manifest.append({'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    content = buffer.getvalue()
    return content, hashlib.sha256(content).hexdigest(), manifest


def snapshot_path(directory: Path, name: str, version: str, digest: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', name):
        raise ValueError('无效 Skill 名称')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', version):
        raise ValueError('版本必须符合 SemVer 格式')
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('无效快照哈希')
    return directory / 'snapshots' / f'{name}-{version}-{digest}.zip'


def store_snapshot(directory: Path, name: str, version: str, content: bytes, digest: str) -> Path:
    path = snapshot_path(directory, name, version, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('同名快照文件已存在但内容不匹配，拒绝覆盖')
        return path
    created = False
    try:
        with path.open('xb') as handle:
            created = True
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if created and path.exists():
            path.unlink()  # 仅清理本次新建但写入失败的文件，不处理已有快照。
        raise
    return path


def verify_snapshot(directory: Path, name: str, record: dict) -> bool:
    try:
        digest = record['snapshot_sha256']
        path = snapshot_path(directory, name, record['version'], digest)
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except (KeyError, TypeError, ValueError, OSError):
        return False
