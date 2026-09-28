"""StepFun 仅解析用户意图；Skill 准入必须由本地治理器独立判定。"""
import json
import os
import urllib.error
import urllib.request


class StepFunError(Exception):
    pass


MODEL = 'step-3.7-flash'
MAX_OUTPUT_TOKENS = 1600
ENDPOINTS = {
    'china': 'https://api.stepfun.com/v1/chat/completions',
    'global': 'https://api.stepfun.ai/v1/chat/completions',
}


def status():
    return {'available': bool(os.environ.get('STEPFUN_API_KEY')), 'model': MODEL,
            'region': os.environ.get('STEPFUN_REGION', 'china') if os.environ.get('STEPFUN_REGION', 'china') in ENDPOINTS else 'invalid'}


def interpret(task: str, scene: str, candidates: list[dict], environment: str = '') -> dict:
    key = os.environ.get('STEPFUN_API_KEY')
    if not key:
        raise StepFunError('未配置 StepFun API Key；本地检索仍可使用')
    region = os.environ.get('STEPFUN_REGION', 'china')
    if region not in ENDPOINTS:
        raise StepFunError('STEPFUN_REGION 只能为 china 或 global')
    allowed = {item['name'] for item in candidates}
    public_catalog = [{'name': item['name'], 'use': str(item.get('use', ''))[:100],
                       'summary': str(item.get('summary', ''))[:120], 'scene': str(item.get('scene', ''))[:40],
                       'source': item.get('source', '')} for item in candidates[:24]]
    instruction = (
        '你是 SkillPulse 的需求解析器，不是安全审核员。将用户任务解析成 JSON 对象，'
        '必须包含 intent (简短中文), constraints (环境或业务约束字符串数组), capabilities (能力字符串数组), '
        'recommended_skill_names (名称数组), recommendation_reasons (名称到匹配理由的对象), '
        'gaps (目录未覆盖的能力或条件数组), reason (整体判断依据), next_step (建议的下一步)。'
        '只可从给定本地目录中选择最多 3 个 Skill 名称；没有匹配就返回空数组。'
        '逐项理由只能引用目录提供的用途、简介、场景或来源；不匹配要说明缺口，不能复述任务充数。'
        'JSON 结构示意：{"intent":"文本","constraints":["文本"],"capabilities":["文本"],'
        '"recommended_skill_names":["目录中的名称"],"recommendation_reasons":{"目录中的名称":"依据"},'
        '"gaps":["文本"],"reason":"文本","next_step":"文本"}。'
        '每个字符串不超过 50 字，数组最多 3 项，只输出紧凑 JSON，不要解释或 Markdown。'
        '不要推断安全、签名、来源身份或合规结论；不得建议跳过本地安检。'
    )
    payload = {'model': MODEL, 'stream': False,
               'max_tokens': MAX_OUTPUT_TOKENS, 'response_format': {'type': 'json_object'},
               'messages': [{'role': 'system', 'content': instruction},
                            {'role': 'user', 'content': json.dumps({'task': task, 'scene': scene,
                                                                     'environment': environment,
                                                                     'local_catalog': public_catalog}, ensure_ascii=False)}]}
    request = urllib.request.Request(
        ENDPOINTS[region], data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            outer = json.load(response)
    except urllib.error.HTTPError as exc:
        raise StepFunError(f'StepFun 返回 HTTP {exc.code}；请检查新 Key、区域和模型权限') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise StepFunError('StepFun 网络请求未完成；本地检索仍可使用') from None
    try:
        choice = outer['choices'][0]
        finish_reason = choice.get('finish_reason')
        content = choice['message']['content']
    except (KeyError, IndexError, TypeError, ValueError):
        raise StepFunError('StepFun 响应缺少必要字段；没有采信模型建议') from None
    if finish_reason == 'length':
        raise StepFunError('StepFun 输出达到长度上限；本次建议未采信，请缩短需求后再试')
    if finish_reason != 'stop':
        raise StepFunError('StepFun 未正常完成输出；本次建议未采信')
    if not isinstance(content, str) or not content.strip():
        raise StepFunError('StepFun 返回空内容；本次建议未采信')
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise StepFunError('StepFun 未返回有效 JSON；本次建议未采信') from None
    if not isinstance(parsed, dict):
        raise StepFunError('StepFun JSON 不是对象；本次建议未采信')
    raw_names = parsed.get('recommended_skill_names')
    raw_caps = parsed.get('capabilities')
    raw_constraints = parsed.get('constraints')
    raw_gaps = parsed.get('gaps')
    raw_reasons = parsed.get('recommendation_reasons')
    if (not isinstance(raw_names, list) or not isinstance(raw_caps, list)
            or not isinstance(raw_constraints, list) or not isinstance(raw_gaps, list)
            or not isinstance(raw_reasons, dict)
            or any(not isinstance(parsed.get(key), str) for key in ('intent', 'reason', 'next_step'))
            or any(not isinstance(value, str) for group in (raw_caps, raw_constraints, raw_gaps) for value in group)):
        raise StepFunError('StepFun 返回字段不符合约定；没有采信模型建议')
    names = list(dict.fromkeys(name for name in raw_names if isinstance(name, str) and name in allowed))[:3]
    return {'intent': parsed['intent'][:160],
            'constraints': [value[:120] for value in raw_constraints][:5],
            'capabilities': [value[:80] for value in raw_caps][:5],
            'gaps': [value[:120] for value in raw_gaps][:5],
            'recommended_skill_names': names,
            'recommendation_reasons': {name: raw_reasons.get(name, '')[:200] for name in names
                                       if isinstance(raw_reasons.get(name), str)},
            'reason': parsed['reason'][:300], 'next_step': parsed['next_step'][:200],
            'model': MODEL, 'mode': 'stepfun-interpretation-not-trust-verdict'}
