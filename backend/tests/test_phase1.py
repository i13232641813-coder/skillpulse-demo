"""隔离验证 Phase 1 治理门禁和两节点本地数据流。"""
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import app
from engine import run_local
from fastapi import HTTPException
from governance.checks import check_evaluation


class Phase1PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillpulse-test-')
        self.root = Path(self.temp.name)
        self.audit = self.root / 'audit.jsonl'
        self.catalog = self.root / 'catalogs'
        self.catalog.mkdir()
        self.patchers = [
            patch.object(app, 'AUDIT_PATH', self.audit),
            patch.object(app, 'CATALOG_DIR', self.catalog),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def node(self, node_id, name, inputs=None):
        health = app._health_check(app._skill_dir(name))
        return app.LegoNode(
            id=node_id,
            name=name,
            control_type='skill',
            node_schema=health['schema'],
            inputs=inputs or {},
        )

    def test_healthy_two_node_dataflow_and_sanitized_audit(self):
        doctor = self.node('n1', 'skill-doctor', {'query': 'rag-blueprint'})
        report_node = self.node('n2', 'skill-report')
        req = app.LegoRunRequest(
            nodes=[doctor, report_node], edges=[{'from': 'n1', 'to': 'n2'}],
            inputs={'n1': {'query': 'rag-blueprint'}}, selected_node='n2',
        )
        response = app.lego_run(req)
        self.assertTrue(response.passed)
        self.assertEqual([n['name'] for n in response.nodes], ['skill-doctor', 'skill-report'])
        first_output = json.loads(response.nodes[0]['outputs']['result'])
        self.assertEqual(first_output['skill'], 'rag-blueprint')
        self.assertEqual(len(first_output['checks']), 5)
        self.assertIn('rag-blueprint', response.selected_node['outputs']['report'])
        audit_text = self.audit.with_name('audit-chain.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('"query": "rag-blueprint"', audit_text)
        self.assertIn(response.run_id, audit_text)
        self.assertTrue((self.catalog / f'pipeline-{response.run_id}.json').is_file())

    def test_unhealthy_skill_is_blocked(self):
        broken = self.node('bad', 'sample-skill-broken')
        with self.assertRaises(HTTPException) as caught:
            app.lego_run(app.LegoRunRequest(nodes=[broken], edges=[]))
        self.assertEqual(caught.exception.status_code, 403)

    def test_doctor_reports_unhealthy_target_without_executing_it(self):
        result = run_local.run_skill('skill-doctor', {'query': 'sample-skill-broken'})
        self.assertTrue(result['ok'])
        measured = json.loads(result['outputs']['result'])
        self.assertEqual(measured['skill'], 'sample-skill-broken')
        self.assertEqual(measured['status'], 'UNHEALTHY')
        self.assertTrue(any(check['status'] != 'PASS' for check in measured['checks']))
        report = run_local.run_skill('skill-report', {'result': result['outputs']['result']})
        self.assertTrue(report['ok'])
        self.assertIn('UNHEALTHY', report['outputs']['report'])

    def test_doctor_rejects_unknown_or_path_target(self):
        for target in ('../../backend', 'missing-local-skill', 'skill-doctor'):
            with self.subTest(target=target):
                result = run_local.run_skill('skill-doctor', {'query': target})
                self.assertFalse(result['ok'])
                self.assertFalse(result['outputs'])

    def test_provenance_fields_do_not_claim_signature(self):
        health = app._health_check(app._skill_dir('rag-blueprint'))
        self.assertTrue(health['integrity_verified'])
        self.assertFalse(health['signature_verified'])
        self.assertFalse(health['origin_verified'])
        self.assertEqual(health['risk'], 'MED')
        self.assertEqual(health['trust'], 'LOCAL')
        self.assertIn('SHA-256', health['provenance_note'])

    def test_missing_reference_catalog_entries_are_not_claimed_healthy(self):
        reference = next(item for item in app.CATALOG if item['name'] == 'deepstream-generate-pipeline')
        self.assertEqual(reference['status'], 'REFERENCE_ONLY')
        self.assertEqual(reference['trust'], 'UNVERIFIED')
        self.assertNotIn(reference['name'], [item['name'] for item in app.search_local_catalog('deepstream')])

    def test_missing_required_input_is_clear_error(self):
        rag = self.node('n1', 'rag-blueprint')
        with self.assertRaises(HTTPException) as caught:
            app.lego_run(app.LegoRunRequest(nodes=[rag], edges=[]))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('缺少必填输入', caught.exception.detail)
        rag.inputs = {'query': '   '}
        with self.assertRaises(HTTPException) as empty:
            app.lego_run(app.LegoRunRequest(nodes=[rag], edges=[]))
        self.assertIn('缺少必填输入', empty.exception.detail)
        rag.inputs = {'query': {'not': 'a string'}}
        with self.assertRaises(HTTPException) as wrong_value:
            app.lego_run(app.LegoRunRequest(nodes=[rag], edges=[]))
        self.assertIn('输入类型不匹配', wrong_value.exception.detail)

    def test_incompatible_types_and_cycle_are_rejected(self):
        image = self.node('n1', 'tao-generate-image-grounding', {'image': 'sample'})
        doctor = self.node('n2', 'skill-doctor', {'query': 'test'})
        with self.assertRaises(HTTPException) as mismatch:
            app.lego_run(app.LegoRunRequest(nodes=[image, doctor], edges=[{'from': 'n1', 'to': 'n2'}]))
        self.assertEqual(mismatch.exception.status_code, 422)
        self.assertIn('类型不匹配', mismatch.exception.detail)

        rag = self.node('a', 'rag-blueprint', {'query': 'cycle'})
        doctor = self.node('b', 'skill-doctor', {'query': 'cycle'})
        with self.assertRaises(HTTPException) as cycle:
            app.lego_run(app.LegoRunRequest(nodes=[rag, doctor], edges=[{'from': 'a', 'to': 'b'}, {'from': 'b', 'to': 'a'}]))
        self.assertEqual(cycle.exception.status_code, 422)
        self.assertIn('循环依赖', cycle.exception.detail)

    def test_duplicate_nodes_and_runner_failure_never_report_success(self):
        rag = self.node('n1', 'rag-blueprint', {'query': 'failure'})
        with self.assertRaises(HTTPException) as duplicate:
            app.lego_run(app.LegoRunRequest(nodes=[rag, rag], edges=[]))
        self.assertEqual(duplicate.exception.status_code, 400)

        with patch.object(app, 'run_skill', return_value={'ok': False, 'outputs': {}, 'artifacts': [], 'error': 'intentional test failure'}):
            response = app.lego_run(app.LegoRunRequest(nodes=[rag], edges=[]))
        self.assertFalse(response.passed)
        self.assertEqual(response.nodes[0]['status'], 'UNHEALTHY')

    def test_yaml_edge_alias_and_empty_audit_export(self):
        rag = self.node('n1', 'rag-blueprint', {'query': 'yaml'})
        doctor = self.node('n2', 'skill-doctor')
        payload = app.pipeline_export(app.PipelineExportRequest(
            nodes=[rag, doctor], edges=[{'from': 'n1', 'to': 'n2'}], format='yaml',
        ))
        self.assertIn('from: "n1"', payload['yaml'])
        self.assertIn('to: "n2"', payload['yaml'])
        self.assertIn(b'operation-export', app.export_audit().body)

    def test_runner_timeout_is_returned_as_failure(self):
        with patch.object(run_local.subprocess, 'run', side_effect=run_local.subprocess.TimeoutExpired('runner', 1)):
            result = run_local.run_skill('rag-blueprint', {'query': 'timeout test'})
        self.assertFalse(result['ok'])
        self.assertIn('timed out', result['error'])
        self.assertFalse(result['resource']['memory_limit_enforced'])

    def test_evaluation_ignores_legacy_pass_label_and_checks_actual_output(self):
        skill_dir = self.root / 'eval-fixture'
        (skill_dir / 'evals').mkdir(parents=True)
        sample = {
            'verdict': 'PASS',
            'smoke_cases': [{
                'id': 'mismatch', 'inputs': {'query': 'demo'},
                'expected_output_keys': ['answer'],
                'expected_contains': {'answer': 'expected text'},
            }],
        }
        (skill_dir / 'evals' / 'evals.json').write_text(json.dumps(sample), encoding='utf-8')
        meta = {
            'inputs': [{'name': 'query', 'required': True}],
            'outputs': [{'name': 'answer'}],
        }
        with patch('governance.checks.run_skill', return_value={'ok': True, 'outputs': {'answer': 'actual text'}}):
            passed, detail = check_evaluation(skill_dir, meta, True, True)
        self.assertFalse(passed)
        self.assertIn('不包含预期内容', detail)


if __name__ == '__main__':
    unittest.main()
