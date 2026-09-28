"""固定外部动作的门禁测试；不运行 Docker、不访问网络或真实账号。"""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import curated_card_validator as action
from engine.run_local import run_skill
from governance.checks import check_integrity, skill_health


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'workspace' / 'skills' / action.ACTION_NAME


class CuratedActionTests(unittest.TestCase):
    def test_adapter_contract_and_integrity(self):
        ok, detail = check_integrity(SKILL)
        self.assertTrue(ok, detail)
        with patch.object(action, 'availability', return_value=(False, 'isolation unavailable')):
            report = skill_health(SKILL)
        self.assertEqual(report['status'], 'UNHEALTHY')
        self.assertFalse(report['signature_verified'])
        self.assertEqual(next(c for c in report['checks'] if c['name'] == 'Evaluated')['status'], 'FAIL')

    def test_invalid_input_fails_before_container(self):
        with patch.object(action, 'availability') as availability:
            self.assertFalse(run_skill(action.ACTION_NAME, {'card': ''})['ok'])
            self.assertFalse(run_skill(action.ACTION_NAME, {'card': 'x' * (action.MAX_CARD_BYTES + 1)})['ok'])
            availability.assert_not_called()

    def test_pinned_container_success_and_negative_case(self):
        with tempfile.TemporaryDirectory(prefix='skillpulse-action-test-') as folder:
            script = Path(folder) / 'validator.py'
            script.write_bytes(b'fixed test source')
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            with patch.object(action, '_source_path', return_value=script), \
                 patch.object(action, 'SOURCE_SHA256', digest), \
                 patch.object(action, 'availability', return_value=(True, 'ready')), \
                 patch.object(action.subprocess, 'run') as invoke:
                invoke.return_value = subprocess.CompletedProcess([], 0, 'OK: card clean', '')
                passed = run_skill(action.ACTION_NAME, {'card': '# Card\nReviewed.'})
                self.assertTrue(passed['ok'])
                self.assertEqual(passed['outputs']['validation'], 'PASS')
                command = invoke.call_args.args[0]
                self.assertIn('--network', command)
                self.assertIn('none', command)
                self.assertIn('--read-only', command)
                self.assertIn(action.IMAGE_ID, command)
                invoke.return_value = subprocess.CompletedProcess([], 1, '', 'FAIL: unresolved verify-comment')
                blocked = run_skill(action.ACTION_NAME, {'card': '<!-- VERIFY: owner -->'})
                self.assertFalse(blocked['ok'])
                self.assertIn('待人工处理标记', blocked['error'])


if __name__ == '__main__':
    unittest.main()
