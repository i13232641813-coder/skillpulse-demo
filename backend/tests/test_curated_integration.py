"""仅显式开启时执行真实 DGX 容器集成测试；产物写临时目录。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from engine.curated_card_validator import availability
from engine.run_local import run_skill


@unittest.skipUnless(os.environ.get('SKILLPULSE_CURATED_INTEGRATION') == '1',
                     '需要在已授权 DGX 上显式开启真实容器测试')
class CuratedIntegrationTests(unittest.TestCase):
    def test_three_node_pipeline_and_marker_block(self):
        ready, reason = availability()
        self.assertTrue(ready, reason)
        with tempfile.TemporaryDirectory(prefix='skillpulse-curated-integration-') as folder:
            root = Path(folder)
            with patch.object(app, 'AUDIT_PATH', root / 'audit.jsonl'), \
                 patch.object(app, 'CATALOG_DIR', root / 'catalogs'), \
                 patch.object(app, 'USER_RUNS_DIR', root / 'runs'):
                nodes = []
                for node_id, name, inputs in (
                    ('n1', 'skill-doctor', {'query': 'skill-report'}),
                    ('n2', 'skill-report', {}),
                    ('n3', 'nvidia-skill-card-validator', {}),
                ):
                    report = app._health_check(app._skill_dir(name))
                    self.assertEqual(report['status'], 'HEALTHY', name)
                    nodes.append(app.LegoNode(id=node_id, name=name,
                                              control_type='skill',
                                              node_schema=report['schema'],
                                              inputs=inputs))
                response = app.lego_run(app.LegoRunRequest(
                    nodes=nodes,
                    edges=[{'from': 'n1', 'to': 'n2'}, {'from': 'n2', 'to': 'n3'}],
                    inputs={'n1': {'query': 'skill-report'}},
                    selected_node='n3',
                ))
                self.assertTrue(response.passed)
                self.assertEqual([item['name'] for item in response.nodes],
                                 ['skill-doctor', 'skill-report', 'nvidia-skill-card-validator'])
                self.assertEqual(response.selected_node['outputs']['validation'], 'PASS')
                self.assertTrue((root / 'catalogs' / f'pipeline-{response.run_id}.json').is_file())
                audit = (root / 'audit-chain.jsonl').read_text(encoding='utf-8')
                self.assertIn('d8519c57da6db5d9bea274ec1724a4a7a56a3dee', audit)
                self.assertNotIn('"query": "skill-report"', audit)
        blocked = run_skill('nvidia-skill-card-validator',
                            {'card': '# Card\n<!-- VERIFY: owner -->'})
        self.assertFalse(blocked['ok'])
        self.assertIn('待人工处理标记', blocked['error'])


if __name__ == '__main__':
    unittest.main()
