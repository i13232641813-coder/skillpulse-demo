"""Phase4 初审和账号数据隔离；只使用临时目录，不写真实 outbox。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from fastapi import HTTPException
from security.quarantine import (PackageError, import_package, list_review_queue, preview_file,
                                 read_upload, review_files, submit_review)
from security.user_data import read_run, read_workflow
from test_phase3 import package_bytes


def request_for(user_id):
    return SimpleNamespace(state=SimpleNamespace(user={'id': user_id, 'username': f'user{user_id}', 'role': 'engineer'}))


class Phase4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillpulse-phase4-')
        self.root = Path(self.temp.name)
        self.private = self.root / 'private_skills'
        self.runs = self.root / 'user_runs'
        self.workflows = self.root / 'user_workflows'
        self.audit = self.root / 'audit.jsonl'
        self.catalog = self.root / 'catalogs'
        self.patchers = [patch.object(app, 'PRIVATE_SKILLS_DIR', self.private),
                         patch.object(app, 'USER_RUNS_DIR', self.runs),
                         patch.object(app, 'USER_WORKFLOWS_DIR', self.workflows),
                         patch.object(app, 'AUDIT_PATH', self.audit),
                         patch.object(app, 'CATALOG_DIR', self.catalog)]
        for item in self.patchers:
            item.start()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def node(self, node_id, name, inputs=None):
        return app.LegoNode(id=node_id, name=name, node_schema=app._health_check(app._skill_dir(name))['schema'], inputs=inputs or {})

    def test_two_person_review_is_record_only_and_immutable(self):
        record = import_package(self.private, 1, package_bytes())
        upload_id = record['id']
        self.assertEqual(len(list_review_queue(self.private)), 1)
        paths = {item['path'] for item in review_files(self.private, 1, upload_id)}
        self.assertIn('runner.py', paths)
        self.assertIn('def run', preview_file(self.private, 1, upload_id, 'runner.py'))
        with self.assertRaises(PackageError):
            preview_file(self.private, 1, upload_id, '../record.json')
        with self.assertRaises(PackageError):
            submit_review(self.private, 1, upload_id, 1, 'PRELIMINARY_PASS', 'Self review must fail')
        review = submit_review(self.private, 1, upload_id, 2, 'PRELIMINARY_PASS', 'Static and document checks reviewed; remain quarantined')
        self.assertEqual(review['reviewer_id'], 2)
        after = read_upload(self.private, 1, upload_id)
        self.assertEqual(after['report']['status'], 'QUARANTINED')
        self.assertFalse(after['report']['can_run'])
        self.assertEqual(after['report']['checks'][2]['status'], 'UNVERIFIED')
        with self.assertRaises(PackageError):
            submit_review(self.private, 1, upload_id, 3, 'REJECTED', 'Cannot overwrite decision')

    def test_failed_static_scan_cannot_receive_preliminary_pass(self):
        broken = package_bytes(extra={'sample-skill/runner.py': 'eval("2+2")\n'})
        record = import_package(self.private, 1, broken)
        with self.assertRaises(PackageError):
            submit_review(self.private, 1, record['id'], 2, 'PRELIMINARY_PASS', 'Should be rejected because scan failed')

    def test_workflow_snapshot_and_run_summary_are_owner_scoped(self):
        actor = app.CURRENT_ACTOR.set(1)
        try:
            doctor = self.node('n1', 'skill-doctor', {'query': 'rag-blueprint'})
            formatter = self.node('n2', 'skill-report')
            edges = [{'from': 'n1', 'to': 'n2'}]
            with self.assertRaises(HTTPException) as duplicate:
                app.save_my_workflow(request_for(1), app.WorkflowSaveRequest(name='Duplicate', nodes=[doctor, self.node('n3', 'skill-doctor')], edges=[]))
            self.assertEqual(duplicate.exception.status_code, 422)
            saved = app.save_my_workflow(request_for(1), app.WorkflowSaveRequest(name='Private workflow', nodes=[doctor, formatter], edges=edges))
            self.assertNotIn('"query": "rag-blueprint"', json.dumps(saved, ensure_ascii=False))
            self.assertIsNone(read_workflow(self.workflows, 2, saved['id']))
            run = app.lego_run(app.LegoRunRequest(nodes=[doctor, formatter], edges=edges, inputs={'n1': {'query': 'rag-blueprint'}}))
            self.assertTrue(run.passed)
            self.assertIsNone(read_run(self.runs, 2, run.run_id))
            own_record = read_run(self.runs, 1, run.run_id)
            self.assertEqual(own_record['owner_id'], 1)
            self.assertNotIn('"query": "rag-blueprint"', json.dumps(own_record, ensure_ascii=False))
            self.assertFalse((self.catalog / f'pipeline-{run.run_id}.json').exists())
            self.assertEqual(app.audit_report(run.run_id).status_code, 200)
            with self.assertRaises(HTTPException) as other:
                app.my_run(request_for(2), run.run_id)
            self.assertEqual(other.exception.status_code, 404)
            with self.assertRaises(HTTPException) as other_flow:
                app.my_workflow(request_for(2), saved['id'])
            self.assertEqual(other_flow.exception.status_code, 404)
        finally:
            app.CURRENT_ACTOR.reset(actor)


if __name__ == '__main__':
    unittest.main()
