"""可复现的本地 Skill 检查；这是演示基线，不是完整恶意代码防护。"""
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from engine.run_local import run_skill
from governance.schema import normalize_skill_meta, validate_frontmatter

ROOT = Path(__file__).resolve().parents[2]
TRUST_MANIFEST = ROOT / 'backend' / 'catalog' / 'trusted_skill_hashes.json'
IGNORED_FILES = {'skill.oms.sig'}  # 历史占位文件从不被当成签名。


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def check_integrity(skill_dir: Path) -> Tuple[bool, str]:
    """按仓库内维护的 SHA-256 allowlist 做本地完整性比对，不声称是数字签名。"""
    try:
        manifest = json.loads(TRUST_MANIFEST.read_text(encoding='utf-8'))
        expected = manifest.get('skills', {}).get(skill_dir.name)
        if not isinstance(expected, dict) or not expected:
            return False, '未登记在本地可信哈希清单；未签名/无法验证'
        actual_paths = {
            path.relative_to(skill_dir).as_posix()
            for path in skill_dir.rglob('*')
            if path.is_file()
            and '__pycache__' not in path.parts
            and path.suffix != '.pyc'
            and path.name not in IGNORED_FILES
        }
        if actual_paths != set(expected):
            added = sorted(actual_paths - set(expected))
            missing = sorted(set(expected) - actual_paths)
            return False, f'可信哈希清单文件集合不一致；新增={added} 缺失={missing}'
        for relative, expected_hash in expected.items():
            path = (skill_dir / relative).resolve()
            if skill_dir.resolve() not in path.parents:
                return False, '哈希清单包含越界路径'
            if _hash(path) != expected_hash:
                return False, f'完整性校验失败: {relative}'
        return True, '与仓库本地 SHA-256 可信清单一致（非公钥数字签名）'
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, f'无法读取或验证本地可信哈希清单: {exc}'


def check_static_scan(skill_dir: Path) -> Tuple[bool, str]:
    """扫描 Python AST 与 shell 文本中的少量高风险模式，规则公开且可重复。"""
    findings: List[str] = []
    for path in sorted(skill_dir.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        relative = path.relative_to(skill_dir).as_posix()
        if path.suffix == '.py':
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
            except (OSError, SyntaxError, UnicodeError) as exc:
                findings.append(f'{relative}: Python 语法/编码检查失败 ({exc})')
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
                    if name in {'eval', 'exec', 'system', 'popen', 'Popen', 'run', 'call'}:
                        findings.append(f'{relative}:{getattr(node, "lineno", 0)}: 检测到受限调用 {name}()')
        elif path.suffix in {'.sh', '.bat', '.cmd', '.ps1'}:
            text = path.read_text(encoding='utf-8', errors='replace').lower()
            for marker in ('curl | sh', 'wget | sh', 'rm -rf /', 'format c:', 'invoke-expression'):
                if marker in text:
                    findings.append(f'{relative}: 检测到受限 shell 模式 {marker}')
    if findings:
        return False, '基础规则命中：' + '; '.join(findings[:8])
    return True, 'Python AST 语法与危险调用清单、shell 危险命令清单通过；非完整恶意代码检测'


def check_evaluation(skill_dir: Path, meta: Dict[str, Any], integrity_ok: bool, scan_ok: bool) -> Tuple[bool, str]:
    """仅对完整性与静态检查通过的 Skill 执行 evals 中可运行的样例。"""
    if not integrity_ok or not scan_ok:
        return False, '未执行样例：完整性或基础静态检查未通过'
    eval_path = skill_dir / 'evals' / 'evals.json'
    if not eval_path.is_file():
        return False, '缺少 evals/evals.json 样例定义'
    try:
        eval_data = json.loads(eval_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, f'样例定义无法解析: {exc}'
    cases = eval_data.get('smoke_cases') if isinstance(eval_data, dict) else None
    if not isinstance(cases, list) or not cases:
        return False, 'evals/evals.json 没有可执行的 smoke_cases；旧 verdict 字段不作为通过依据'
    declared_outputs = {port['name'] for port in meta['outputs']}
    required_inputs = {port['name'] for port in meta['inputs'] if port['required']}
    for index, case in enumerate(cases, start=1):
        case_id = case.get('id', f'case-{index}') if isinstance(case, dict) else f'case-{index}'
        inputs = case.get('inputs') if isinstance(case, dict) else None
        expected_keys = case.get('expected_output_keys') if isinstance(case, dict) else None
        if not isinstance(inputs, dict) or not isinstance(expected_keys, list) or not expected_keys:
            return False, f'{case_id}: 样例必须提供 inputs 与 expected_output_keys'
        if required_inputs - set(inputs):
            return False, f'{case_id}: 样例缺少必填输入 {", ".join(sorted(required_inputs - set(inputs)))}'
        result = run_skill(skill_dir.name, inputs)
        if not result.get('ok'):
            return False, f'{case_id}: 样例运行失败: {result.get("error") or result.get("status")}'
        outputs = result.get('outputs') or {}
        if not isinstance(outputs, dict):
            return False, f'{case_id}: runner 未返回 outputs 对象'
        missing = (declared_outputs | set(expected_keys)) - set(outputs)
        if missing:
            return False, f'{case_id}: 样例运行缺少输出 {", ".join(sorted(missing))}'
        equals = case.get('expected_equals', {})
        contains = case.get('expected_contains', {})
        if not isinstance(equals, dict) or not isinstance(contains, dict):
            return False, f'{case_id}: 样例断言格式无效'
        for key, expected in equals.items():
            if outputs.get(key) != expected:
                return False, f'{case_id}: 输出 {key} 不符合精确断言'
        for key, expected in contains.items():
            if not isinstance(expected, str) or expected not in str(outputs.get(key, '')):
                return False, f'{case_id}: 输出 {key} 不包含预期内容'
    if skill_dir.name == 'nvidia-skill-card-validator':
        return True, f'在固定 Linux 容器中实际执行并通过 {len(cases)} 个上游脚本动作样例；不代表完整上游 Skill 已评估'
    return True, f'实际执行并通过 {len(cases)} 个 smoke_cases；旧 verdict 字段未参与判定（部分 runner 仍为模拟能力）'


def check_documentation(skill_dir: Path, meta: Dict[str, Any]) -> Tuple[bool, str]:
    card = skill_dir / 'skill-card.md'
    skill_md = skill_dir / 'SKILL.md'
    missing = [name for name, path in (('SKILL.md', skill_md), ('skill-card.md', card)) if not path.is_file()]
    if missing:
        return False, '缺少文档: ' + ', '.join(missing)
    issues = validate_frontmatter(skill_dir, meta)
    for direction in ('inputs', 'outputs'):
        if not meta.get(direction):
            issues.append(f'Schema 未声明 {direction}')
    if issues:
        return False, '; '.join(issues)
    return True, 'SKILL.md/frontmatter、Skill Card 与 Schema 输入输出说明齐全'


def skill_health(skill_dir: Path) -> Dict[str, Any]:
    checks = []
    meta = None
    try:
        meta = normalize_skill_meta(skill_dir)
        issues = validate_frontmatter(skill_dir, meta)
        catalog_ok = not issues
        catalog_detail = 'Skill Schema 字段及 frontmatter 一致' if catalog_ok else '; '.join(issues)
    except ValueError as exc:
        catalog_ok = False
        catalog_detail = str(exc)
    checks.append({'name': 'Catalog', 'status': 'PASS' if catalog_ok else 'FAIL', 'detail': catalog_detail})

    scan_ok, scan_detail = check_static_scan(skill_dir)
    checks.append({'name': 'Scanned', 'status': 'PASS' if scan_ok else 'FAIL', 'detail': scan_detail})

    integrity_ok, integrity_detail = check_integrity(skill_dir)
    checks.append({'name': 'Signed', 'status': 'PASS' if integrity_ok else 'FAIL', 'detail': integrity_detail})

    if meta is not None:
        eval_ok, eval_detail = check_evaluation(skill_dir, meta, integrity_ok, scan_ok)
    else:
        eval_ok, eval_detail = False, '未执行样例：Catalog Schema 无效'
    checks.append({'name': 'Evaluated', 'status': 'PASS' if eval_ok else 'FAIL', 'detail': eval_detail})

    if meta is not None:
        docs_ok, docs_detail = check_documentation(skill_dir, meta)
    else:
        docs_ok, docs_detail = False, '未通过：Catalog Schema 无效'
    checks.append({'name': 'Documented', 'status': 'PASS' if docs_ok else 'FAIL', 'detail': docs_detail})

    status = 'HEALTHY' if all(item['status'] == 'PASS' for item in checks) else 'UNHEALTHY'
    integrity_failed = not integrity_ok
    scan_failed = not scan_ok
    # 没有上游身份和公钥签名时，即使本地门禁通过也不能标为低风险。
    risk = 'MED' if status == 'HEALTHY' else 'HIGH' if integrity_failed or scan_failed else 'MED'
    failed = [item['name'] for item in checks if item['status'] != 'PASS']
    provenance_note = ('固定 NVIDIA/skills Git 提交及脚本 SHA-256 与本地审阅版本匹配；未验证发布者公钥签名。'
                       '这里只发布单一验证动作，不是完整上游 Skill。') if skill_dir.name == 'nvidia-skill-card-validator' else (
                       '仅核对仓库本地 SHA-256 清单；未验证上游发布者身份或公钥数字签名')
    return {
        'skill': skill_dir.name,
        'version': meta.get('version') if meta else None,
        'schema': meta,
        'status': status,
        'trust': 'LOCAL' if status == 'HEALTHY' else 'LOW',
        'risk': risk,
        'origin_verified': False,
        'signature_verified': False,
        'integrity_verified': integrity_ok,
        'provenance_note': provenance_note,
        'checks': checks,
        'repair': ['repair ' + name.lower() for name in failed] or ['none'],
        'verdict': '可进入本地演示工作流' if status == 'HEALTHY' else '默认阻止下载和运行',
    }
