"""私有 Skill 上传待审区：只做静态读取，不导入或运行上传的代码。"""
import io
import hashlib
import json
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from governance.checks import check_documentation, check_static_scan
from governance.schema import normalize_skill_meta, validate_frontmatter

MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_FILES = 100
SKILL_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
FORBIDDEN_NAMES = {'.env', '.env.local', 'id_rsa', 'id_ed25519', 'credentials.json', 'secrets.json'}
REVIEW_DECISIONS = {'PRELIMINARY_PASS', 'CHANGES_REQUESTED', 'REJECTED'}
TEXT_SUFFIXES = {'.py', '.md', '.json', '.yaml', '.yml', '.sh', '.txt', '.ps1', '.cmd', '.bat'}


class PackageError(ValueError):
    pass


def _standard_skill_summary(skill_dir: Path) -> str | None:
    """只识别标准 SKILL.md 的身份字段；不推断运行端口或依赖。"""
    path = skill_dir / 'SKILL.md'
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        return None
    text = path.read_text(encoding='utf-8', errors='replace')
    if not text.startswith('---\n'):
        return None
    header = text.split('---', 2)
    if len(header) < 3:
        return None
    values = {}
    for line in header[1].splitlines():
        if ':' in line and not line.startswith((' ', '\t')):
            key, value = line.split(':', 1)
            values[key.strip()] = value.strip().strip('"\'')
    if values.get('name') != skill_dir.name or not values.get('description'):
        return None
    return values['description'][:240]


def _content_digest(skill_dir: Path) -> str:
    """对解压后的相对路径和文件内容建立稳定摘要，不把 ZIP 时间戳当内容。"""
    digest = hashlib.sha256()
    for path in sorted(skill_dir.rglob('*')):
        if path.is_symlink():
            raise PackageError('隔离目录包含符号链接')
        if path.is_file():
            relative = path.relative_to(skill_dir).as_posix().encode('utf-8')
            digest.update(len(relative).to_bytes(4, 'big'))
            digest.update(relative)
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _members(archive: zipfile.ZipFile):
    entries = archive.infolist()
    files = [item for item in entries if not item.is_dir()]
    if not files or len(files) > MAX_FILES:
        raise PackageError('ZIP 必须包含 1–100 个文件')
    if sum(item.file_size for item in files) > MAX_EXPANDED_BYTES:
        raise PackageError('解压后文件总量超过 20 MiB')
    roots, seen = set(), set()
    for item in entries:
        name = item.filename
        path = PurePosixPath(name)
        parts = path.parts
        if not parts or name.startswith('/') or '\\' in name or ':' in name or '.' in parts or '..' in parts:
            raise PackageError('ZIP 含不安全路径')
        if len(parts) < 2 or not SKILL_NAME.fullmatch(parts[0]):
            raise PackageError('ZIP 须只有一个以 Skill 名称命名的顶层目录')
        if name.casefold() in seen:
            raise PackageError('ZIP 包含重复文件名')
        seen.add(name.casefold())
        roots.add(parts[0])
        if stat.S_ISLNK(item.external_attr >> 16):
            raise PackageError('ZIP 不允许符号链接')
        if any(part.lower() in FORBIDDEN_NAMES for part in parts):
            raise PackageError('ZIP 包含疑似密钥文件，已拒绝')
    if len(roots) != 1:
        raise PackageError('ZIP 只能包含一个 Skill 目录')
    return entries, roots.pop()


def static_report(skill_dir: Path) -> dict:
    meta = None
    standard_summary = None
    try:
        meta = normalize_skill_meta(skill_dir)
        issues = validate_frontmatter(skill_dir, meta)
        catalog_ok, catalog_detail = not issues, '; '.join(issues) if issues else 'Schema 与 frontmatter 一致'
    except (ValueError, OSError) as exc:
        catalog_ok, catalog_detail = False, str(exc)
        if not (skill_dir / 'skill.schema.json').exists():
            standard_summary = _standard_skill_summary(skill_dir)
            if standard_summary:
                catalog_detail = '标准 SKILL.md 身份字段可读；缺少平台运行 Schema（输入、输出、资源、版本）'
    scan_ok, scan_detail = check_static_scan(skill_dir)
    docs_ok, docs_detail = check_documentation(skill_dir, meta) if meta else (False, 'Catalog 无效，无法检查文档')
    if standard_summary:
        docs_detail = 'SKILL.md 有名称与描述；缺平台输入输出说明和 skill-card.md 核验'
    return {
        'skill': skill_dir.name, 'version': meta.get('version') if meta else None,
        'status': 'QUARANTINED', 'risk': 'HIGH', 'can_download': False, 'can_run': False,
        'standard_skill_summary': standard_summary,
        'checks': [
            {'name': 'Catalog', 'status': 'PASS' if catalog_ok else 'PARTIAL' if standard_summary else 'FAIL', 'detail': catalog_detail},
            {'name': 'Scanned', 'status': 'PASS' if scan_ok else 'FAIL', 'detail': scan_detail},
            {'name': 'Signed', 'status': 'UNVERIFIED', 'detail': '上传包没有可验证的受信任签名；仓库本地哈希清单不用于自动信任上传包'},
            {'name': 'Evaluated', 'status': 'NOT_RUN', 'detail': '隔离区禁止执行上传代码，未运行样例'},
            {'name': 'Documented', 'status': 'PASS' if docs_ok else 'PARTIAL' if standard_summary else 'FAIL', 'detail': docs_detail},
        ],
        'verdict': '隔离待审；不可下载、加入画布或运行。静态检查通过不代表安全。',
    }


def import_package(root: Path, owner_id: int, payload: bytes, provenance: dict | None = None) -> dict:
    if len(payload) > MAX_ZIP_BYTES:
        raise PackageError('ZIP 大小超过 5 MiB')
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        entries, skill_name = _members(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError('无效 ZIP 文件') from exc
    owner_dir = root / str(owner_id)
    owner_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    destination = owner_dir / upload_id
    with tempfile.TemporaryDirectory(prefix='upload-', dir=owner_dir) as temp:
        temp_path = Path(temp)
        expanded = 0
        for item in entries:
            target = temp_path.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open('xb') as output:
                while block := source.read(1024 * 1024):
                    expanded += len(block)
                    if expanded > MAX_EXPANDED_BYTES:
                        raise PackageError('实际解压大小超过 20 MiB')
                    output.write(block)
        archive.close()
        skill_dir = temp_path / skill_name
        report = static_report(skill_dir)
        record = {'id': upload_id, 'owner_id': owner_id, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'content_sha256': _content_digest(skill_dir), 'entry_state': 'QUARANTINED',
                  'report': report}
        if provenance is not None:
            record['provenance'] = provenance
        (temp_path / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.move(str(temp_path), str(destination))
    return record


def read_upload(root: Path, owner_id: int, upload_id: str) -> dict | None:
    if not re.fullmatch(r'[0-9a-f]{32}', upload_id):
        return None
    path = root / str(owner_id) / upload_id / 'record.json'
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    review_path = path.with_name('review.json')
    record['review'] = json.loads(review_path.read_text(encoding='utf-8')) if review_path.is_file() else {'decision': 'PENDING'}
    revoke_path = path.with_name('revocation.json')
    record['revocation'] = json.loads(revoke_path.read_text(encoding='utf-8')) if revoke_path.is_file() else None
    expected = record.get('content_sha256')
    try:
        actual = _content_digest(path.parent / record['report']['skill']) if expected else None
    except (OSError, PackageError):
        actual = None
    record['content_integrity'] = ('PASS' if actual == expected else 'FAIL') if expected else 'UNVERIFIED'
    decision = record['review'].get('decision', 'PENDING')
    record['workflow_state'] = ('REJECTED' if decision == 'REJECTED' else
                                'APPROVED_FOR_EVALUATION' if decision == 'PRELIMINARY_PASS' else
                                'CHANGES_REQUESTED' if decision == 'CHANGES_REQUESTED' else
                                'REVIEW_REQUIRED')
    if record['content_integrity'] == 'FAIL' and decision == 'PRELIMINARY_PASS':
        record['workflow_state'] = 'REVOKED'
        record['block_reason'] = '初审后内容摘要不一致；自动撤销评估资格'
    elif record['content_integrity'] != 'PASS' and decision == 'PRELIMINARY_PASS':
        record['workflow_state'] = 'REVIEW_REQUIRED'
        record['block_reason'] = '无可复核的导入时内容摘要；须重新导入固定版本'
    elif record['content_integrity'] != 'PASS' and decision != 'REJECTED':
        record['block_reason'] = ('导入后内容摘要不一致；禁止继续初审' if record['content_integrity'] == 'FAIL'
                                  else '无可复核的导入时内容摘要；须重新导入固定版本')
    if record['revocation'] is not None:
        record['workflow_state'] = 'REVOKED'
        record['block_reason'] = '专家已撤销该隔离版本'
    # 不存在安全隔离评估和发布路径时，任何初审状态都不能获得运行权限。
    record['allowed_for_evaluation'] = record['workflow_state'] == 'APPROVED_FOR_EVALUATION'
    record['can_download'] = False
    record['can_run'] = False
    return record


def list_uploads(root: Path, owner_id: int) -> list[dict]:
    owner_dir = root / str(owner_id)
    if not owner_dir.is_dir():
        return []
    return [record for child in sorted(owner_dir.iterdir()) if child.is_dir() if (record := read_upload(root, owner_id, child.name))]


def list_review_queue(root: Path) -> list[dict]:
    """供专家查看待审摘要；不包含原始 ZIP 内容。"""
    if not root.is_dir():
        return []
    records = []
    for owner_dir in sorted(root.iterdir()):
        if owner_dir.is_dir() and owner_dir.name.isdecimal():
            records.extend(list_uploads(root, int(owner_dir.name)))
    return records


def _skill_dir(root: Path, owner_id: int, upload_id: str) -> Path:
    record = read_upload(root, owner_id, upload_id)
    if record is None:
        raise PackageError('上传记录不存在')
    name = record.get('report', {}).get('skill')
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
        raise PackageError('上传记录中的 Skill 名称无效')
    base = (root / str(owner_id) / upload_id).resolve()
    path = (base / name).resolve()
    if path.parent != base or not path.is_dir():
        raise PackageError('待审 Skill 目录不存在')
    return path


def review_files(root: Path, owner_id: int, upload_id: str) -> list[dict]:
    skill_dir = _skill_dir(root, owner_id, upload_id)
    result = []
    for path in sorted(skill_dir.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(skill_dir).as_posix()
        result.append({'path': relative, 'bytes': path.stat().st_size,
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'previewable': path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 256 * 1024})
    return result


def preview_file(root: Path, owner_id: int, upload_id: str, relative: str) -> str:
    skill_dir = _skill_dir(root, owner_id, upload_id)
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith('/') or '\\' in relative or ':' in relative or '..' in parts:
        raise PackageError('文件路径无效')
    raw_path = skill_dir.joinpath(*parts)
    path = raw_path.resolve()
    if raw_path.is_symlink() or skill_dir not in path.parents or not path.is_file():
        raise PackageError('文件不存在或超出待审目录')
    if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 256 * 1024:
        raise PackageError('只允许预览不超过 256 KiB 的文本文件')
    return path.read_text(encoding='utf-8', errors='replace')


def submit_review(root: Path, owner_id: int, upload_id: str, reviewer_id: int, decision: str, reason: str) -> dict:
    record = read_upload(root, owner_id, upload_id)
    if record is None:
        raise PackageError('上传记录不存在')
    if record['workflow_state'] == 'REVOKED':
        raise PackageError('此隔离版本已撤销，不能继续初审；须重新导入新版本')
    if record['review'].get('decision') != 'PENDING':
        raise PackageError('此版本已有初审决定；不能覆盖历史决定')
    if decision == 'PRELIMINARY_PASS' and record['content_integrity'] != 'PASS':
        raise PackageError('隔离文件完整性未通过或历史记录无摘要；不能初审通过，请重新导入')
    if reviewer_id == owner_id:
        raise PackageError('上传者不能复核自己的包')
    if decision not in REVIEW_DECISIONS or not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
        raise PackageError('复核决定无效，理由须为 10–1000 字')
    if decision == 'PRELIMINARY_PASS' and any(item['status'] != 'PASS' for item in record['report']['checks'] if item['name'] in {'Catalog', 'Scanned', 'Documented'}):
        raise PackageError('元数据、静态扫描或文档未通过，不能标记为初审通过')
    review = {'decision': decision, 'reason': reason.strip(), 'reviewer_id': reviewer_id,
              'reviewed_at': datetime.now(timezone.utc).isoformat(),
              'limitation': '仅为人工初审记录；上传包仍隔离，签名和运行样例未验证，不得下载或执行'}
    review_path = root / str(owner_id) / upload_id / 'review.json'
    try:
        with review_path.open('x', encoding='utf-8') as stream:
            json.dump(review, stream, ensure_ascii=False, indent=2)
    except FileExistsError as exc:
        raise PackageError('已存在复核决定；请重新上传新版本，不能覆盖历史决定') from exc
    return review


def revoke_upload(root: Path, owner_id: int, upload_id: str, reviewer_id: int, reason: str) -> dict:
    """不可逆冻结当前隔离版本；不修改历史初审和原始包。"""
    record = read_upload(root, owner_id, upload_id)
    if record is None:
        raise PackageError('上传记录不存在')
    if record['revocation'] is not None:
        raise PackageError('此隔离版本已撤销')
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
        raise PackageError('撤销理由须为 10–1000 字')
    revocation = {'state': 'REVOKED', 'reason': reason.strip(), 'reviewer_id': reviewer_id,
                  'revoked_at': datetime.now(timezone.utc).isoformat(),
                  'content_sha256': record.get('content_sha256'),
                  'limitation': '冻结此版本；不可下载、运行或继续初审。新版本须重新导入。'}
    path = root / str(owner_id) / upload_id / 'revocation.json'
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(revocation, stream, ensure_ascii=False, indent=2)
    except FileExistsError as exc:
        raise PackageError('此隔离版本已撤销') from exc
    return revocation
