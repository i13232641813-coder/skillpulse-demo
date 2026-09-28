"""从明确白名单构建赛事源码包；绝不纳入账号、密钥和运行数据。"""
import hashlib
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '.gitignore', 'LICENSE', 'README.md', 'README-LOCAL.md', 'README-DGX.md',
    'README-STEPFUN-DGX.md', 'DEMO-SCRIPT.md', 'RELEASE-CHECKLIST.md',
    'PROVENANCE.md', 'SUBMISSION.md', 'SUBMISSION-EVIDENCE.md', 'LAST-HOUR-SUBMISSION.md',
    'TEAM-DEMO.md', 'CONTEST-READINESS.md', 'TECHNICAL-ARCHITECTURE.md',
    'TEN-DAY-STORY-DRAFT.md', 'TEAM-SUBMISSION-TEMPLATE.md', 'check_frontend.py',
    'setup.cmd', 'start.cmd', 'start-stepfun.cmd', 'reset-password.cmd',
    'setup-dgx.sh', 'start-dgx.sh',
}
# 运行证据可能包含设备地址、账户或输入摘要；须人工脱敏后单独提交。
TREES = ('backend', 'frontend', 'scripts', 'workspace/skills')
ALLOWED_SUFFIXES = {'.py', '.md', '.json', '.html', '.css', '.js', '.sh', '.cmd', '.ps1',
                    '.cu', '.txt', '.yml', '.yaml', '.example'}
FORBIDDEN_PARTS = {'.venv', '.env', 'outbox', '__pycache__', '.git',
                   'id_rsa', 'id_ed25519', 'credentials.json', 'secrets.json'}


def package_files():
    files = [ROOT / name for name in ROOT_FILES]
    for relative in TREES:
        directory = ROOT / relative
        if not directory.is_dir():
            raise RuntimeError(f'必需目录缺失：{relative}')
        files.extend(path for path in directory.rglob('*') if path.is_file())
    selected = []
    for path in sorted(set(files)):
        relative = path.relative_to(ROOT)
        if (path.is_symlink() or path.name.lower().startswith('.env') or
                any(part.lower() in FORBIDDEN_PARTS for part in relative.parts)):
            continue
        if path.suffix.lower() == '.sig':
            continue  # 历史占位签名不能作为可验证签名提交。
        if path.name not in ROOT_FILES and path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise RuntimeError(f'发现未审查文件类型，停止打包：{relative}')
        if path.name.lower() in {'登录信息表.xlsx', 'spark云节点访问与使用手册.docx'}:
            raise RuntimeError(f'敏感附件不能进入提交包：{relative}')
        selected.append((path, relative.as_posix()))
    for name in ROOT_FILES:
        if not (ROOT / name).is_file():
            raise RuntimeError(f'必需文件缺失：{name}')
    return selected


def main():
    output = ROOT / 'dist' / 'SkillPulse-submission-20260928-v15.zip'
    if output.exists():
        raise RuntimeError('提交包已存在；请先人工确认版本，不自动覆盖')
    files = package_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path, name in files:
                archive.write(path, name)
        with zipfile.ZipFile(output) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise RuntimeError(f'提交包 CRC 校验失败：{bad}')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f'{output.name}: {len(files)} files, {output.stat().st_size} bytes, SHA256 {hashlib.sha256(output.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
