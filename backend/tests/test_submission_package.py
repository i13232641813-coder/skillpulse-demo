"""赛事源码包只测试选入路径，不读取私有数据或生成压缩包。"""
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.build_submission import package_files  # noqa: E402


class SubmissionPackageTest(unittest.TestCase):
    def test_windows_powershell_launcher_is_ascii_for_legacy_parser(self):
        launcher = Path(__file__).resolve().parents[2] / 'scripts' / 'start_with_stepfun.ps1'
        launcher.read_text(encoding='ascii')

    def test_submission_whitelist_contains_required_launchers_and_no_private_paths(self):
        names = {name for _, name in package_files()}
        self.assertTrue({'start.cmd', 'start-stepfun.cmd', 'scripts/start_with_stepfun.ps1',
                         'README-STEPFUN-DGX.md', 'DEMO-SCRIPT.md',
                         'RELEASE-CHECKLIST.md'} <= names)
        for name in names:
            parts = name.lower().split('/')
            self.assertNotIn('evidence', parts)
            self.assertNotIn('outbox', parts)
            self.assertNotIn('.venv', parts)
            self.assertFalse(any(part.startswith('.env') for part in parts))


if __name__ == '__main__':
    unittest.main()
