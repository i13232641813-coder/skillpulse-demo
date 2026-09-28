"""Phase 2 控制节点、依赖声明和导出审计回归。测试产物只写临时目录。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from fastapi import HTTPException
from governance.versions import build_snapshot


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillpulse-phase2-')
        self.root = Path(self.temp.name)
        self.catalog = self.root / 'catalogs'
        self.versions = self.root / 'versions'
        self.catalog.mkdir()
        self.versions.mkdir()
        self.audit = self.root / 'audit.jsonl'
        self.patchers = [patch.object(app, 'CATALOG_DIR', self.catalog),
                         patch.object(app, 'VERSIONS_DIR', self.versions),
                         patch.object(app, 'AUDIT_PATH', self.audit)]
        for item in self.patchers:
            item.start()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def node(node_id, name, inputs=None):
        control_type = name if name in app.CONTROL_SCHEMAS else 'skill'
        return app.LegoNode(id=node_id, name=name, control_type=control_type, inputs=inputs or {})

    def test_explicit_branch_runs_only_chosen_route(self):
        nodes = [self.node('doctor', 'skill-doctor', {'query': 'rag-blueprint'}),
                 self.node('branch', 'if_branch', {'condition': True}),
                 self.node('yes', 'skill-report'),
                 self.node('no', 'tao-generate-image-grounding')]
        edges = [{'from': 'doctor', 'to': 'branch'},
                 {'from': 'branch', 'to': 'yes', 'route': 'true'},
                 {'from': 'branch', 'to': 'no', 'route': 'false'}]
        result = app.lego_run(app.LegoRunRequest(nodes=nodes, edges=edges))
        self.assertTrue(result.passed)
        self.assertEqual([n['status'] for n in result.nodes], ['HEALTHY', 'HEALTHY', 'HEALTHY', 'SKIPPED'])
        self.assertEqual([e['active'] for e in result.edges], [True, True, False])
        self.assertIn('rag-blueprint', result.nodes[2]['outputs']['report'])
        self.assertEqual(result.nodes[3]['outputs'], {})
        nodes[1].inputs['condition'] = False
        false_result = app.lego_run(app.LegoRunRequest(nodes=nodes, edges=edges))
        self.assertTrue(false_result.passed)
        self.assertEqual([n['status'] for n in false_result.nodes], ['HEALTHY', 'HEALTHY', 'SKIPPED', 'HEALTHY'])
        self.assertEqual([e['active'] for e in false_result.edges], [True, False, True])

    def test_branch_requires_both_routes(self):
        nodes = [self.node('branch', 'if_branch', {'condition': False, 'value': 'x'}),
                 self.node('yes', 'skill-doctor')]
        with self.assertRaises(HTTPException) as caught:
            app.lego_run(app.LegoRunRequest(nodes=nodes, edges=[{'from': 'branch', 'to': 'yes', 'route': 'true'}]))
        self.assertEqual(caught.exception.status_code, 422)

    def test_bounded_loop_repeats_healthy_skill(self):
        loop = self.node('loop', 'loop', {'value': 'hello', 'count': 2, 'target_skill': 'rag-blueprint'})
        result = app.lego_run(app.LegoRunRequest(nodes=[loop], edges=[]))
        self.assertTrue(result.passed)
        self.assertEqual(len(result.nodes[0]['outputs']['results']), 2)
        self.assertIn('hello', result.nodes[0]['outputs']['results'][1]['answer'])
        loop.inputs['count'] = 6
        with self.assertRaises(HTTPException) as caught:
            app.lego_run(app.LegoRunRequest(nodes=[loop], edges=[]))
        self.assertEqual(caught.exception.status_code, 422)
        loop.inputs = {'value': 'hello', 'count': 1, 'target_skill': 'sample-skill-broken'}
        with self.assertRaises(HTTPException) as blocked:
            app.lego_run(app.LegoRunRequest(nodes=[loop], edges=[]))
        self.assertEqual(blocked.exception.status_code, 403)

    def test_runner_preserves_chinese_text(self):
        result = app.run_skill('skill-doctor', {'query': 'rag-blueprint'})
        self.assertTrue(result['ok'])
        self.assertIn('rag-blueprint', result['outputs']['result'])
        self.assertIn('Catalog', result['outputs']['result'])
        self.assertIn('实际执行', result['outputs']['result'])

    def test_versions_are_grounded_in_disk_and_legacy_is_unverified(self):
        (self.versions / 'skill-doctor.json').write_text(json.dumps({'versions': [{'version': '99.0', 'status': 'current'}]}), encoding='utf-8')
        data = app.skill_versions('skill-doctor')
        self.assertEqual(data['current']['version'], '0.2.0')
        self.assertEqual(data['history'][0]['status'], 'unverified_record')
        with self.assertRaises(HTTPException) as caught:
            app.save_skill_version('skill-doctor', {'version': '99.0'})
        self.assertEqual(caught.exception.status_code, 409)
        saved = app.save_skill_version('skill-doctor', {'version': '0.2.0'})
        self.assertTrue(saved['saved'])
        self.assertEqual(len(json.loads((self.versions / 'skill-doctor.json').read_text(encoding='utf-8'))['versions']), 2)
        updated = app.skill_versions('skill-doctor')
        self.assertEqual(updated['history'][-1]['status'], 'snapshot_verified')
        self.assertTrue(app.download_skill_snapshot('skill-doctor', saved['snapshot_sha256']).body.startswith(b'PK'))
        archive = next((self.versions / 'snapshots').glob('skill-doctor-*.zip'))
        archive.write_bytes(b'tampered test data')
        self.assertEqual(app.skill_versions('skill-doctor')['history'][-1]['status'], 'snapshot_missing_or_tampered')
        with self.assertRaises(HTTPException) as missing:
            app.download_skill_snapshot('skill-doctor', saved['snapshot_sha256'])
        self.assertEqual(missing.exception.status_code, 404)

    def test_dependency_conflict_from_declared_pins(self):
        real = app._health_check
        def health(path):
            report = real(path)
            report['schema']['resources']['dependencies'] = ['demo-lib==1.0'] if path.name == 'rag-blueprint' else ['demo-lib==2.0']
            return report
        with patch.object(app, '_health_check', side_effect=health):
            data = app._dependency_compatibility(['rag-blueprint', 'skill-doctor'])
        self.assertFalse(data['compatible'])
        self.assertTrue(any('demo-lib' in note for note in data['notes']))
        with patch.object(app, '_health_check', side_effect=health):
            with self.assertRaises(HTTPException) as caught:
                app.lego_run(app.LegoRunRequest(nodes=[self.node('a', 'rag-blueprint', {'query': 'x'}),
                                                      self.node('b', 'skill-doctor')], edges=[{'from': 'a', 'to': 'b'}]))
        self.assertEqual(caught.exception.status_code, 422)

    def test_export_and_audit_report_are_sanitized(self):
        doctor = self.node('n1', 'skill-doctor', {'query': 'rag-blueprint'})
        formatter = self.node('n2', 'skill-report')
        run = app.lego_run(app.LegoRunRequest(nodes=[doctor, formatter], edges=[{'from': 'n1', 'to': 'n2'}]))
        report = json.loads(app.audit_report(run.run_id).body)
        self.assertTrue(report['passed'])
        self.assertEqual(report['audit_integrity']['coverage'], 'current_chain')
        self.assertTrue(report['audit_integrity']['run_record_verified'])
        self.assertNotIn('"query": "rag-blueprint"', json.dumps(report))
        exported = app.pipeline_export(app.PipelineExportRequest(nodes=[doctor, formatter], edges=[{'from': 'n1', 'to': 'n2'}]))
        self.assertIn('version: "0.2.0"', exported['yaml'])
        self.assertNotIn('"query": "rag-blueprint"', exported['yaml'])
        self.assertIn('/api/pipelines/run', exported['openapi']['paths'])
        with self.assertRaises(HTTPException) as caught:
            app.pipeline_export(app.PipelineExportRequest(nodes=[doctor, formatter], edges=[
                {'from': 'n1', 'to': 'n2'}, {'from': 'n2', 'to': 'n1'}]))
        self.assertEqual(caught.exception.status_code, 422)
        run_path = self.catalog / f'pipeline-{run.run_id}.json'
        altered = json.loads(run_path.read_text(encoding='utf-8'))
        altered['response_summary']['passed'] = False
        run_path.write_text(json.dumps(altered, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(HTTPException) as tampered:
            app.audit_report(run.run_id)
        self.assertEqual(tampered.exception.status_code, 409)

    def test_audit_chain_detects_tampering_and_blocks_append(self):
        app._append_audit({'time': 'test', 'action': 'operation-add', 'target': 'skill-doctor', 'result': 'recorded'})
        chain_path = self.audit.with_name('audit-chain.jsonl')
        self.assertTrue(app.audit_verify()['valid'])
        self.assertEqual(app.audit_verify()['count'], 1)
        original = chain_path.read_text(encoding='utf-8')
        chain_path.write_text(original.replace('skill-doctor', 'rag-blueprint'), encoding='utf-8')
        self.assertFalse(app.audit_verify()['valid'])
        with self.assertRaises(HTTPException) as blocked:
            app._append_audit({'time': 'test', 'action': 'operation-add', 'target': 'another', 'result': 'recorded'})
        self.assertEqual(blocked.exception.status_code, 409)
        with self.assertRaises(HTTPException) as caught:
            app.get_audit_logs()
        self.assertEqual(caught.exception.status_code, 409)
        with patch.object(app, 'run_skill') as runner:
            with self.assertRaises(HTTPException) as blocked_run:
                app.lego_run(app.LegoRunRequest(nodes=[self.node('n1', 'skill-doctor', {'query': 'must not run'})], edges=[]))
            runner.assert_not_called()
        self.assertEqual(blocked_run.exception.status_code, 409)

    def test_legacy_audit_is_redacted_and_operation_is_chained(self):
        self.audit.write_text(json.dumps({'time': '2026-09-24 12:00:00', 'action': 'run',
                                          'target': 'skill-doctor', 'detail': 'api_key=old-sensitive-value'}) + '\n', encoding='utf-8')
        result = app.get_audit_logs()
        self.assertEqual(result['integrity']['legacy_count'], 1)
        self.assertNotIn('old-sensitive-value', json.dumps(result))
        app.append_audit_log(app.OperationAuditRequest(action='add', target='skill-doctor', node_id='n1'))
        self.assertTrue(app.audit_verify()['valid'])
        exported = app.export_audit().body.decode('utf-8')
        self.assertIn('operation-add', exported)
        self.assertNotIn('old-sensitive-value', exported)

    def test_snapshot_rejects_credential_like_file(self):
        skill = self.root / 'example-skill'
        skill.mkdir()
        (skill / 'run_local.py').write_text('def run(x): return x', encoding='utf-8')
        (skill / '.env.production').write_text('not-a-real-key', encoding='utf-8')
        with self.assertRaises(ValueError):
            build_snapshot(skill)


if __name__ == '__main__':
    unittest.main()
