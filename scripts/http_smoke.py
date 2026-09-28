"""隔离的端到端 HTTP 验收：不使用真实账号、密钥或历史 outbox。"""
import http.cookiejar
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def request_json(opener, base, path, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'} if body is not None else {}
    request = urllib.request.Request(base + path, data=body, headers=headers)
    with opener.open(request, timeout=12) as response:
        return json.load(response)


def upload_isolated_sample(opener, base):
    """只上传一个含 SKILL.md 的测试 ZIP；隔离区绝不执行其中内容。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('smoke-external/SKILL.md',
                         '---\nname: smoke-external\ndescription: Isolated test asset\n---\nRead-only example.')
    boundary = 'skillpulse-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="package"; '
            'filename="sample.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()
    body += buffer.getvalue() + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(base + '/api/repository/upload', data=body,
                                     headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with opener.open(request, timeout=12) as response:
        return json.load(response)


def main():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='skillpulse-http-smoke-') as isolated_outbox:
        env = dict(os.environ, SKILLPULSE_OUTBOX_DIR=isolated_outbox)
        # 冒烟测试永远不继承真实模型密钥，也不产生付费调用。
        env.pop('STEPFUN_API_KEY', None)
        env.pop('SKILLPULSE_EMBEDDING_MODEL_DIR', None)
        server = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(port)],
            cwd=ROOT / 'backend', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            for _ in range(50):
                if server.poll() is not None:
                    raise RuntimeError('隔离服务未能启动')
                try:
                    request_json(opener, base, '/api/auth/status')
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.2)
            else:
                raise RuntimeError('隔离服务启动超时')

            credentials = {'username': 'smoke-' + uuid.uuid4().hex[:8], 'password': uuid.uuid4().hex}
            request_json(opener, base, '/api/auth/bootstrap', credentials)
            request_json(opener, base, '/api/auth/login', credentials)
            owner_id = request_json(opener, base, '/api/auth/me')['id']
            assert request_json(opener, base, '/api/ai/status')['available'] is False
            try:
                request_json(opener, base, '/api/ai/interpret', {'task': '检查 Skill'})
                raise AssertionError('无 Key 时不应假报模型接入成功')
            except urllib.error.HTTPError as exc:
                assert exc.code == 503
            assert request_json(opener, base, '/api/search/semantic/status')['available'] is False
            try:
                request_json(opener, base, '/api/search/semantic', {'task': '检查 Skill'})
                raise AssertionError('无 CUDA 模型时不应假报语义检索成功')
            except urllib.error.HTTPError as exc:
                assert exc.code == 503
            teammate_credentials = {'username': 'mate-' + uuid.uuid4().hex[:8], 'password': uuid.uuid4().hex}
            request_json(opener, base, '/api/auth/users', {**teammate_credentials, 'role': 'business'})
            teammate = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            request_json(teammate, base, '/api/auth/login', teammate_credentials)
            assert request_json(teammate, base, '/api/auth/me')['role'] == 'business'
            assert request_json(teammate, base, '/api/search/skills', {'messages': [{'role': 'user', 'content': 'skill'}]})['results']
            try:
                request_json(teammate, base, '/api/repository/uploads')
                raise AssertionError('业务账号不应访问专家待审仓库')
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            upload = upload_isolated_sample(opener, base)
            upload_id = upload['id']
            revoke_path = f'/api/repository/review-queue/{owner_id}/{upload_id}/revoke'
            try:
                request_json(teammate, base, revoke_path, {'reason': 'Business account must not revoke a package'})
                raise AssertionError('业务账号不应能撤销隔离包')
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            revoked = request_json(opener, base, revoke_path,
                                   {'reason': 'Explicit emergency revocation in isolated smoke test'})
            assert revoked['state'] == 'REVOKED' and 'reason' not in revoked
            stored = request_json(opener, base, '/api/repository/uploads/' + upload_id)
            assert stored['workflow_state'] == 'REVOKED' and not stored['can_run']
            doctor = request_json(opener, base, '/api/skills/skill-doctor/health')
            formatter = request_json(opener, base, '/api/skills/skill-report/health')
            target = request_json(opener, base, '/api/skills/rag-blueprint/health')
            assert doctor['status'] == formatter['status'] == target['status'] == 'HEALTHY'
            assert not target['signature_verified'] and not target['origin_verified']
            assert target['integrity_verified']
            pipeline = request_json(opener, base, '/api/pipelines/run', {
                'nodes': [
                    {'id': 'n1', 'name': 'skill-doctor', 'control_type': 'skill',
                     'node_schema': doctor['schema'], 'inputs': {'query': 'rag-blueprint'}},
                    {'id': 'n2', 'name': 'skill-report', 'control_type': 'skill',
                     'node_schema': formatter['schema'], 'inputs': {}},
                ],
                'edges': [{'from': 'n1', 'to': 'n2'}],
                'inputs': {'n1': {'query': 'rag-blueprint'}},
                'selected_node': 'n2',
            })
            assert pipeline['passed'] and [node['name'] for node in pipeline['nodes']] == ['skill-doctor', 'skill-report']
            assert 'rag-blueprint' in pipeline['nodes'][1]['outputs']['report']
            try:
                request_json(opener, base, '/api/skills/sample-skill-broken/download')
                raise AssertionError('不健康 Skill 下载未被阻止')
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            with opener.open(base + '/frontend/lego/index.html', timeout=12) as page:
                assert page.status == 200 and b'diagnose-target' in page.read()
            print(json.dumps({
                'http_smoke': 'PASS', 'pipeline': ['skill-doctor', 'skill-report'],
                'target': 'rag-blueprint', 'target_risk': target['risk'],
                'broken_download_http': 403, 'frontend_http': 200,
                'credentials': 'two ephemeral isolated test accounts', 'teammate_role_boundary_http': 403,
                'quarantine_revoked': stored['workflow_state'], 'business_revoke_http': 403,
            }, ensure_ascii=False))
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == '__main__':
    main()
