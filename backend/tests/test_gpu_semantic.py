"""CUDA 路由的离线测试：不安装模型、不假报 GPU。"""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import gpu_semantic
from fastapi import HTTPException


class _Vector:
    def __init__(self, values):
        self.values = values

    def __matmul__(self, other):
        return sum(left * right for left, right in zip(self.values, other.values))


class GpuSemanticTests(unittest.TestCase):
    def test_no_local_model_is_unavailable_not_mock_success(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(gpu_semantic.status()['available'])
            with self.assertRaises(gpu_semantic.SemanticUnavailable):
                gpu_semantic.rank('检查 Skill', [{'name': 'skill-doctor'}])

    def test_ranking_uses_cuda_model_output(self):
        fake_cuda = SimpleNamespace(cuda=SimpleNamespace(get_device_name=lambda _index: 'Test GPU',
                                                          synchronize=lambda: None))
        fake_model = SimpleNamespace(encode=lambda *_args, **_kwargs: [
            _Vector([1, 0]), _Vector([0, 1]), _Vector([1, 0])])
        with tempfile.TemporaryDirectory(prefix='skillpulse-model-test-') as folder, \
             patch.dict('os.environ', {'SKILLPULSE_EMBEDDING_MODEL_DIR': folder}), \
             patch.object(gpu_semantic, '_cuda', return_value=fake_cuda), \
             patch.object(gpu_semantic, '_load_model', return_value=fake_model):
            result = gpu_semantic.rank('skill report', [{'name': 'a'}, {'name': 'b'}])
        self.assertEqual([item['name'] for item in result['results']], ['b', 'a'])
        self.assertEqual(result['mode'], 'local-cuda-embedding')

    def test_route_refuses_unavailable_gpu(self):
        with patch.object(app, 'rank_on_cuda', side_effect=gpu_semantic.SemanticUnavailable('CUDA unavailable')):
            with self.assertRaises(HTTPException) as caught:
                app.semantic_search(app.IntentRequest(task='检查本地 Skill'))
        self.assertEqual(caught.exception.status_code, 503)

    def test_semantic_score_does_not_override_health(self):
        measured = {'results': [{'name': 'sample-skill-broken', 'score': 0.99}],
                    'model': 'local-model', 'device': 'Test GPU', 'elapsed_ms': 1.0}
        with patch.object(app, 'rank_on_cuda', return_value=measured), \
             patch.object(app, '_health_check', return_value={'status': 'UNHEALTHY', 'risk': 'HIGH',
                                                               'trust': 'LOW', 'schema': {}}):
            response = app.semantic_search(app.IntentRequest(task='检查本地 Skill'))
        self.assertEqual(response['results'][0]['status'], 'UNHEALTHY')
        self.assertEqual(response['results'][0]['risk'], 'HIGH')


if __name__ == '__main__':
    unittest.main()
