"""显式取得 NVIDIA 固定提交的单一脚本并校验哈希；不下载依赖或执行代码。"""
import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path


COMMIT = 'd8519c57da6db5d9bea274ec1724a4a7a56a3dee'
SCRIPT_SHA256 = '27df79512570edc4965830a74c4082a5604d89f7fac0ec48c7230fb5fa7cdfb9'
BLOB_SHA1 = '55924e6227dab0e971eff34deb15d1763b80bcf4'
URL = ('https://api.github.com/repos/NVIDIA/skills/contents/'
       'skills/skill-card-generator/scripts/validate_submission.py?ref=' + COMMIT)
PROJECT = Path(__file__).resolve().parents[1]
OUTBOX = Path(os.environ.get('SKILLPULSE_OUTBOX_DIR', str(PROJECT / 'workspace' / 'outbox')))
DESTINATION = OUTBOX / 'curated' / 'validate_submission.py'


def main() -> None:
    if DESTINATION.exists():
        if hashlib.sha256(DESTINATION.read_bytes()).hexdigest() == SCRIPT_SHA256:
            print('Fixed upstream action already present and SHA-256 verified.')
            return
        raise SystemExit('Existing curated script differs from pinned SHA-256; refusing to overwrite it.')
    request = urllib.request.Request(URL, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'SkillPulse-Demo',
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        metadata = json.load(response)
    if metadata.get('sha') != BLOB_SHA1 or metadata.get('encoding') != 'base64':
        raise SystemExit('Pinned GitHub blob identity was not returned.')
    content = base64.b64decode(metadata['content'], validate=False)
    if len(content) > 50_000 or hashlib.sha256(content).hexdigest() != SCRIPT_SHA256:
        raise SystemExit('Pinned script size or SHA-256 mismatch; no file was installed.')
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    # 独占创建，不覆盖现有证据或用户文件。
    with DESTINATION.open('xb') as stream:
        stream.write(content)
    print('Installed one pinned upstream action in the private outbox; SHA-256 verified.')


if __name__ == '__main__':
    main()
