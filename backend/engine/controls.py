"""Phase 2 控制节点契约：分支不执行表达式，循环只重复可信的本地 Skill。"""

CONTROL_SCHEMAS = {
    'if_branch': {
        'name': 'if_branch', 'version': '1.0.0',
        'description': '按显式布尔条件只运行 true 或 false 一条路径；不求值任意代码。',
        'inputs': [
            {'name': 'value', 'type': 'any', 'required': True, 'description': '向选中路径传递的值'},
            {'name': 'condition', 'type': 'boolean', 'required': True, 'description': 'true 或 false'},
        ],
        'outputs': [{'name': 'value', 'type': 'any', 'description': '原样传递的值'}],
        'resources': {'gpu': 'none', 'model': 'none', 'dependencies': []},
    },
    'loop': {
        'name': 'loop', 'version': '1.0.0',
        'description': '最多重复 5 次调用指定的健康本地 Skill；每次将上次输出作为下次输入。',
        'inputs': [
            {'name': 'value', 'type': 'any', 'required': True, 'description': '首次调用的输入'},
            {'name': 'count', 'type': 'integer', 'required': True, 'description': '调用次数，1–5'},
            {'name': 'target_skill', 'type': 'string', 'required': True, 'description': '要重复调用的健康 Skill 名称'},
        ],
        'outputs': [
            {'name': 'value', 'type': 'any', 'description': '最后一次输出'},
            {'name': 'results', 'type': 'json', 'description': '各次输出列表'},
        ],
        'resources': {'gpu': 'none', 'model': 'none', 'dependencies': []},
    },
}


def compatible_type(source: str, target: str) -> bool:
    return source == target or 'any' in (source, target) or {source, target} == {'text', 'string'}


def coerce_control_value(value, port_type: str):
    if port_type == 'boolean' and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('true', 'false'):
            return normalized == 'true'
    if port_type == 'integer' and isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def value_matches_type(value, port_type: str) -> bool:
    if port_type == 'any':
        return True
    if port_type == 'boolean':
        return isinstance(value, bool)
    if port_type == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if port_type == 'json':
        return isinstance(value, (dict, list))
    return isinstance(value, str)
