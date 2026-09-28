"""线上 Skill 发现与隔离导入；全部使用模拟网络和临时数据。"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from governance import discovery
from security.quarantine import read_upload
from test_phase3 import package_bytes


COMMIT = 'a' * 40
SKILL_DATA = b'---\nname: sample-skill\ndescription: Demo\n---\n'
BLOB = hashlib.sha1(f'blob {len(SKILL_DATA)}\0'.encode() + SKILL_DATA).hexdigest()


class DiscoveryTests(unittest.TestCase):
    def test_self_built_skills_have_explicit_local_provenance(self):
        names = {'skill-doctor', 'skill-report', 'local-pedestrian-detector', 'sample-skill-broken'}
        catalog = {item['name']: item for item in app.CATALOG}
        self.assertTrue(names <= catalog.keys())
        for name in names:
            self.assertEqual(catalog[name]['source'], 'local_project')
        results = app.search_local_catalog('doctor', 'local_project')
        self.assertTrue(results)
        self.assertTrue(all(item['source'] == 'local_project' for item in results))

    def test_result_has_fixed_source_and_is_not_installable(self):
        tree = [{'path': 'skills/sample-skill/SKILL.md', 'type': 'blob', 'sha': BLOB, 'size': len(SKILL_DATA)}]
        with patch.object(discovery, '_api', return_value={'items': []}), patch.object(discovery, '_repo_tree', return_value=(COMMIT, tree)):
            result = discovery.discover_github_skills('sample', 'online')
        self.assertTrue(result['results'])
        item = result['results'][0]
        self.assertEqual(item['repository'], 'NVIDIA/skills')
        self.assertEqual(item['commit_sha'], COMMIT)
        self.assertIn('/blob/' + COMMIT + '/', item['source_url'])
        self.assertEqual(item['status'], 'DISCOVERED')
        self.assertFalse(item['can_download'])
        self.assertFalse(item['can_run'])
        self.assertFalse(item['origin_verified'])

    def test_network_failure_is_visible_without_fabricating_results(self):
        with patch.object(discovery, '_repo_tree', side_effect=discovery.DiscoveryError('offline')):
            result = discovery.discover_github_skills('rag', 'nvidia_online')
        self.assertEqual(result['results'], [])
        self.assertIn('offline', result['warnings'][0])

    def test_license_file_discovery_does_not_claim_license_grant(self):
        tree = [
            {'path': 'skills/sample-skill/SKILL.md', 'type': 'blob', 'sha': BLOB},
            {'path': 'LICENSE', 'type': 'blob', 'sha': BLOB},
        ]
        with patch.object(discovery, '_repo_tree', return_value=(COMMIT, tree)):
            result = discovery.discover_github_skills('sample', 'nvidia_online')
        item = result['results'][0]
        self.assertEqual(item['license'], 'UNKNOWN')
        self.assertEqual(item['license_status'], 'FILE_PRESENT_UNVERIFIED')
        self.assertEqual(item['license_file_url'],
                         f'https://github.com/NVIDIA/skills/blob/{COMMIT}/LICENSE')

    def test_missing_license_file_remains_unknown(self):
        tree = [{'path': 'skills/sample-skill/SKILL.md', 'type': 'blob', 'sha': BLOB}]
        with patch.object(discovery, '_repo_tree', return_value=(COMMIT, tree)):
            item = discovery.discover_github_skills('sample', 'nvidia_online')['results'][0]
        self.assertEqual(item['license_status'], 'UNKNOWN')
        self.assertIsNone(item['license_file_url'])

    def test_rag_does_not_match_storage_substring(self):
        tree = [{'path': 'skills/vss-storage/SKILL.md', 'type': 'blob', 'sha': BLOB},
                {'path': 'skills/rag-eval/SKILL.md', 'type': 'blob', 'sha': BLOB}]
        with patch.object(discovery, '_repo_tree', return_value=(COMMIT, tree)):
            result = discovery.discover_github_skills('rag', 'nvidia_online')
        self.assertEqual([item['name'] for item in result['results']], ['rag-eval'])

    def test_fixed_commit_blob_checked_before_quarantine(self):
        tree = [{'path': 'skills/sample-skill/SKILL.md', 'type': 'blob', 'sha': BLOB, 'size': len(SKILL_DATA)}]
        with patch.object(discovery, '_api', return_value={'tree': tree, 'truncated': False}), patch.object(discovery, '_request', return_value=SKILL_DATA):
            payload = discovery.build_quarantine_zip('demo', 'skills', COMMIT, 'skills/sample-skill')
        self.assertTrue(payload.startswith(b'PK'))
        with patch.object(discovery, '_api', return_value={'tree': tree, 'truncated': False}), patch.object(discovery, '_request', return_value=b'tampered'):
            with self.assertRaisesRegex(discovery.DiscoveryError, '哈希不一致'):
                discovery.build_quarantine_zip('demo', 'skills', COMMIT, 'skills/sample-skill')

    def test_import_stays_quarantined_and_records_provenance(self):
        with tempfile.TemporaryDirectory(prefix='skillpulse-online-test-') as folder:
            root = Path(folder) / 'private'
            request = SimpleNamespace(state=SimpleNamespace(user={'id': 8, 'role': 'engineer'}))
            with patch.object(app, 'PRIVATE_SKILLS_DIR', root), patch.object(app, 'build_quarantine_zip', return_value=package_bytes()):
                result = app.repository_import_github(request, app.GithubImportRequest(
                    repository='demo/skills', commit_sha=COMMIT, skill_path='skills/sample-skill'))
            saved = read_upload(root, 8, result['id'])
            self.assertEqual(saved['provenance']['commit_sha'], COMMIT)
            self.assertFalse(saved['provenance']['origin_verified'])
            self.assertEqual(saved['report']['status'], 'QUARANTINED')
            self.assertFalse(saved['report']['can_run'])
            self.assertIsNone(read_upload(root, 9, result['id']))

    def test_invalid_remote_selector_rejected(self):
        for path in ('../secrets', 'skills/../secret', '.agents/other/skill'):
            with self.subTest(path=path), self.assertRaises(discovery.DiscoveryError):
                discovery.build_quarantine_zip('demo', 'skills', COMMIT, path)


if __name__ == '__main__':
    unittest.main()
