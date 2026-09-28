"""StepFun 离线契约测试：绝不向付费 API 发送真实请求。"""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import stepfun_client
from fastapi import HTTPException


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class StepFunTests(unittest.TestCase):
    def test_missing_key_does_not_contact_provider(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(stepfun_client.urllib.request, 'urlopen') as remote:
            self.assertFalse(stepfun_client.status()['available'])
            with self.assertRaisesRegex(stepfun_client.StepFunError, '未配置'):
                stepfun_client.interpret('检查 Skill', 'diagnostics', [])
            remote.assert_not_called()

    def test_model_output_is_restricted_to_catalog(self):
        content = {'intent': '检查技能', 'capabilities': ['诊断'], 'constraints': ['本地'],
                   'gaps': ['没有签名验真'], 'next_step': '查看本地安检',
                   'recommendation_reasons': {'skill-doctor': '目录用途是诊断'},
                   'recommended_skill_names': ['skill-doctor', 'invented-trusted-skill', 'skill-doctor'],
                   'reason': '匹配诊断能力', 'trust': 'HIGH', 'status': 'HEALTHY'}
        body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]}).encode()
        with patch.dict('os.environ', {'STEPFUN_API_KEY': 'test-key', 'STEPFUN_REGION': 'china'}), \
             patch.object(stepfun_client.urllib.request, 'urlopen', return_value=_Response(body)) as remote:
            result = stepfun_client.interpret('检查 Skill', 'diagnostics', [{'name': 'skill-doctor', 'use': '诊断'}])
        request = remote.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent['model'], 'step-3.7-flash')
        self.assertEqual(sent['response_format'], {'type': 'json_object'})
        self.assertEqual(sent['max_tokens'], stepfun_client.MAX_OUTPUT_TOKENS)
        self.assertEqual(result['recommended_skill_names'], ['skill-doctor'])
        self.assertEqual(result['recommendation_reasons'], {'skill-doctor': '目录用途是诊断'})
        self.assertEqual(result['gaps'], ['没有签名验真'])
        self.assertNotIn('trust', result)
        self.assertNotIn('status', result)

    def test_incomplete_explanation_is_rejected(self):
        content = {'intent': '检查技能', 'capabilities': ['诊断'],
                   'recommended_skill_names': ['skill-doctor'], 'reason': '太少'}
        body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}]}).encode()
        with patch.dict('os.environ', {'STEPFUN_API_KEY': 'test-key'}), \
             patch.object(stepfun_client.urllib.request, 'urlopen', return_value=_Response(body)):
            with self.assertRaisesRegex(stepfun_client.StepFunError, '字段不符合约定'):
                stepfun_client.interpret('检查 Skill', 'diagnostics', [{'name': 'skill-doctor'}])

    def test_truncated_or_invalid_json_is_rejected_with_distinct_errors(self):
        for finish_reason, content, expected in [
            ('length', '{"intent":"未完', '长度上限'),
            ('stop', '{"intent":"未完', '有效 JSON'),
            ('stop', '', '空内容'),
        ]:
            body = json.dumps({'choices': [{'finish_reason': finish_reason,
                                            'message': {'content': content}}]}).encode()
            with self.subTest(finish_reason=finish_reason, content=content), \
                 patch.dict('os.environ', {'STEPFUN_API_KEY': 'test-key'}), \
                 patch.object(stepfun_client.urllib.request, 'urlopen', return_value=_Response(body)):
                with self.assertRaisesRegex(stepfun_client.StepFunError, expected):
                    stepfun_client.interpret('检查 Skill', 'diagnostics', [{'name': 'skill-doctor'}])

    def test_api_uses_fresh_health_not_model_verdict(self):
        fake = {'intent': '检查', 'capabilities': ['诊断'],
                'recommended_skill_names': ['skill-doctor', 'sample-skill-broken'],
                'reason': '测试', 'model': 'step-3.7-flash', 'mode': 'test'}
        with patch.object(app, 'interpret_with_stepfun', return_value=fake), \
             patch.object(app, 'stepfun_status', return_value={'available': True}), \
             patch.object(app, 'AI_CALLS', {}), \
             patch.object(app, '_health_check', side_effect=lambda path: {
                 'status': 'UNHEALTHY' if path.name == 'sample-skill-broken' else 'HEALTHY',
                 'risk': 'HIGH' if path.name == 'sample-skill-broken' else 'MED'}):
            result = app.ai_interpret(app.IntentRequest(task='检查本地 Skill'),
                                      SimpleNamespace(state=SimpleNamespace(user={'id': 1})))
        self.assertEqual([item['eligible'] for item in result['recommendations']], [True, False])
        self.assertNotIn('recommended_skill_names', result)

    def test_per_account_demo_limit_prevents_unbounded_paid_calls(self):
        fake = {'intent': '检查', 'capabilities': [], 'recommended_skill_names': [],
                'reason': '', 'model': 'step-3.7-flash', 'mode': 'test'}
        request = SimpleNamespace(state=SimpleNamespace(user={'id': 7}))
        with patch.object(app, 'interpret_with_stepfun', return_value=fake) as remote, \
             patch.object(app, 'stepfun_status', return_value={'available': True}), \
             patch.object(app, 'AI_CALLS', {}):
            for _ in range(8):
                app.ai_interpret(app.IntentRequest(task='检查本地 Skill'), request)
            with self.assertRaises(HTTPException) as caught:
                app.ai_interpret(app.IntentRequest(task='检查本地 Skill'), request)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(remote.call_count, 8)


if __name__ == '__main__':
    unittest.main()
