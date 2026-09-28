"""Skill Schema 读取与校验；磁盘上的 skill.schema.json 是唯一规范来源。"""
from pathlib import Path
import json
import re
from typing import Any, Dict, List

SKILL_SCHEMA_PATH = Path(__file__).resolve().parent.parent / 'schemas' / 'skill.schema.json'
PORT_TYPES = {'string', 'image', 'json', 'file', 'text'}


def load_skill_schema() -> Dict[str, Any]:
    try:
        return json.loads(SKILL_SCHEMA_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Skill Schema 文件不可读: {exc}') from exc


def normalize_skill_meta(skill_dir: Path) -> Dict[str, Any]:
    """加载一个 Skill 的规范化元数据；不猜测/补造缺失的字段。"""
    path = skill_dir / 'skill.schema.json'
    try:
        meta = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ValueError('缺少 skill.schema.json') from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f'skill.schema.json 不是有效 JSON: {exc}') from exc
    if not isinstance(meta, dict):
        raise ValueError('Skill Schema 顶层必须是 JSON 对象')

    for field in ('name', 'version', 'description', 'inputs', 'outputs', 'resources'):
        if field not in meta:
            raise ValueError(f'Skill Schema 缺少必填字段: {field}')
    if meta['name'] != skill_dir.name:
        raise ValueError('Schema name 与 Skill 目录名不一致')
    if not isinstance(meta['version'], str) or not meta['version'].strip():
        raise ValueError('version 必须是非空字符串')
    if not isinstance(meta['description'], str) or not meta['description'].strip():
        raise ValueError('description 必须是非空字符串')
    if not isinstance(meta['inputs'], list) or not isinstance(meta['outputs'], list):
        raise ValueError('inputs 和 outputs 必须是数组')

    for direction in ('inputs', 'outputs'):
        seen = set()
        for port in meta[direction]:
            if not isinstance(port, dict):
                raise ValueError(f'{direction} 中的每个端口都必须是对象')
            name, port_type = port.get('name'), port.get('type')
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f'{direction} 端口缺少有效 name')
            if name in seen:
                raise ValueError(f'{direction} 存在重复端口: {name}')
            seen.add(name)
            if port_type not in PORT_TYPES:
                raise ValueError(f'{direction}.{name} 的 type 不受支持: {port_type}')
            if 'description' not in port or not isinstance(port['description'], str) or not port['description'].strip():
                raise ValueError(f'{direction}.{name} 缺少 description')
            if direction == 'inputs' and not isinstance(port.get('required'), bool):
                raise ValueError(f'inputs.{name}.required 必须是布尔值')

    resources = meta['resources']
    if not isinstance(resources, dict):
        raise ValueError('resources 必须是对象')
    if not isinstance(resources.get('dependencies'), list):
        raise ValueError('resources.dependencies 必须是数组')
    for dependency in resources['dependencies']:
        if not isinstance(dependency, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:==[A-Za-z0-9_.-]+)?', dependency):
            raise ValueError(f'不支持的依赖声明: {dependency!r}；本地原型仅支持 package 或 package==version')
    return meta


def validate_frontmatter(skill_dir: Path, meta: Dict[str, Any]) -> List[str]:
    """确认人类可读的 SKILL.md 身份信息和规范 Schema 一致。"""
    path = skill_dir / 'SKILL.md'
    if not path.is_file():
        return ['缺少 SKILL.md']
    text = path.read_text(encoding='utf-8', errors='replace')
    if not text.startswith('---') or text.count('---') < 2:
        return ['SKILL.md 缺少 YAML frontmatter']
    header = text.split('---', 2)[1]
    values = {}
    for line in header.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            values[key.strip()] = value.strip()
    issues = []
    if values.get('name') != meta['name']:
        issues.append('frontmatter name 与 Schema 不一致')
    if values.get('description') != meta['description']:
        issues.append('frontmatter description 与 Schema 不一致')
    return issues
