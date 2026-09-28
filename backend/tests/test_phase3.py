"""Phase3 本地账号和上传隔离测试；所有数据写入临时目录。"""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from fastapi import HTTPException
from security import local_auth, quarantine


def package_bytes(name='sample-skill', extra=None):
    meta = {'name': name, 'version': '0.1.0', 'description': 'Read-only example',
            'inputs': [{'name': 'query', 'type': 'string', 'required': True, 'description': 'Question'}],
            'outputs': [{'name': 'answer', 'type': 'text', 'description': 'Answer'}],
            'resources': {'dependencies': []}}
    files = {
        f'{name}/skill.schema.json': json.dumps(meta),
        f'{name}/SKILL.md': '---\nname: '+name+'\ndescription: Read-only example\n---\nDemo.',
        f'{name}/skill-card.md': '# Example\n',
        f'{name}/runner.py': 'def run(inputs):\n    return {"answer": "ok"}\n',
    }
    files.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return buffer.getvalue()


class Phase3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillpulse-phase3-')
        self.root = Path(self.temp.name)
        self.db = self.root / 'accounts.sqlite3'
        self.uploads = self.root / 'private_skills'

    def tearDown(self):
        self.temp.cleanup()

    def test_bootstrap_login_logout_and_role(self):
        self.assertFalse(local_auth.has_users(self.db))
        engineer = local_auth.create_user(self.db, 'owner', 'long-demo-password', 'engineer', bootstrap=True)
        self.assertEqual(engineer['role'], 'engineer')
        with self.assertRaises(local_auth.AuthError):
            local_auth.create_user(self.db, 'second', 'long-demo-password', 'engineer', bootstrap=True)
        business = local_auth.create_user(self.db, 'viewer', 'another-long-password', 'business')
        with self.assertRaises(local_auth.AuthError):
            local_auth.login(self.db, 'viewer', 'wrong-password')
        token, logged_in = local_auth.login(self.db, 'viewer', 'another-long-password')
        self.assertEqual(logged_in['id'], business['id'])
        self.assertEqual(local_auth.get_user(self.db, token)['role'], 'business')
        self.assertNotIn(token, self.db.read_bytes().decode('latin-1'))
        local_auth.logout(self.db, token)
        self.assertIsNone(local_auth.get_user(self.db, token))

    def test_local_password_reset_preserves_account_and_revokes_sessions(self):
        missing = self.root / 'missing.sqlite3'
        with self.assertRaises(local_auth.AuthError):
            local_auth.list_local_users(missing)
        self.assertFalse(missing.exists())
        owner = local_auth.create_user(self.db, 'owner', 'long-demo-password', 'engineer', bootstrap=True)
        old_token, _ = local_auth.login(self.db, 'owner', 'long-demo-password')
        self.assertEqual(local_auth.list_local_users(self.db), [{'username': 'owner', 'role': 'engineer'}])
        with self.assertRaises(local_auth.AuthError):
            local_auth.reset_local_password(self.db, 'unknown', 'new-long-demo-password')
        with self.assertRaises(local_auth.AuthError):
            local_auth.reset_local_password(self.db, 'owner', 'short')
        self.assertIsNotNone(local_auth.get_user(self.db, old_token))
        local_auth.reset_local_password(self.db, 'owner', 'new-long-demo-password')
        self.assertIsNone(local_auth.get_user(self.db, old_token))
        with self.assertRaises(local_auth.AuthError):
            local_auth.login(self.db, 'owner', 'long-demo-password')
        _, user = local_auth.login(self.db, 'owner', 'new-long-demo-password')
        self.assertEqual(user['id'], owner['id'])
        self.assertEqual(user['role'], 'engineer')

    def test_private_package_is_always_quarantined_and_owner_scoped(self):
        with patch('governance.checks.run_skill', side_effect=AssertionError('上传包禁止执行')) as runner:
            record = quarantine.import_package(self.uploads, 1, package_bytes())
        runner.assert_not_called()
        self.assertEqual(record['report']['status'], 'QUARANTINED')
        self.assertEqual(record['report']['risk'], 'HIGH')
        self.assertFalse(record['report']['can_run'])
        self.assertEqual([item['status'] for item in record['report']['checks']][2:4], ['UNVERIFIED', 'NOT_RUN'])
        self.assertEqual(len(quarantine.list_uploads(self.uploads, 1)), 1)
        self.assertEqual(quarantine.list_uploads(self.uploads, 2), [])
        self.assertIsNone(quarantine.read_upload(self.uploads, 2, record['id']))
        self.assertFalse((app.SKILLS_DIR / record['report']['skill'] / record['id']).exists())

    def test_standard_skill_is_explained_but_never_silently_normalized(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('external-demo/SKILL.md',
                             '---\nname: external-demo\ndescription: Public agent instructions\n---\nRead-only demo.')
        record = quarantine.import_package(self.uploads, 1, buffer.getvalue())
        self.assertEqual(record['report']['checks'][0]['status'], 'PARTIAL')
        self.assertIn('缺少平台运行 Schema', record['report']['checks'][0]['detail'])
        saved = quarantine.read_upload(self.uploads, 1, record['id'])
        self.assertEqual(saved['content_integrity'], 'PASS')
        self.assertEqual(saved['workflow_state'], 'REVIEW_REQUIRED')
        self.assertFalse(saved['report']['can_run'])
        with self.assertRaises(quarantine.PackageError):
            quarantine.submit_review(self.uploads, 1, record['id'], 2,
                                     'PRELIMINARY_PASS', 'No execution schema exists yet')

    def test_tampered_quarantine_content_blocks_preliminary_pass(self):
        record = quarantine.import_package(self.uploads, 1, package_bytes())
        path = self.uploads / '1' / record['id'] / 'sample-skill' / 'SKILL.md'
        path.write_text(path.read_text(encoding='utf-8') + '\nchanged', encoding='utf-8')
        saved = quarantine.read_upload(self.uploads, 1, record['id'])
        self.assertEqual(saved['content_integrity'], 'FAIL')
        with self.assertRaisesRegex(quarantine.PackageError, '完整性'):
            quarantine.submit_review(self.uploads, 1, record['id'], 2,
                                     'PRELIMINARY_PASS', 'Tampered content must not pass')

    def test_review_can_only_approve_isolated_evaluation_then_revoke(self):
        record = quarantine.import_package(self.uploads, 1, package_bytes())
        self.assertEqual(quarantine.read_upload(self.uploads, 1, record['id'])['workflow_state'], 'REVIEW_REQUIRED')
        quarantine.submit_review(self.uploads, 1, record['id'], 2,
                                 'PRELIMINARY_PASS', 'Only permit isolated future evaluation')
        approved = quarantine.read_upload(self.uploads, 1, record['id'])
        self.assertEqual(approved['workflow_state'], 'APPROVED_FOR_EVALUATION')
        self.assertTrue(approved['allowed_for_evaluation'])
        self.assertFalse(approved['can_download'])
        self.assertFalse(approved['can_run'])
        with patch.object(app, 'PRIVATE_SKILLS_DIR', self.uploads), \
             patch.object(app, '_require_audit_integrity'), \
             patch.object(app, '_append_audit') as audit:
            result = app.repository_revoke_upload(
                1, record['id'], app.RevocationReason(reason='Known risk; revoke this fixed version'),
                SimpleNamespace(state=SimpleNamespace(user={'id': 3})))
        self.assertEqual(result['state'], 'REVOKED')
        self.assertNotIn('reason', result)
        self.assertEqual(audit.call_args.args[0]['action'], 'repository-revoke')
        revoked = quarantine.read_upload(self.uploads, 1, record['id'])
        self.assertEqual(revoked['workflow_state'], 'REVOKED')
        self.assertFalse(revoked['allowed_for_evaluation'])
        self.assertFalse(revoked['can_run'])
        with self.assertRaises(quarantine.PackageError):
            quarantine.revoke_upload(self.uploads, 1, record['id'], 3, 'Second revocation is forbidden')

    def test_tamper_after_preliminary_approval_auto_revokes_evaluation(self):
        record = quarantine.import_package(self.uploads, 1, package_bytes())
        quarantine.submit_review(self.uploads, 1, record['id'], 2,
                                 'PRELIMINARY_PASS', 'Only permit isolated future evaluation')
        path = self.uploads / '1' / record['id'] / 'sample-skill' / 'runner.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# modified', encoding='utf-8')
        state = quarantine.read_upload(self.uploads, 1, record['id'])
        self.assertEqual(state['content_integrity'], 'FAIL')
        self.assertEqual(state['workflow_state'], 'REVOKED')
        self.assertFalse(state['allowed_for_evaluation'])
        self.assertFalse(state['can_run'])

    def test_revocation_remains_effective_when_central_audit_write_fails(self):
        record = quarantine.import_package(self.uploads, 1, package_bytes())
        with patch.object(app, 'PRIVATE_SKILLS_DIR', self.uploads), \
             patch.object(app, '_require_audit_integrity'), \
             patch.object(app, '_append_audit', side_effect=HTTPException(status_code=503)):
            with self.assertRaisesRegex(HTTPException, '版本已撤销'):
                app.repository_revoke_upload(
                    1, record['id'], app.RevocationReason(reason='Emergency freeze after security review'),
                    SimpleNamespace(state=SimpleNamespace(user={'id': 3})))
        self.assertEqual(quarantine.read_upload(self.uploads, 1, record['id'])['workflow_state'], 'REVOKED')

    def test_review_save_is_reported_when_central_audit_write_fails(self):
        record = quarantine.import_package(self.uploads, 1, package_bytes())
        with patch.object(app, 'PRIVATE_SKILLS_DIR', self.uploads), \
             patch.object(app, '_require_audit_integrity'), \
             patch.object(app, '_append_audit', side_effect=HTTPException(status_code=503)):
            with self.assertRaisesRegex(HTTPException, '初审决定已保存'):
                app.repository_review_decision(
                    1, record['id'],
                    app.ReviewDecision(decision='REJECTED', reason='Unsafe pattern found during manual review'),
                    SimpleNamespace(state=SimpleNamespace(user={'id': 3})))
        self.assertEqual(quarantine.read_upload(self.uploads, 1, record['id'])['workflow_state'], 'REJECTED')

    def test_rejected_version_stays_rejected_even_if_content_later_changes(self):
        record = quarantine.import_package(self.uploads, 1, package_bytes())
        quarantine.submit_review(self.uploads, 1, record['id'], 2,
                                 'REJECTED', 'Unsafe pattern found during manual review')
        path = self.uploads / '1' / record['id'] / 'sample-skill' / 'runner.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# modified', encoding='utf-8')
        state = quarantine.read_upload(self.uploads, 1, record['id'])
        self.assertEqual(state['workflow_state'], 'REJECTED')
        self.assertFalse(state['can_run'])

    def test_zip_slip_credentials_and_oversize_rejected(self):
        for payload in (
            package_bytes(extra={'sample-skill/../outside.txt': 'escape'}),
            package_bytes(extra={'sample-skill/.env': 'secret'}),
            b'x' * (quarantine.MAX_ZIP_BYTES + 1),
        ):
            with self.subTest(size=len(payload)):
                with self.assertRaises(quarantine.PackageError):
                    quarantine.import_package(self.uploads, 1, payload)
        self.assertFalse((self.root / 'outside.txt').exists())
        self.assertEqual(quarantine.list_uploads(self.uploads, 1), [])

    def test_business_api_boundary_is_server_side(self):
        self.assertTrue(app._engineer_route('/api/repository/uploads', 'GET'))
        self.assertTrue(app._engineer_route('/api/repository/review-queue/1/demo/revoke', 'POST'))
        self.assertTrue(app._engineer_route('/api/audit/logs', 'GET'))
        self.assertTrue(app._engineer_route('/api/auth/users', 'POST'))
        self.assertTrue(app._engineer_route('/api/skills/demo/versions', 'GET'))
        self.assertFalse(app._engineer_route('/api/pipelines/run', 'POST'))


if __name__ == '__main__':
    unittest.main()
