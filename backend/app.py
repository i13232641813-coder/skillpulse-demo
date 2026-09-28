from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Dict, Any
import uuid
import json
import os
import socket
import io
import re
import hashlib
import zipfile
import time
from threading import Lock
from datetime import datetime

PROJECT_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = Path(os.environ.get('SKILLPULSE_WORKSPACE_DIR', str(PROJECT_DIR / 'workspace'))).resolve()
SKILLS_DIR = BASE_DIR / 'skills'
OUTBOX_DIR = Path(os.environ.get('SKILLPULSE_OUTBOX_DIR', str(BASE_DIR / 'outbox'))).resolve()
REPORTS_DIR = OUTBOX_DIR / 'reports'
CATALOG_DIR = OUTBOX_DIR / 'catalogs'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_DIR.mkdir(parents=True, exist_ok=True)

CATALOG_PATH = Path(__file__).resolve().parent / "catalog" / "skills.json"
CATALOG = []
if CATALOG_PATH.exists():
    try:
        CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        CATALOG = []
if not isinstance(CATALOG, list):
    CATALOG = []


app = FastAPI(title='SkillPulse Demo', version='0.3.0')

from fastapi.staticfiles import StaticFiles
from governance.schema import load_skill_schema, normalize_skill_meta
from engine.run_local import run_skill
from governance.checks import skill_health as evaluate_skill_health
from governance.audit import append_chained, verify_chain
from governance.versions import build_snapshot, store_snapshot, verify_snapshot, snapshot_path
from governance.discovery import DiscoveryError, discover_github_skills, build_quarantine_zip
from stepfun_client import StepFunError, interpret as interpret_with_stepfun, status as stepfun_status
from gpu_semantic import SemanticUnavailable, rank as rank_on_cuda, status as gpu_semantic_status
from engine.controls import CONTROL_SCHEMAS, compatible_type, coerce_control_value, value_matches_type
from security.local_auth import AuthError, CURRENT_ACTOR, SESSION_SECONDS, create_user, get_user, has_users, login, logout
from security.quarantine import (PackageError, MAX_ZIP_BYTES, import_package, list_uploads, read_upload,
                                 list_review_queue, review_files, preview_file, submit_review, revoke_upload)
from security.user_data import (save_workflow as persist_workflow, read_workflow, list_workflows,
                                read_run, list_runs)
app.mount('/frontend', StaticFiles(directory=str(Path(__file__).resolve().parent.parent / 'frontend'), html=True), name='frontend')

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

AUTH_DB = OUTBOX_DIR / 'accounts.sqlite3'
PRIVATE_SKILLS_DIR = OUTBOX_DIR / 'private_skills'
USER_WORKFLOWS_DIR = OUTBOX_DIR / 'user_workflows'
USER_RUNS_DIR = OUTBOX_DIR / 'user_runs'
SESSION_COOKIE = 'skillpulse_session'
AI_CALLS: Dict[int, List[float]] = {}
AI_CALLS_LOCK = Lock()


def _engineer_route(path: str, method: str) -> bool:
    """专家 API 的后端边界；业务用户可检索和运行既有健康 Skill。"""
    return (path.startswith(('/api/audit', '/api/reports', '/api/repository', '/api/environment', '/api/demo/'))
            or path == '/api/auth/users'
            or path == '/api/pipelines/export'
            or (path.startswith('/api/skills/') and (
                '/versions' in path or '/snapshots/' in path or path.endswith('/repair'))))


@app.middleware('http')
async def require_local_session(request: Request, call_next):
    path = request.url.path
    if request.method == 'OPTIONS' or not path.startswith('/api/') or path in {'/api/auth/status', '/api/auth/bootstrap', '/api/auth/login'}:
        return await call_next(request)
    user = get_user(AUTH_DB, request.cookies.get(SESSION_COOKIE))
    if user is None:
        return JSONResponse({'detail': '请先登录本地 SkillPulse 演示账号'}, status_code=401)
    if _engineer_route(path, request.method) and user['role'] != 'engineer':
        return JSONResponse({'detail': '此功能仅专家角色可用'}, status_code=403)
    request.state.user = user
    actor_token = CURRENT_ACTOR.set(user['id'])
    try:
        return await call_next(request)
    finally:
        CURRENT_ACTOR.reset(actor_token)


class Credentials(BaseModel):
    username: str
    password: str


class NewUser(Credentials):
    role: str


@app.get('/api/auth/status')
def auth_status(request: Request):
    return {'initialized': has_users(AUTH_DB), 'user': get_user(AUTH_DB, request.cookies.get(SESSION_COOKIE))}


@app.post('/api/auth/bootstrap')
def auth_bootstrap(request: Request, credentials: Credentials):
    if request.client is None or request.client.host not in {'127.0.0.1', '::1', 'localhost'}:
        raise HTTPException(status_code=403, detail='首次初始化仅允许从本机访问')
    try:
        return create_user(AUTH_DB, credentials.username, credentials.password, 'engineer', bootstrap=True)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/auth/login')
def auth_login(credentials: Credentials):
    try:
        token, user = login(AUTH_DB, credentials.username, credentials.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response = JSONResponse({'user': user})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite='strict', path='/api')
    return response


@app.post('/api/auth/logout')
def auth_logout(request: Request):
    logout(AUTH_DB, request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({'ok': True})
    response.delete_cookie(SESSION_COOKIE, path='/api')
    return response


@app.get('/api/auth/me')
def auth_me(request: Request):
    return request.state.user


@app.post('/api/auth/users')
def auth_create_user(credentials: NewUser):
    try:
        return create_user(AUTH_DB, credentials.username, credentials.password, credentials.role)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/repository/upload')
async def repository_upload(request: Request, package: UploadFile = File(...)):
    if not package.filename or not package.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail='只接受 ZIP 格式的 Skill 包')
    payload = await package.read(MAX_ZIP_BYTES + 1)
    try:
        return import_package(PRIVATE_SKILLS_DIR, request.state.user['id'], payload)
    except PackageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class GithubImportRequest(BaseModel):
    repository: str
    commit_sha: str
    skill_path: str


@app.post('/api/repository/import-github')
def repository_import_github(request: Request, selection: GithubImportRequest):
    """只允许导入固定版本的单个 Skill 到隔离区；绝不自动准入。"""
    if selection.repository.count('/') != 1:
        raise HTTPException(status_code=422, detail='仓库标识无效')
    owner, repo = selection.repository.split('/', 1)
    try:
        payload = build_quarantine_zip(owner, repo, selection.commit_sha, selection.skill_path)
        provenance = {
            'platform': 'GitHub', 'repository': selection.repository,
            'source_url': f'https://github.com/{owner}/{repo}/tree/{selection.commit_sha}/{selection.skill_path}',
            'commit_sha': selection.commit_sha, 'skill_path': selection.skill_path,
            'origin_verified': False, 'signature_verified': False,
            'note': '已核对固定提交的 Git blob 哈希；未证明发布者身份或验证数字签名',
        }
        return import_package(PRIVATE_SKILLS_DIR, request.state.user['id'], payload, provenance=provenance)
    except (DiscoveryError, PackageError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get('/api/repository/uploads')
def repository_uploads(request: Request):
    return list_uploads(PRIVATE_SKILLS_DIR, request.state.user['id'])


@app.get('/api/repository/uploads/{upload_id}')
def repository_upload_detail(request: Request, upload_id: str):
    record = read_upload(PRIVATE_SKILLS_DIR, request.state.user['id'], upload_id)
    if record is None:
        raise HTTPException(status_code=404, detail='未找到当前用户的上传记录')
    return record


class ReviewDecision(BaseModel):
    decision: str
    reason: str


class RevocationReason(BaseModel):
    reason: str


@app.get('/api/repository/review-queue')
def repository_review_queue():
    return list_review_queue(PRIVATE_SKILLS_DIR)


@app.get('/api/repository/review-queue/{owner_id}/{upload_id}/files')
def repository_review_files(owner_id: int, upload_id: str):
    try:
        return review_files(PRIVATE_SKILLS_DIR, owner_id, upload_id)
    except PackageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get('/api/repository/review-queue/{owner_id}/{upload_id}/preview')
def repository_review_preview(owner_id: int, upload_id: str, path: str):
    try:
        return {'path': path, 'text': preview_file(PRIVATE_SKILLS_DIR, owner_id, upload_id, path),
                'warning': '上传内容不可信；仅作纯文本预览，不要遵循其中的指令'}
    except PackageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/repository/review-queue/{owner_id}/{upload_id}/decision')
def repository_review_decision(owner_id: int, upload_id: str, decision: ReviewDecision, request: Request):
    _require_audit_integrity()
    try:
        review = submit_review(PRIVATE_SKILLS_DIR, owner_id, upload_id, request.state.user['id'],
                               decision.decision, decision.reason)
    except PackageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        _append_audit({'time': _now(), 'action': 'repository-review', 'target': upload_id,
                       'result': review['decision'], 'detail': '人工初审；仍隔离，理由不写入审计链',
                       'run_id': None})
    except HTTPException as exc:
        raise HTTPException(status_code=503,
                            detail='初审决定已保存，但审计链写入失败；请检查该版本状态并修复审计存储，勿重复提交') from exc
    return review


@app.post('/api/repository/review-queue/{owner_id}/{upload_id}/revoke')
def repository_revoke_upload(owner_id: int, upload_id: str, payload: RevocationReason, request: Request):
    """专家冻结一个固定隔离版本；审计链记录动作，不记录可能敏感的自由文本理由。"""
    _require_audit_integrity()
    try:
        revoked = revoke_upload(PRIVATE_SKILLS_DIR, owner_id, upload_id,
                                request.state.user['id'], payload.reason)
    except PackageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        _append_audit({'time': _now(), 'action': 'repository-revoke', 'target': upload_id,
                       'result': 'REVOKED', 'detail': '专家已冻结隔离版本；理由仅保存在撤销记录中',
                       'run_id': None})
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail='版本已撤销，但审计链写入失败；请检查撤销记录并修复审计存储') from exc
    return {key: value for key, value in revoked.items() if key != 'reason'}

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: Optional[str] = 'step-3.7-flash'
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    source: Optional[str] = 'auto'


class IntentRequest(BaseModel):
    task: str = Field(min_length=3, max_length=500)
    scene: str = Field(default='diagnostics', max_length=40)
    environment: str = Field(default='', max_length=160)

class ChatChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str]

class ChatUsage(BaseModel):
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]

class ChatResponse(BaseModel):
    id: str
    object: str = 'chat.completion'
    created: int
    model: str
    choices: List[ChatChoice]
    usage: ChatUsage

class SkillSummary(BaseModel):
    name: str
    path: str
    status: str
    catalog: str
    scanned: str
    evaluated: str
    signed: str
    documented: str
    risk: str = 'HIGH'
    origin_verified: bool = False
    signature_verified: bool = False
    integrity_verified: bool = False
    provenance_note: str = ''
    checks: List[dict] = []
    schema_: Optional[Dict[str, Any]] = Field(default=None, alias='schema')

class HealthReport(BaseModel):
    skill: str
    status: str
    trust: str
    risk: str = 'HIGH'
    origin_verified: bool = False
    signature_verified: bool = False
    integrity_verified: bool = False
    provenance_note: str = ''
    version: Optional[str] = None
    schema_: Optional[Dict[str, Any]] = Field(default=None, alias='schema')
    checks: List[dict]
    repair: List[str]
    verdict: str

class LegoNode(BaseModel):
    id: str
    name: str
    x: Optional[int] = None
    y: Optional[int] = None
    control_type: str = 'skill'
    input_type: str = 'any'
    output_type: str = 'any'
    node_schema: Optional[Dict[str, Any]] = None
    inputs: Any = {}
    model_config = ConfigDict(extra="ignore")

class LegoEdge(BaseModel):
    from_: str = Field(alias='from')
    to: str
    route: Optional[str] = None
    model_config = ConfigDict(populate_by_name=True)


class SkillSchema(BaseModel):
    title: str
    type: str = "object"
    required: List[str] = []
    properties: Dict[str, Any] = {}

class LegoRunRequest(BaseModel):
    nodes: List[LegoNode]
    edges: List[LegoEdge]
    inputs: Any = None
    selected_node: Optional[str] = None

class LegoRunResponse(BaseModel):
    passed: bool
    run_id: str
    started_at: str
    finished_at: str
    topology: Dict[str, Any]
    selected_node: Optional[Dict[str, Any]] = None
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    logs: List[Dict[str, Any]]
    artifacts: List[Dict[str, Any]]
    exports: Dict[str, str]

class AuditLog(BaseModel):
    time: str
    action: str
    target: str
    result: str
    detail: Optional[str] = None
    run_id: Optional[str] = None
    node_id: Optional[str] = None


class OperationAuditRequest(BaseModel):
    action: str
    target: str
    node_id: Optional[str] = None


class CompatibilityRequest(BaseModel):
    skill_name: str

class PipelineExportRequest(BaseModel):
    nodes: List[LegoNode]
    edges: List[LegoEdge]
    format: Optional[str] = 'yaml'
    generated_at: Optional[str] = None


@app.get('/api/controls')
def control_schemas():
    return CONTROL_SCHEMAS

AUDIT_PATH = REPORTS_DIR / "audit.jsonl"
AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
VERSIONS_DIR = REPORTS_DIR.parent / "versions"
VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

@app.get('/api/skills')
def list_skills() -> List[SkillSummary]:
    results = []
    if not SKILLS_DIR.exists():
        return results
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        report = _health_check(skill_dir)
        results.append(SkillSummary(
            name=skill_dir.name,
            path=str(skill_dir),
            status=report['status'],
            catalog=report['checks'][0]['status'],
            scanned=report['checks'][1]['status'],
            evaluated=report['checks'][3]['status'],
            signed=report['checks'][2]['status'],
            documented=report['checks'][4]['status'],
            risk=report['risk'],
            origin_verified=report['origin_verified'],
            signature_verified=report['signature_verified'],
            integrity_verified=report['integrity_verified'],
            provenance_note=report['provenance_note'],
            checks=report['checks'],
            schema=report.get('schema'),
        ))
    return results


def _skill_dir(skill_name: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', skill_name):
        raise HTTPException(status_code=400, detail='Invalid skill name')
    skill_dir = (SKILLS_DIR / skill_name).resolve()
    if skill_dir.parent != SKILLS_DIR.resolve() or not skill_dir.is_dir():
        raise HTTPException(status_code=404, detail='Skill not found')
    return skill_dir

@app.get('/api/skills/{skill_name}/health', response_model=HealthReport)
def skill_health(skill_name: str) -> HealthReport:
    skill_dir = _skill_dir(skill_name)
    report = _health_check(skill_dir)
    return HealthReport(**report)


@app.get('/api/skills/{skill_name}/download')
def download_skill(skill_name: str):
    """仅打包当前全部检查通过的本地 Skill；ZIP 在内存生成，不写入项目目录。"""
    skill_dir = _skill_dir(skill_name)
    report = _health_check(skill_dir)
    if report['status'] != 'HEALTHY':
        raise HTTPException(status_code=403, detail={
            'message': 'Skill 未通过可信准入，禁止下载',
            'risk': report['risk'],
            'origin_verified': report['origin_verified'],
            'signature_verified': report['signature_verified'],
            'integrity_verified': report['integrity_verified'],
            'provenance_note': report['provenance_note'],
            'checks': report['checks'],
        })
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill_dir.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc' and path.name != 'skill.oms.sig':
                archive.write(path, Path(skill_name) / path.relative_to(skill_dir))
    _append_audit({'time': _now(), 'action': 'operation-download', 'target': skill_name,
                   'result': 'recorded', 'detail': None, 'run_id': None})
    return Response(
        content=buffer.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{skill_name}.zip"'},
    )

@app.post('/api/skills/{skill_name}/repair')
def repair_skill(skill_name: str):
    _skill_dir(skill_name)
    raise HTTPException(status_code=409, detail='本轮禁用自动修复：自动补文档或伪造签名会产生虚假的可信状态。请人工修复后重新安检。')

@app.get('/api/reports')
def list_reports() -> List[dict]:
    reports = []
    if REPORTS_DIR.exists():
        for path in sorted(REPORTS_DIR.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                reports.append({'file': path.name, 'skill': data.get('skill'), 'status': data.get('status'), 'time': data.get('time')})
            except Exception:
                pass
    return reports

@app.get('/api/reports/{skill_name}')
def get_report(skill_name: str):
    path = REPORTS_DIR / f'{skill_name}.json'
    if not path.exists():
        raise HTTPException(status_code=404, detail='Report not found')
    return json.loads(path.read_text(encoding='utf-8'))

@app.get('/api/demo/download-report/{skill_name}')
def download_report(skill_name: str):
    skill_dir = _skill_dir(skill_name)
    report = _health_check(skill_dir)
    report['time'] = _now()
    return Response(
        content=json.dumps(report, ensure_ascii=False, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{skill_name}-report.json"'},
    )

@app.get('/api/lego/skills')
def lego_skills() -> List[Dict[str, Any]]:
    results = []
    if not SKILLS_DIR.exists():
        return results
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        report = _health_check(skill_dir)
        results.append({
            'name': skill_dir.name,
            'source': next((item.get('source') for item in CATALOG if item.get('name') == skill_dir.name), 'local_project'),
            'status': report['status'],
            'trust': report['trust'],
            'risk': report['risk'],
            'origin_verified': report['origin_verified'],
            'signature_verified': report['signature_verified'],
            'integrity_verified': report['integrity_verified'],
            'provenance_note': report['provenance_note'],
            'use': _infer_use(skill_dir.name),
            'description': (report.get('schema') or {}).get('description', ''),
            'schema': report.get('schema'),
            'version': report.get('version'),
            'checks': report['checks'],
        })
    return results

@app.post('/api/pipelines/run', response_model=LegoRunResponse)
def lego_run(req: LegoRunRequest):
    _require_audit_integrity()
    started_at = _now()
    if not req.nodes:
        raise HTTPException(status_code=400, detail='nodes required')
    node_names = [n.name for n in req.nodes if n.control_type == 'skill']
    seen = set(node_names)
    if len(seen) != len(node_names):
        raise HTTPException(status_code=400, detail='duplicate skill nodes in pipeline')
    node_map = {n.id: {
        'id': n.id, 'name': n.name, 'x': n.x, 'y': n.y,
        'control_type': n.control_type, 'input_type': n.input_type, 'output_type': n.output_type,
        'node_schema': n.node_schema or {}, 'inputs': n.inputs or {}
    } for n in req.nodes}
    if len(node_map) != len(req.nodes):
        raise HTTPException(status_code=400, detail='duplicate node ids in pipeline')
    for edge in req.edges:
        if edge.from_ not in node_map or edge.to not in node_map:
            raise HTTPException(status_code=422, detail='连线引用了不存在的节点')
        if edge.from_ == edge.to:
            raise HTTPException(status_code=422, detail='节点不能连接自身')
        source_type = node_map[edge.from_]['control_type']
        if source_type == 'if_branch':
            if edge.route not in ('true', 'false'):
                raise HTTPException(status_code=422, detail='分支出线必须指定 route=true 或 route=false')
        elif edge.route is not None:
            raise HTTPException(status_code=422, detail='只有分支出线可以指定 route')
    if len({(e.from_, e.to, e.route) for e in req.edges}) != len(req.edges):
        raise HTTPException(status_code=422, detail='重复连线')
    for node_id, node in node_map.items():
        if node['control_type'] == 'if_branch':
            routes = [e.route for e in req.edges if e.from_ == node_id]
            if not (req.selected_node == node_id and len(req.nodes) == 1 and not req.edges) and sorted(routes) != ['false', 'true']:
                raise HTTPException(status_code=422, detail='分支节点必须分别连接一条 true 和 false 出线')
    node_health = {}
    for node_id, node in node_map.items():
        if node['control_type'] in CONTROL_SCHEMAS:
            if node['name'] != node['control_type']:
                raise HTTPException(status_code=422, detail='控制节点 name 与 control_type 不一致')
            node_health[node_id] = {
                'status': 'HEALTHY', 'trust': 'LOCAL', 'risk': 'LOW',
                'version': CONTROL_SCHEMAS[node['control_type']]['version'],
                'schema': CONTROL_SCHEMAS[node['control_type']], 'checks': [],
            }
            node['node_schema'] = node_health[node_id]['schema']
            continue
        if node['control_type'] != 'skill':
            raise HTTPException(status_code=422, detail=f'不支持的控制节点: {node["name"]}')
        health = _health_check(_skill_dir(node['name']))
        if health['status'] != 'HEALTHY':
            raise HTTPException(status_code=403, detail={
                'message': f'Skill 未通过可信准入，禁止运行: {node["name"]}',
                'risk': health['risk'], 'checks': health['checks'],
            })
        node_health[node_id] = health
        # Schema is always sourced from the validated on-disk Skill contract, never the request body.
        node['node_schema'] = health['schema']
    for e in req.edges:
        src = node_map.get(e.from_)
        dst = node_map.get(e.to)
        if not src or not dst:
            continue
        out_types = {port['type'] for port in node_health[e.from_]['schema']['outputs']}
        destination_ports = node_health[e.to]['schema']['inputs']
        if dst['control_type'] in CONTROL_SCHEMAS:
            destination_ports = [port for port in destination_ports if port['name'] == 'value']
        in_types = {port['type'] for port in destination_ports}
        compatible = any(compatible_type(a, b) for a in out_types for b in in_types)
        if not compatible:
            raise HTTPException(status_code=422, detail='类型不匹配：' + src['name'] + ' 输出 ' + ','.join(sorted(out_types)) + ' -> ' + dst['name'] + ' 输入 ' + ','.join(sorted(in_types)))
    control_inputs = {nid: dict(node.get('inputs') or {}) for nid, node in node_map.items()}
    if isinstance(req.inputs, dict):
        roots = [nid for nid in node_map if not any(e.to == nid for e in req.edges)]
        for nid in node_map:
            if isinstance(req.inputs.get(nid), dict):
                control_inputs[nid].update(req.inputs[nid])
            elif nid in roots and not any(isinstance(value, dict) for value in req.inputs.values()):
                control_inputs[nid].update({k: v for k, v in req.inputs.items() if not str(k).startswith('_')})
    elif isinstance(req.inputs, list) and req.inputs:
        roots = [nid for nid in node_map if not any(e.to == nid for e in req.edges)]
        if roots:
            control_inputs[roots[0]].update({port['name']: req.inputs[i] for i, port in enumerate(node_health[roots[0]]['schema']['inputs']) if i < len(req.inputs)})
    for node_id, node in node_map.items():
        if node['control_type'] == 'loop':
            target_name = control_inputs[node_id].get('target_skill')
            if not isinstance(target_name, str) or not target_name.strip():
                raise HTTPException(status_code=422, detail='循环节点缺少 target_skill')
            target_health = _health_check(_skill_dir(target_name))
            if target_health['status'] != 'HEALTHY':
                raise HTTPException(status_code=403, detail='循环目标 Skill 未通过安检')
    involved_skills = [node['name'] for node in node_map.values() if node['control_type'] == 'skill']
    involved_skills += [control_inputs[node_id]['target_skill'] for node_id, node in node_map.items() if node['control_type'] == 'loop']
    if involved_skills:
        dependency_check = _dependency_compatibility(involved_skills)
        if not dependency_check['compatible']:
            raise HTTPException(status_code=422, detail={'message': '依赖声明存在冲突或无法验证，禁止运行', 'notes': dependency_check['notes']})
    ordered = []
    if req.edges:
        from collections import deque
        indegree = {n.id: 0 for n in req.nodes}
        adj = {n.id: [] for n in req.nodes}
        for e in req.edges:
            if e.from_ not in adj or e.to not in adj:
                continue
            adj[e.from_].append(e.to)
            indegree[e.to] += 1
        q = deque([nid for nid in node_map if indegree[nid] == 0])
        while q:
            cur = q.popleft()
            ordered.append(cur)
            for nxt in adj[cur]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    q.append(nxt)
        if len(ordered) != len(node_map):
            raise HTTPException(status_code=422, detail='拓扑包含循环依赖')
    else:
        ordered = [n.id for n in req.nodes]
    new_nodes = []
    logs = []
    passed = True
    run_id = uuid.uuid4().hex[:8]
    edge_dicts = []
    active_edges = set()
    outputs_by_node = {}
    for nid in ordered:
        skill = node_map[nid]
        result = None
        incoming = [(index, edge) for index, edge in enumerate(req.edges) if edge.to == nid]
        skipped = bool(incoming) and not any(index in active_edges for index, _ in incoming)
        if skipped:
            provided = {}
            result = {'ok': False, 'outputs': {}, 'artifacts': [], 'skipped': True, 'elapsed_ms': 0}
        else:
            provided = dict(control_inputs.get(nid, {}))
            meta = node_health[nid]['schema']
            for edge_index, edge in incoming:
                if edge_index not in active_edges:
                    continue
                parent_outputs = outputs_by_node.get(edge.from_, {})
                parent_ports = node_health[edge.from_]['schema']['outputs']
                input_ports = meta['inputs']
                if skill['control_type'] in CONTROL_SCHEMAS and parent_outputs:
                    provided['value'] = parent_outputs.get('value', next(iter(parent_outputs.values())))
                    continue
                if node_map[edge.from_]['control_type'] in CONTROL_SCHEMAS and len(input_ports) == 1:
                    provided[input_ports[0]['name']] = parent_outputs.get('value')
                    continue
                for port in input_ports:
                    if port['name'] in parent_outputs:
                        provided[port['name']] = parent_outputs[port['name']]
                    elif len(parent_ports) == 1 and len(input_ports) == 1 and parent_ports[0]['name'] in parent_outputs:
                        provided[port['name']] = parent_outputs[parent_ports[0]['name']]
            for port in meta['inputs']:
                key = port['name']
                if port['type'] != 'json' or not isinstance(provided.get(key), str):
                    continue
                if not provided[key].strip() and not port['required']:
                    provided.pop(key)
                else:
                    try:
                        provided[key] = json.loads(provided[key])
                    except json.JSONDecodeError:
                        pass  # 下方输入类型校验返回可读的 422。
            missing = [
                port['name'] for port in meta['inputs']
                if port['required'] and (
                    port['name'] not in provided
                    or provided[port['name']] is None
                    or isinstance(provided[port['name']], str) and not provided[port['name']].strip()
                )
            ]
            if missing:
                raise HTTPException(status_code=422, detail=f'{skill["name"]} 缺少必填输入: {", ".join(missing)}')
            invalid = []
            for port in meta['inputs']:
                if port['name'] not in provided:
                    continue
                value, kind = provided[port['name']], port['type']
                if skill['control_type'] in CONTROL_SCHEMAS:
                    value = coerce_control_value(value, kind)
                    provided[port['name']] = value
                if not value_matches_type(value, kind):
                    invalid.append(port['name'] + ':' + kind)
            if invalid:
                raise HTTPException(status_code=422, detail=f'{skill["name"]} 输入类型不匹配: {", ".join(invalid)}')
            control_type = skill.get('control_type', 'skill')
            if control_type == 'if_branch':
                route = 'true' if provided['condition'] else 'false'
                result = {
                    'ok': True,
                    'outputs': {'value': provided['value']}, 'route': route,
                    'artifacts': [],
                    'elapsed_ms': 0,
                }
            elif control_type == 'loop':
                count = provided['count']
                if count < 1 or count > 5:
                    raise HTTPException(status_code=422, detail='循环次数必须在 1–5 之间')
                target_name = provided['target_skill']
                target_report = _health_check(_skill_dir(target_name))
                if target_report['status'] != 'HEALTHY':
                    raise HTTPException(status_code=403, detail='循环目标 Skill 未通过安检')
                target_inputs = target_report['schema']['inputs']
                target_outputs = target_report['schema']['outputs']
                required_inputs = [port for port in target_inputs if port['required']]
                if len(required_inputs) != 1 or len(target_outputs) != 1:
                    raise HTTPException(status_code=422, detail='循环目标 Skill 必须恰有一个必填输入和一个输出端口')
                current_value = provided['value']
                iterations = []
                for _ in range(count):
                    if not value_matches_type(current_value, required_inputs[0]['type']):
                        raise HTTPException(status_code=422, detail='循环输入与目标 Skill 端口类型不匹配')
                    try:
                        item = run_skill(target_name, {required_inputs[0]['name']: current_value})
                    except Exception:
                        item = {'ok': False, 'outputs': {}, 'artifacts': [], 'error': 'runner_failed'}
                    iterations.append(item)
                    if not item.get('ok'):
                        break
                    current_value = item.get('outputs', {}).get(target_outputs[0]['name'])
                    if not value_matches_type(current_value, target_outputs[0]['type']):
                        raise HTTPException(status_code=422, detail='循环目标 Skill 输出类型与声明不符')
                ok = all(item.get('ok') for item in iterations)
                result = {
                    'ok': ok,
                    'outputs': {'value': current_value, 'results': [item.get('outputs') for item in iterations]},
                    'artifacts': [],
                    'error': 'loop_target_failed' if not ok else None,
                    'elapsed_ms': sum(item.get('elapsed_ms', 0) for item in iterations),
                    'iterations': len(iterations), 'target_skill': target_name,
                }
            else:
                try:
                    result = run_skill(skill['name'], provided)
                except Exception as exc:
                    result = {'ok': False, 'outputs': {}, 'artifacts': [], 'error': str(exc)}
            if result.get('ok'):
                outputs_by_node[nid] = result.get('outputs') or {}
                for edge_index, edge in enumerate(req.edges):
                    if edge.from_ == nid and (control_type != 'if_branch' or edge.route == result['route']):
                        active_edges.add(edge_index)
            else:
                passed = False
        report = node_health[nid]
        healthy = report['status'] == 'HEALTHY'
        if not healthy:
            passed = False
        status = report['status']
        trust = report['trust']
        failed = [] if healthy else [c['name'] for c in report['checks'] if c['status'] != 'PASS']
        status = 'SKIPPED' if skipped else 'HEALTHY' if bool(result and result.get('ok')) else 'UNHEALTHY'
        trust = report['trust'] if status == 'HEALTHY' else 'LOW'
        failed = [] if status in ('HEALTHY', 'SKIPPED') else [str((result or {}).get('error') or 'runner_failed')]
        input_bytes = json.dumps(provided, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
        output_bytes = json.dumps((result or {}).get('outputs') or {}, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
        result['input_summary'] = {'fields': sorted(provided), 'sha256': hashlib.sha256(input_bytes).hexdigest()}
        result['output_summary'] = {'fields': sorted(((result or {}).get('outputs') or {}).keys()), 'sha256': hashlib.sha256(output_bytes).hexdigest()}
        node_summary = {
            'id': skill['id'],
            'name': skill['name'],
            'version': report.get('version'),
            'x': skill['x'],
            'y': skill['y'],
            'status': status,
            'trust': trust,
            'control_type': skill.get('control_type', 'skill'),
            'input_type': skill.get('input_type', 'any'),
            'output_type': skill.get('output_type', 'any'),
            'schema': skill.get('node_schema') or {},
            'failed': failed,
            'checks': report['checks'],
            'result': result,
            'outputs': result.get('outputs') or {},
            'elapsed_ms': result.get('elapsed_ms'),
            'input_summary': result['input_summary'],
            'output_summary': result['output_summary'],
        }
        if skill['name'] == 'nvidia-skill-card-validator':
            node_summary['source'] = (result or {}).get('source')
        new_nodes.append(node_summary)
        logs.append({'time': _now(), 'action': 'skip' if skipped else 'run', 'target': skill['name'], 'result': '跳过' if skipped else '通过' if healthy and status == 'HEALTHY' else '阻断', 'detail': f'v{report.get("version")} · {(result or {}).get("elapsed_ms", 0)}ms'})
        _append_audit({
            'time': started_at,
            'action': 'skip-node' if skipped else 'run-node',
            'target': skill['name'],
            'result': node_summary['status'],
            'detail': json.dumps({'version': report.get('version'), 'input_summary': result['input_summary'], 'output_summary': result['output_summary'], 'source': node_summary.get('source'), 'error': 'runner_failed' if not result.get('ok') else None}, ensure_ascii=False),
            'run_id': run_id,
            'node_id': nid,
        })
    edge_dicts = [{'from': e.from_, 'to': e.to, 'route': e.route, 'active': index in active_edges} for index, e in enumerate(req.edges)]
    for index, e in enumerate(req.edges):
        edge_active = index in active_edges
        logs.append({'time': _now(), 'action': 'link', 'target': node_map.get(e.from_, {}).get('name') + ' -> ' + node_map.get(e.to, {}).get('name'), 'result': '已连接' if edge_active else '未走此路径', 'detail': '链路'})
        _append_audit({
            'time': _now(),
            'action': 'link',
            'target': node_map.get(e.from_, {}).get('name') + ' -> ' + node_map.get(e.to, {}).get('name'),
            'result': '已连接' if edge_active else '未走此路径',
            'detail': '链路',
            'run_id': run_id,
        })
    logs.append({'time': _now(), 'action': 'verdict', 'target': 'pipeline', 'result': '通过' if passed else '阻断', 'detail': 'SkillPulse 治理检查 + 运行结果'})
    run_finished = datetime.now()
    artifacts = []
    exports = {'json': '/api/pipelines/export?run_id=' + run_id, 'openapi': '/api/pipelines/export?format=openapi&run_id=' + run_id}
    selected_node = None
    if req.selected_node:
        selected = next((n for n in new_nodes if n['id'] == req.selected_node), None)
        if selected:
            selected_node = {
                'id': selected['id'],
                'name': selected['name'],
                'status': selected['status'],
                'trust': selected['trust'],
                'control_type': selected.get('control_type', 'skill'),
                'outputs': (selected.get('result') or {}).get('outputs') or {},
                'artifacts': (selected.get('result') or {}).get('artifacts') or [],
                'error': (selected.get('result') or {}).get('error'),
                'elapsed_ms': (selected.get('result') or {}).get('elapsed_ms'),
            }
            if selected_node['artifacts']:
                artifacts.extend(selected_node['artifacts'] if isinstance(selected_node['artifacts'], list) else [selected_node['artifacts']])
    if not selected_node:
        first = next((n for n in new_nodes if n['status'] == 'HEALTHY'), new_nodes[0] if new_nodes else None)
        if first:
            selected_node = {
                'id': first['id'],
                'name': first['name'],
                'status': first['status'],
                'trust': first['trust'],
                'control_type': first.get('control_type', 'skill'),
                'outputs': (first.get('result') or {}).get('outputs') or {},
                'artifacts': (first.get('result') or {}).get('artifacts') or [],
                'error': (first.get('result') or {}).get('error'),
                'elapsed_ms': (first.get('result') or {}).get('elapsed_ms'),
            }
    actor_id = CURRENT_ACTOR.get()
    run_folder = USER_RUNS_DIR / str(actor_id) if actor_id is not None else CATALOG_DIR
    run_folder.mkdir(parents=True, exist_ok=True)
    pipeline_file = run_folder / f'pipeline-{run_id}.json'
    safe_request = {
        'nodes': [{'id': node_id, 'name': node['name'], 'version': node_health[node_id].get('version')} for node_id, node in node_map.items()],
        'edges': [{'from': edge.from_, 'to': edge.to, 'route': edge.route} for edge in req.edges],
        'input_fields': sorted(req.inputs.keys()) if isinstance(req.inputs, dict) else [],
        'input_sha256': hashlib.sha256(json.dumps(req.inputs, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest() if req.inputs is not None else None,
    }
    safe_nodes = [{
        'id': node['id'], 'name': node['name'], 'version': node.get('version'),
        'status': node['status'], 'risk': node_health[node['id']]['risk'],
        'input_summary': node['input_summary'], 'output_summary': node['output_summary'],
        'elapsed_ms': node.get('elapsed_ms'),
        'error': 'runner_failed' if node['status'] == 'UNHEALTHY' else None,
        'checks': node['checks'],
        'source': node.get('source'),
    } for node in new_nodes]
    payload = {
        'run_id': run_id,
        'started_at': started_at,
        'finished_at': run_finished.strftime('%Y-%m-%d %H:%M:%S'),
        'request_summary': safe_request,
        'response_summary': {'passed': passed, 'nodes': safe_nodes, 'edges': edge_dicts, 'logs': logs},
    }
    if actor_id is not None:
        payload['owner_id'] = actor_id
    record_sha256 = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    pipeline_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    _append_audit({
        'time': run_finished.strftime('%Y-%m-%d %H:%M:%S'),
        'action': 'run',
        'target': 'pipeline',
        'result': '通过' if passed else '阻断',
        'detail': json.dumps({'run_id': run_id, 'nodes': len(new_nodes), 'record_sha256': record_sha256}, ensure_ascii=False),
        'run_id': run_id,
    })
    import sys
    return LegoRunResponse(
        run_id=run_id,
        started_at=started_at,
        finished_at=run_finished.strftime('%Y-%m-%d %H:%M:%S'),
        passed=passed,
        topology={'nodes': len(new_nodes), 'edges': len(req.edges)},
        selected_node=selected_node,
        nodes=[{k: v for k, v in n.items() if k != 'result'} for n in new_nodes],
        edges=edge_dicts,
        logs=logs,
        artifacts=artifacts,
        exports=exports,
    )

def search_local_catalog(query: str, source: str = 'auto', limit: int = 8) -> List[dict]:
    """可复现的本地词项匹配；只返回当前磁盘上真实存在的 Skill。"""
    items = [item for item in CATALOG if isinstance(item, dict) and
             (SKILLS_DIR / str(item.get('name', ''))).is_dir() and
             (source in {'auto', 'local'} or item.get('source') == source)]
    if source not in {'auto', 'local', 'local_project', 'local_sample'}:
        return []
    q = (query or '').strip().lower()[:200]
    aliases = {'知识库': 'rag', '问答': 'qa', '健康': 'health', '诊断': 'doctor',
               '安检': 'skill', '安全': 'trust', '合规': 'report', '报告': 'report',
               '图片': 'image', '图像': 'image', '视觉': 'vision', '检测': 'detection',
               '视频': 'video', '优化': 'optimization'}
    terms = list(dict.fromkeys(re.findall(r'[a-z][a-z0-9_.-]{1,31}', q) +
                               [term for phrase, term in aliases.items() if phrase in q]))
    # “Skill / Agent / 场景”属于宽泛词；有明确能力词时不让它们单独召回无关资产。
    broad_terms = {'skill', 'agent', 'local', 'general', 'diagnostics'}
    specific_terms = set(terms) - broad_terms
    if q and not terms:
        return []
    scored = []
    for item in items:
        name_tags = ' '.join([str(item.get('name', '')), ' '.join(item.get('tags', [])),
                              str(item.get('scene', ''))]).lower()
        description = ' '.join([str(item.get('use', '')), str(item.get('summary', ''))]).lower()
        matched = [term for term in terms if term in name_tags or term in description]
        if q and not matched:
            continue
        if specific_terms and not (set(matched) & specific_terms):
            continue
        score = sum(3 if term in name_tags else 1 for term in matched) if q else 1
        scored.append((score, {**item, 'score': score, 'matched_terms': matched}))
    scored.sort(key=lambda x: (-x[0], x[1].get('name','')))
    return [entry for _, entry in scored[:max(1, limit)]]

def fetch_github_nvidia_skills(query: str, limit: int = 6) -> List[dict]:
    q = urllib.parse.quote(query or 'nvidia/skills')
    url = f'https://api.github.com/search/repositories?q={q}&sort=updated&per_page={limit}'
    req = urllib.request.Request(url, headers={'Accept':'application/vnd.github+json','User-Agent':'SkillPulse-Demo'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return []
    items = []
    for repo in body.get('items', [])[:limit]:
        items.append({
            'name': repo.get('full_name') or repo.get('name') or 'github-repo',
            'scene': 'general',
            'source': 'github',
            'trust': 'MEDIUM',
            'status': 'UNHEALTHY',
            'use': repo.get('description') or 'GitHub repository result',
            'summary': repo.get('description') or '',
            'tags': ['github','repo'],
            'color': '#fee2e2',
        })
    return items

@app.post('/api/search/skills')
def search_skills(req: ChatRequest):
    query = (req.messages[-1].content if req.messages else '') if req.messages else ''
    source = getattr(req, 'source', 'auto') if hasattr(req, 'source') else 'auto'
    results = search_local_catalog(query, source=source, limit=8)
    normalized = []
    for item in results:
        skill_dir = _skill_dir(item['name'])
        report = _health_check(skill_dir)
        tokens = [token for token in query.lower().replace('/', ' ').replace('-', ' ').split() if token]
        haystack = ' '.join([item.get('name', ''), item.get('use', ''), item.get('summary', ''), ' '.join(item.get('tags', []))]).lower()
        matched = [token for token in tokens if token in haystack]
        normalized.append({
            **item,
            'status': report['status'],
            'trust': report['trust'],
            'risk': report['risk'],
            'checks': report['checks'],
            'schema': report.get('schema'),
            'version': report.get('version'),
            'match_hint': '本地词项匹配：' + (' / '.join(item.get('matched_terms', [])) if item.get('matched_terms') else '无查询词'),
            'origin_verified': False,
            'origin_note': '本地演示实现；不因名称或目录标签而获得上游发布者身份',
        })
    return {'query': query, 'results': normalized, 'mode': 'local-prototype-match'}


@app.get('/api/ai/status')
def ai_status():
    """只返回可用状态，不暴露密钥或账户资料。"""
    return stepfun_status()


@app.get('/api/search/semantic/status')
def semantic_status():
    return gpu_semantic_status()


@app.post('/api/search/semantic')
def semantic_search(req: IntentRequest):
    """仅在本地 CUDA 模型真实可用时检索；不把 CPU/词项回退伪装为 GPU 推理。"""
    candidates = [item for item in CATALOG if item.get('source') in {'local_project', 'local_sample'}
                  and isinstance(item.get('name'), str) and _skill_dir(item['name']).is_dir()]
    try:
        measured = rank_on_cuda(req.task, candidates)
    except SemanticUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    by_name = {item['name']: item for item in candidates}
    results = []
    for item in measured['results'][:8]:
        catalog_item = by_name[item['name']]
        report = _health_check(_skill_dir(item['name']))
        results.append({**catalog_item, 'semantic_score': item['score'],
                        'match_hint': f"CUDA 向量相似度 {item['score']:.4f}",
                        'status': report['status'], 'risk': report['risk'],
                        'trust': report['trust'], 'schema': report.get('schema')})
    return {**measured, 'results': results, 'trust_note': '排序不等于安检；下载和运行仍按本地健康状态判断'}


@app.post('/api/ai/interpret')
def ai_interpret(req: IntentRequest, request: Request):
    """用户主动触发云端解析；模型推荐仍须重新经过本地安检。"""
    if not stepfun_status()['available']:
        raise HTTPException(status_code=503, detail='未配置 StepFun API Key；本地检索仍可使用')
    actor_id = request.state.user['id']
    with AI_CALLS_LOCK:
        now = time.monotonic()
        recent = [instant for instant in AI_CALLS.get(actor_id, []) if now - instant < 3600]
        if len(recent) >= 8:
            raise HTTPException(status_code=429, detail='本账号一小时内的模型调用已达演示上限')
        recent.append(now)
        AI_CALLS[actor_id] = recent
    candidates = [item for item in CATALOG if item.get('source') in {'local_project', 'local_sample'}
                  and isinstance(item.get('name'), str) and _skill_dir(item['name']).is_dir()]
    try:
        parsed = interpret_with_stepfun(req.task, req.scene, candidates, req.environment)
    except StepFunError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    recommendations = []
    for name in parsed.get('recommended_skill_names', []):
        item = next((entry for entry in candidates if entry['name'] == name), None)
        if item is None:
            continue
        report = _health_check(_skill_dir(name))
        recommendations.append({'name': name, 'use': item.get('use', ''),
                                'catalog_summary': item.get('summary', ''),
                                'catalog_scene': item.get('scene', ''),
                                'model_match_reason': parsed.get('recommendation_reasons', {}).get(name, ''),
                                'status': report['status'], 'risk': report['risk'],
                                'eligible': report['status'] == 'HEALTHY',
                                'source': item['source']})
    return {**{key: value for key, value in parsed.items()
               if key not in {'recommended_skill_names', 'recommendation_reasons'}},
            'recommendations': recommendations,
            'trust_note': '模型只解释任务；下载与运行权限以实时本地安检为准'}


@app.post('/api/search/online')
def search_online_skills(req: ChatRequest):
    query = req.messages[-1].content if req.messages else ''
    source = req.source if req.source in {'online', 'nvidia_online'} else 'online'
    return discover_github_skills(query, source=source, limit=8)
def llm_search_skills(req: ChatRequest):
    scene = (req.messages[-1].content if req.messages else '') if False else 'general'
    body = {
        'model': req.model or 'step-3.7-flash',
        'messages': [
            {'role': 'system', 'content': '你是 SkillPulse 的 Skill 搜索助手。请根据用户需求，从 NVIDIA/skills 风格目录中推荐最匹配的 3-5 个 skill。只返回 JSON 数组，每个元素包含 name/scene/source/trust/status/use/summary。'},
            {'role': 'user', 'content': (req.messages[-1].content if req.messages else '') if req.messages else ''}
        ],
        'stream': False,
        'temperature': 0.4,
        'max_tokens': 800,
    }
    api_key = os.environ.get('STEPFUN_API_KEY', '')
    if not api_key:
        raise HTTPException(status_code=500, detail='STEPFUN_API_KEY is not set')
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req_obj = urllib.request.Request(
        'https://api.stepfun.com/v1/chat/completions',
        data=data,
        headers={'Content-Type':'application/json','Authorization':f'Bearer {api_key}'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req_obj, timeout=60) as resp:
            outer = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    content = ((outer.get('choices') or [{}])[0].get('message') or {}).get('content', '') or ''
    try:
        items = json.loads(content)
    except Exception:
        start = content.find('[')
        end = content.rfind(']')
        items = json.loads(content[start:end+1]) if start != -1 and end != -1 else []
    if not isinstance(items, list):
        items = []
    norm = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        norm.append({
            'name': item.get('name') or item.get('title') or 'unknown-skill',
            'scene': item.get('scene', 'general'),
            'source': item.get('source', 'community'),
            'trust': item.get('trust', 'MEDIUM'),
            'status': item.get('status', 'UNHEALTHY'),
            'use': item.get('use') or item.get('description') or item.get('name'),
            'summary': item.get('summary') or item.get('description') or '',
            'color': '#fee2e2',
        })
    if not norm:
        norm = [{'name':'stepfun-llm-search','scene':'general','source':'community','trust':'MEDIUM','status':'UNHEALTHY','use':'LLM search wrapper','summary':'No structured skill candidates returned from model.','color':'#fee2e2'}]
    return {'items': norm, 'raw': content}
def llm_chat(req: ChatRequest):
    api_key = os.environ.get('STEPFUN_API_KEY', '')
    if not api_key:
        raise HTTPException(status_code=500, detail='STEPFUN_API_KEY is not set')
    payload = {
        'model': req.model or 'step-3.7-flash',
        'messages': [{'role': m.role, 'content': m.content} for m in req.messages],
        'stream': bool(req.stream),
        'temperature': float(req.temperature),
        'max_tokens': int(req.max_tokens),
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req_obj = urllib.request.Request(
        'https://api.stepfun.com/v1/chat/completions',
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req_obj, timeout=60) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='ignore')
        raise HTTPException(status_code=e.code, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return ChatResponse(
        id=body.get('id', uuid.uuid4().hex),
        created=body.get('created', int(datetime.now().timestamp())),
        model=body.get('model', payload['model']),
        choices=body.get('choices', []),
        usage=ChatUsage(**body.get('usage', {})),
    )

@app.get('/api/environment/status')
def environment_status():
    ports = [3030, 8200, 11435, 8888, 13200, 8000]
    checks = []
    for port in ports:
        listening = _is_port_listening(port)
        checks.append({'port': port, 'listening': listening, 'service': _service_name(port)})
    return {'time': _now(), 'checks': checks}

def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def _save_report(skill_name: str, report: dict, repaired: bool = False):
    report['time'] = _now()
    report['repaired'] = repaired
    (REPORTS_DIR / f'{skill_name}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

def _health_check(skill_dir: Path) -> dict:
    return evaluate_skill_health(skill_dir)


def _mock_scan(skill_dir: Path) -> bool:
    """兼容旧调用名；返回基础静态扫描结果，不代表完整安全检测。"""
    from governance.checks import check_static_scan
    return check_static_scan(skill_dir)[0]

def _infer_use(name: str) -> str:
    return {
        'tao-generate-image-grounding': '开放词汇目标定位',
        'tao-generate-referring-expressions': '区域描述与框核验',
        'rag-blueprint': '企业内部知识库',
        'skill-doctor': 'Skill 健康诊断',
        'local-pedestrian-detector': '行人检测组合 Skill',
        'sample-skill-broken': '损坏 Skill 样本',
    }.get(name, '通用 Skill')

def _is_port_listening(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False

def _service_name(port: int) -> str:
    return {3030: 'OpenClaw', 8200: 'ComfyUI', 11435: 'Ollama', 8888: 'Jupyter', 13200: 'OpenClaw Alt', 8000: 'SkillPulse API'}.get(port, 'Unknown')
AUDIT_PATH = REPORTS_DIR / "audit.jsonl"
AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
VERSIONS_DIR = REPORTS_DIR.parent / "versions"
VERSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _append_audit(entry: dict) -> None:
    entry.setdefault('run_id', entry.get('run_id'))
    if CURRENT_ACTOR.get() is not None:
        entry.setdefault('actor_id', CURRENT_ACTOR.get())
    try:
        append_chained(AUDIT_PATH.with_name('audit-chain.jsonl'), entry)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f'审计记录无法写入: {exc}') from exc


def _require_audit_integrity() -> None:
    result = verify_chain(AUDIT_PATH.with_name('audit-chain.jsonl'))
    if not result['valid']:
        raise HTTPException(status_code=409, detail='审计链已损坏，拒绝操作: ' + result['error'])


@app.post('/api/audit/logs')
def append_audit_log(entry: OperationAuditRequest):
    if entry.action not in {'add', 'remove', 'connect'}:
        raise HTTPException(status_code=422, detail='仅允许固定的操作审计动作')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', entry.target):
        raise HTTPException(status_code=422, detail='操作目标格式无效')
    if entry.node_id is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', entry.node_id):
        raise HTTPException(status_code=422, detail='节点 ID 格式无效')
    _append_audit({'time': _now(), 'action': 'operation-' + entry.action,
                   'target': entry.target, 'result': 'recorded', 'detail': None,
                   'node_id': entry.node_id, 'run_id': None})
    return {'saved': True}


def _read_audit_records():
    logs = []
    if AUDIT_PATH.exists():
        for line in AUDIT_PATH.read_text(encoding='utf-8', errors='ignore').splitlines():
            try:
                old = json.loads(line)
                if not isinstance(old, dict):
                    continue
                target = old.get('target')
                old_time = old.get('time')
                logs.append({'time': old_time if isinstance(old_time, str) and re.fullmatch(r'[0-9T:Z+ .-]{10,35}', old_time) else None,
                             'action': old.get('action') if old.get('action') in {'run', 'run-node', 'link', 'download', 'add', 'remove'} else 'legacy',
                             'target': target if isinstance(target, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', target) else 'legacy-entry',
                             'result': '旧记录未验证', 'detail': '原始内容已隐藏，防止旧日志泄露敏感输入',
                             'run_id': old.get('run_id') if isinstance(old.get('run_id'), str) and re.fullmatch(r'[0-9a-f]{8}', old['run_id']) else None,
                             'integrity': 'legacy_unverified'})
            except Exception:
                pass
    chain = verify_chain(AUDIT_PATH.with_name('audit-chain.jsonl'))
    if not chain['valid']:
        raise HTTPException(status_code=409, detail='审计链校验失败: ' + chain['error'])
    logs.extend({**record, 'integrity': 'local_hash_chain'} for record in chain['records'])
    return logs, chain


@app.get('/api/audit/logs')
def get_audit_logs(limit: int = 200):
    limit = max(1, min(limit, 1000))
    logs, chain = _read_audit_records()
    return {'logs': logs[-limit:], 'integrity': {'valid': True, 'chained_count': chain['count'],
            'legacy_count': len(logs) - chain['count'],
            'limitation': '本地哈希链可发现内容修改/重排，不能防止能重写全部文件者删除链尾'}}


@app.get('/api/audit/export')
def export_audit():
    logs, _ = _read_audit_records()
    content = ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in logs)
    return Response(content, media_type='application/x-ndjson', headers={'Content-Disposition': 'attachment; filename="skillpulse-audit.jsonl"'})


@app.get('/api/audit/verify')
def audit_verify():
    chain = verify_chain(AUDIT_PATH.with_name('audit-chain.jsonl'))
    return {'valid': chain['valid'], 'count': chain['count'], 'last_hash': chain['last_hash'],
            'error': chain.get('error'), 'scope': chain['scope'],
            'legacy_records_verified': False,
            'limitation': '本地哈希链不能防止有文件写权限者删除尾部或重建整条链；没有外部锚定'}


@app.get('/api/audit/report/{run_id}')
def audit_report(run_id: str):
    if not re.fullmatch(r'[0-9a-f]{8}', run_id):
        raise HTTPException(status_code=400, detail='Invalid run_id')
    actor_id = CURRENT_ACTOR.get()
    path = (USER_RUNS_DIR / str(actor_id) if actor_id is not None else CATALOG_DIR) / f'pipeline-{run_id}.json'
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Run record not found')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('run_id') != run_id:
        raise HTTPException(status_code=409, detail='运行摘要中的 run_id 不匹配')
    chain = verify_chain(AUDIT_PATH.with_name('audit-chain.jsonl'))
    if not chain['valid']:
        raise HTTPException(status_code=409, detail='审计链校验失败: ' + chain['error'])
    chained_events = [record for record in chain['records'] if record.get('run_id') == run_id]
    commit = next((record for record in reversed(chained_events) if record.get('action') == 'run' and record.get('target') == 'pipeline'), None)
    expected_digest = None
    if commit:
        try:
            expected_digest = json.loads(commit.get('detail') or '{}').get('record_sha256')
        except (TypeError, ValueError):
            expected_digest = None
    actual_digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    if expected_digest and expected_digest != actual_digest:
        raise HTTPException(status_code=409, detail='运行摘要与审计链中的内容哈希不一致')
    report = {
        'kind': 'SkillPulse local audit report prototype',
        'run_id': run_id,
        'started_at': data.get('started_at'), 'finished_at': data.get('finished_at'),
        'passed': data.get('response_summary', {}).get('passed'),
        'nodes': data.get('response_summary', {}).get('nodes', []),
        'edges': data.get('response_summary', {}).get('edges', []),
        'request_summary': data.get('request_summary', {}),
        'audit_integrity': {'chained_events': len(chained_events), 'chain_valid_now': True,
                            'run_record_verified': expected_digest == actual_digest,
                            'coverage': 'current_chain' if expected_digest == actual_digest else 'legacy_unverified'},
        'limitations': ['仅含本地检查和哈希摘要，不是第三方合规认证',
                        '本地哈希清单不是公钥数字签名', '子进程执行器不是安全沙箱',
                        '本地审计哈希链没有外部锚定，不能防止整链重写或链尾删除'],
    }
    return Response(json.dumps(report, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="skillpulse-audit-{run_id}.json"'})


@app.post('/api/compatibility/{skill_name}')
def compatibility(skill_name: str, req: CompatibilityRequest):
    return _dependency_compatibility([skill_name])


class PipelineCompatibilityRequest(BaseModel):
    skill_names: List[str]


def _dependency_compatibility(names: List[str]) -> dict:
    """仅比较声明的精确依赖版本；不声称已检测系统中已安装的包。"""
    from collections import defaultdict
    declared = defaultdict(list)
    skills = []
    notes = []
    for name in dict.fromkeys(names):
        report = _health_check(_skill_dir(name))
        schema = report.get('schema')
        if not schema:
            raise HTTPException(status_code=422, detail=f'{name} 缺少可用 Schema')
        resources = schema['resources']
        skills.append({'name': name, 'version': schema['version'], 'status': report['status'], 'resources': resources})
        if report['status'] != 'HEALTHY':
            notes.append(f'{name} 未通过安检')
        for declaration in resources.get('dependencies', []):
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:==([A-Za-z0-9_.-]+))?', declaration)
            if not match:
                notes.append(f'{name} 的依赖声明暂不支持自动判断: {declaration}')
                continue
            declared[match.group(1).lower().replace('_', '-')].append((name, match.group(2)))
    for package, items in declared.items():
        pins = {pin for _, pin in items if pin}
        if len(pins) > 1:
            notes.append(f'{package} 存在冲突的精确版本要求: {", ".join(sorted(pins))}')
    return {'compatible': not notes, 'notes': notes, 'skills': skills,
            'scope': '仅比较 Skill Schema 声明的精确依赖版本与安检状态；未验证本机安装包、驱动或模型兼容性'}


@app.post('/api/pipelines/compatibility')
def pipeline_compatibility(req: PipelineCompatibilityRequest):
    if not req.skill_names:
        raise HTTPException(status_code=422, detail='至少提供一个 Skill')
    return _dependency_compatibility(req.skill_names)


class PipelineExportRequest(BaseModel):
    nodes: List[LegoNode]
    edges: List[LegoEdge]
    format: Optional[str] = 'yaml'
    generated_at: Optional[str] = None


@app.post('/api/pipelines/export')
def pipeline_export(req: PipelineExportRequest):
    if not req.nodes:
        raise HTTPException(status_code=422, detail='不能导出空流水线')
    nodes = []
    for node in req.nodes:
        if node.control_type in CONTROL_SCHEMAS:
            if node.name != node.control_type:
                raise HTTPException(status_code=422, detail='控制节点名称与类型不一致')
            version = CONTROL_SCHEMAS[node.control_type]['version']
        elif node.control_type == 'skill':
            report = _health_check(_skill_dir(node.name))
            if report['status'] != 'HEALTHY':
                raise HTTPException(status_code=403, detail=f'未通过安检的 Skill 不能导出为可运行流水线: {node.name}')
            version = report['version']
        else:
            raise HTTPException(status_code=422, detail=f'不支持导出节点类型: {node.control_type}')
        nodes.append({'id': node.id, 'name': node.name, 'version': version, 'control_type': node.control_type})
    ids = {node['id'] for node in nodes}
    if len(ids) != len(nodes):
        raise HTTPException(status_code=422, detail='重复节点 ID')
    node_types = {node['id']: node['control_type'] for node in nodes}
    edges = []
    for edge in req.edges:
        if edge.from_ not in ids or edge.to not in ids:
            raise HTTPException(status_code=422, detail='导出连线引用不存在的节点')
        if edge.from_ == edge.to:
            raise HTTPException(status_code=422, detail='不能导出自连节点')
        if node_types[edge.from_] == 'if_branch' and edge.route not in ('true', 'false'):
            raise HTTPException(status_code=422, detail='分支导出缺少 true/false 路由')
        if node_types[edge.from_] != 'if_branch' and edge.route is not None:
            raise HTTPException(status_code=422, detail='非分支节点不能指定路由')
        edges.append({'from': edge.from_, 'to': edge.to, 'route': edge.route})
    if len({(edge['from'], edge['to'], edge['route']) for edge in edges}) != len(edges):
        raise HTTPException(status_code=422, detail='不能导出重复连线')
    for node in nodes:
        if node['control_type'] == 'if_branch' and sorted(edge['route'] for edge in edges if edge['from'] == node['id']) != ['false', 'true']:
            raise HTTPException(status_code=422, detail='分支必须同时连接 true 与 false 出线')
    indegree = {node_id: 0 for node_id in ids}
    adjacency = {node_id: [] for node_id in ids}
    for edge in edges:
        adjacency[edge['from']].append(edge['to'])
        indegree[edge['to']] += 1
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node_id = ready.pop()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(ids):
        raise HTTPException(status_code=422, detail='不能导出包含循环依赖的拓扑')
    payload = {'format_version': 1, 'generated_at': req.generated_at or _now(), 'nodes': nodes, 'edges': edges,
               'note': '拓扑与版本快照；不包含运行输入、凭证或 Skill 源代码'}
    yaml_lines = ['format_version: 1', 'generated_at: ' + json.dumps(payload['generated_at']), 'nodes:']
    for node in nodes:
        yaml_lines += ['  - id: ' + json.dumps(node['id']), '    name: ' + json.dumps(node['name']),
                       '    version: ' + json.dumps(node['version']), '    control_type: ' + json.dumps(node['control_type'])]
    yaml_lines.append('edges:')
    for edge in edges:
        yaml_lines += ['  - from: ' + json.dumps(edge['from']), '    to: ' + json.dumps(edge['to']),
                       '    route: ' + (json.dumps(edge['route']) if edge['route'] else 'null')]
    yaml_lines.append('note: ' + json.dumps(payload['note'], ensure_ascii=False))
    yaml_text = '\n'.join(yaml_lines) + '\n'
    openapi = {
        'openapi': '3.0.3',
        'info': {'title': 'SkillPulse Pipeline API prototype', 'version': '0.4.0',
                 'description': '本地运行请求描述，不包含鉴权或生产环境服务定义'},
        'paths': {'/api/pipelines/run': {'post': {
            'summary': 'Run a local governed pipeline',
            'requestBody': {'required': True, 'content': {'application/json': {'schema': {
                'type': 'object', 'required': ['nodes', 'edges'],
                'properties': {'nodes': {'type': 'array', 'items': {'type': 'object'}},
                               'edges': {'type': 'array', 'items': {'type': 'object'}},
                               'inputs': {'type': 'object'}}}}}},
            'responses': {'200': {'description': 'Run result'}, '403': {'description': 'Skill failed governance gate'},
                          '422': {'description': 'Invalid topology or inputs'}}}}}
    }
    if req.format not in ('yaml', 'openapi'):
        raise HTTPException(status_code=422, detail='仅支持 yaml 或 openapi 导出')
    _append_audit({'time': _now(), 'action': 'operation-export', 'target': 'pipeline',
                   'result': 'recorded', 'detail': req.format, 'run_id': None})
    return {'format': req.format, 'yaml': yaml_text, 'openapi': openapi, 'payload': payload}


class WorkflowSaveRequest(BaseModel):
    name: str
    nodes: List[LegoNode]
    edges: List[LegoEdge]


@app.post('/api/me/workflows')
def save_my_workflow(request: Request, workflow: WorkflowSaveRequest):
    """复用完整拓扑/健康验证，仅保存无输入值的流程快照。"""
    if not 1 <= len(workflow.name.strip()) <= 80:
        raise HTTPException(status_code=422, detail='流程名称须为 1–80 字')
    skill_names = [node.name for node in workflow.nodes if node.control_type == 'skill']
    if len(skill_names) != len(set(skill_names)):
        raise HTTPException(status_code=422, detail='同一流程不能保存重复 Skill 节点')
    exported = pipeline_export(PipelineExportRequest(nodes=workflow.nodes, edges=workflow.edges, format='yaml'))
    record = persist_workflow(USER_WORKFLOWS_DIR, request.state.user['id'], workflow.name, exported['payload'])
    _append_audit({'time': _now(), 'action': 'workflow-save', 'target': record['id'],
                   'result': 'recorded', 'detail': '仅保存拓扑与版本', 'run_id': None})
    return record


@app.get('/api/me/workflows')
def my_workflows(request: Request):
    return list_workflows(USER_WORKFLOWS_DIR, request.state.user['id'])


@app.get('/api/me/workflows/{workflow_id}')
def my_workflow(request: Request, workflow_id: str):
    record = read_workflow(USER_WORKFLOWS_DIR, request.state.user['id'], workflow_id)
    if record is None:
        raise HTTPException(status_code=404, detail='当前账号没有此工作流')
    return record


@app.get('/api/me/runs')
def my_runs(request: Request):
    return list_runs(USER_RUNS_DIR, request.state.user['id'])


@app.get('/api/me/runs/{run_id}')
def my_run(request: Request, run_id: str):
    record = read_run(USER_RUNS_DIR, request.state.user['id'], run_id)
    if record is None:
        raise HTTPException(status_code=404, detail='当前账号没有此运行摘要')
    return record

@app.get('/api/compatibility/summary')
def compatibility_summary():
    summaries = []
    for path in SKILLS_DIR.iterdir():
        if not path.is_dir():
            continue
        try:
            summaries.append({'name': path.name, **_dependency_compatibility([path.name])})
        except HTTPException as exc:
            summaries.append({'name': path.name, 'compatible': False, 'notes': [str(exc.detail)]})
    return {'summary': summaries}


@app.get('/api/skills/{skill_name}/versions')
def skill_versions(skill_name: str):
    skill_dir = _skill_dir(skill_name)
    report = _health_check(skill_dir)
    schema = report.get('schema')
    current = {'version': schema['version'], 'status': 'current', 'source': 'disk',
               'schema_sha256': hashlib.sha256((skill_dir / 'skill.schema.json').read_bytes()).hexdigest()} if schema else None
    path = VERSIONS_DIR / (skill_name + '.json')
    history = []
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            for record in data.get('versions', []):
                if not isinstance(record, dict):
                    continue
                if record.get('snapshot_sha256'):
                    verified = verify_snapshot(VERSIONS_DIR, skill_name, record)
                    history.append({**record, 'status': 'snapshot_verified' if verified else 'snapshot_missing_or_tampered',
                                    'source': 'local_archive'})
                else:
                    history.append({**record, 'status': 'unverified_record', 'source': 'historical_record'})
    except (OSError, ValueError, TypeError):
        history = []
    return {'skill': skill_name, 'current': current, 'history': history,
            'note': '只有 snapshot_verified 有本地源码 ZIP 及哈希证据；旧记录仍不证明对应源码存在，快照哈希不是发布者签名'}


@app.post('/api/skills/{skill_name}/versions')
def save_skill_version(skill_name: str, payload: Dict[str, Any]):
    _require_audit_integrity()
    skill_dir = _skill_dir(skill_name)
    report = _health_check(skill_dir)
    if report['status'] != 'HEALTHY':
        raise HTTPException(status_code=403, detail='当前 Skill 未通过安检，不允许生成可信快照')
    current_info = skill_versions(skill_name)['current']
    if not current_info:
        raise HTTPException(status_code=422, detail='缺少可验证的当前版本')
    if payload.get('version') != current_info['version']:
        raise HTTPException(status_code=409, detail='不能凭文本创建版本：请求版本必须与磁盘 Skill Schema 一致')
    path = VERSIONS_DIR / (skill_name + '.json')
    current = {'versions': []}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f'现有版本记录不可解析，未覆盖: {exc}') from exc
    versions = current.get('versions', [])
    if not isinstance(versions, list):
        raise HTTPException(status_code=409, detail='现有版本记录格式错误，未覆盖')
    try:
        archive, digest, manifest = build_snapshot(skill_dir)
        store_snapshot(VERSIONS_DIR, skill_name, current_info['version'], archive, digest)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f'快照未创建: {exc}') from exc
    if not any(v.get('snapshot_sha256') == digest for v in versions if isinstance(v, dict)):
        versions.append({'version': current_info['version'], 'schema_sha256': current_info['schema_sha256'],
                         'snapshot_sha256': digest, 'files': manifest, 'observed_at': _now(), 'status': 'snapshot'})
    current['versions'] = versions
    temp_path = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()  # 仅清理本次创建的临时元数据文件。
    _append_audit({'time': _now(), 'action': 'operation-snapshot', 'target': skill_name,
                   'result': 'recorded', 'detail': digest, 'run_id': None})
    return {'saved': True, 'skill': skill_name, 'version': current_info['version'],
            'schema_sha256': current_info['schema_sha256'], 'snapshot_sha256': digest,
            'files': len(manifest)}


@app.get('/api/skills/{skill_name}/snapshots/{digest}')
def download_skill_snapshot(skill_name: str, digest: str):
    skill_dir = _skill_dir(skill_name)
    if _health_check(skill_dir)['status'] != 'HEALTHY':
        raise HTTPException(status_code=403, detail='当前 Skill 未通过安检，禁止下载历史快照')
    record = next((item for item in skill_versions(skill_name)['history']
                   if item.get('snapshot_sha256') == digest and item.get('status') == 'snapshot_verified'), None)
    if record is None:
        raise HTTPException(status_code=404, detail='已验证快照不存在')
    path = snapshot_path(VERSIONS_DIR, skill_name, record['version'], digest)
    _append_audit({'time': _now(), 'action': 'operation-snapshot-download', 'target': skill_name,
                   'result': 'recorded', 'detail': digest, 'run_id': None})
    return Response(path.read_bytes(), media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="{path.name}"'})
