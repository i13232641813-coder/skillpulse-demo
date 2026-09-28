"""本地账号数据归档；只保存无输入值的流程快照和脱敏运行摘要。"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

WORKFLOW_ID = re.compile(r'^[0-9a-f]{16}$')
RUN_ID = re.compile(r'^[0-9a-f]{8}$')


def save_workflow(root: Path, owner_id: int, name: str, snapshot: dict) -> dict:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('流程名称须为 1–80 字')
    folder = root / str(owner_id)
    folder.mkdir(parents=True, exist_ok=True)
    record = {'id': uuid.uuid4().hex[:16], 'owner_id': owner_id, 'name': name.strip(),
              'created_at': datetime.now(timezone.utc).isoformat(), 'snapshot': snapshot,
              'note': '只保存拓扑与版本；不保存输入值、运行结果或密钥'}
    with (folder / (record['id'] + '.json')).open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return record


def read_workflow(root: Path, owner_id: int, workflow_id: str) -> dict | None:
    if not WORKFLOW_ID.fullmatch(workflow_id):
        return None
    path = root / str(owner_id) / (workflow_id + '.json')
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def list_workflows(root: Path, owner_id: int) -> list[dict]:
    folder = root / str(owner_id)
    if not folder.is_dir():
        return []
    return [record for path in sorted(folder.glob('*.json'))
            if (record := read_workflow(root, owner_id, path.stem)) is not None]


def read_run(root: Path, owner_id: int, run_id: str) -> dict | None:
    if not RUN_ID.fullmatch(run_id):
        return None
    path = root / str(owner_id) / ('pipeline-' + run_id + '.json')
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    return record if record.get('owner_id') == owner_id and record.get('run_id') == run_id else None


def list_runs(root: Path, owner_id: int) -> list[dict]:
    folder = root / str(owner_id)
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob('pipeline-*.json')):
        record = read_run(root, owner_id, path.stem.removeprefix('pipeline-'))
        if record:
            result.append({'run_id': record['run_id'], 'started_at': record.get('started_at'),
                           'finished_at': record.get('finished_at'),
                           'passed': record.get('response_summary', {}).get('passed'),
                           'nodes': len(record.get('response_summary', {}).get('nodes', []))})
    return result
