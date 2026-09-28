"""固定需求集只评估本地词项检索，不替代模型或线上质量评测。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from evaluate_local_search import evaluate
import app


class SearchEvaluationTests(unittest.TestCase):
    def test_specific_task_does_not_return_skills_matching_only_generic_word(self):
        query = '请检查本地 Skill 的健康状况，并生成一份可审计的检查报告。'
        names = [item['name'] for item in app.search_local_catalog(query, source='local')]
        self.assertEqual(names, ['skill-doctor', 'skill-report'])

    def test_ten_cases_and_no_unrelated_fallback(self):
        result = evaluate()
        self.assertEqual(result['total'], 10)
        self.assertEqual(result['passed'], 10, result['cases'])
        self.assertEqual(result['scope'], 'local-keyword-prototype-only')


if __name__ == '__main__':
    unittest.main()
