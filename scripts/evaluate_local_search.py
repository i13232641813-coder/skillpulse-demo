"""十个可重复的本地词项检索样例；不调用 StepFun 或 GitHub。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from app import search_local_catalog  # noqa: E402


CASES = [
    ('企业内部知识库问答', 'rag-blueprint'),
    ('检查 Skill 健康', 'skill-doctor'),
    ('生成合规报告', 'skill-report'),
    ('图片目标检测', 'tao-generate-image-grounding'),
    ('视觉定位', 'tao-generate-image-grounding'),
    ('诊断本地 skill', 'skill-doctor'),
    ('rag', 'rag-blueprint'),
    ('report', 'skill-report'),
    ('video', None),
    ('完全无关的烹饪菜谱', None),
]


def evaluate():
    rows = []
    for query, expected in CASES:
        results = search_local_catalog(query)
        names = [item['name'] for item in results[:3]]
        passed = expected in names if expected else not names
        rows.append({'query': query, 'expected': expected, 'top3': names, 'pass': passed})
    return {'scope': 'local-keyword-prototype-only', 'total': len(rows),
            'passed': sum(row['pass'] for row in rows), 'cases': rows}


if __name__ == '__main__':
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
