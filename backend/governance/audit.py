"""单进程本地演示用追加哈希链；不能替代外部时间戳或防恶意删除的审计系统。"""
import hashlib
import json
import os
from pathlib import Path
from threading import Lock

_LOCK = Lock()
GENESIS = '0' * 64


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def verify_chain(path: Path) -> dict:
    if not path.exists():
        return {'valid': True, 'count': 0, 'last_hash': GENESIS, 'records': [], 'scope': '仅验证本地哈希链内部完整性'}
    previous = GENESIS
    records = []
    try:
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            item = json.loads(line)
            record = item['record']
            if not isinstance(record, dict) or item['sequence'] != number or item['prev_hash'] != previous:
                raise ValueError(f'第 {number} 条顺序或前向哈希不一致')
            expected = hashlib.sha256(previous.encode('ascii') + _canonical(record)).hexdigest()
            if item['entry_hash'] != expected:
                raise ValueError(f'第 {number} 条内容哈希不一致')
            previous = expected
            records.append(record)
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {'valid': False, 'count': len(records), 'last_hash': previous, 'records': records,
                'error': str(exc), 'scope': '仅验证本地哈希链内部完整性'}
    return {'valid': True, 'count': len(records), 'last_hash': previous, 'records': records,
            'scope': '仅验证本地哈希链内部完整性'}


def append_chained(path: Path, entry: dict) -> dict:
    with _LOCK:
        result = verify_chain(path)
        if not result['valid']:
            raise ValueError('审计链已损坏，拒绝追加: ' + result['error'])
        previous = result['last_hash']
        record = dict(entry)
        item = {
            'sequence': result['count'] + 1,
            'prev_hash': previous,
            'record': record,
            'entry_hash': hashlib.sha256(previous.encode('ascii') + _canonical(record)).hexdigest(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return item
