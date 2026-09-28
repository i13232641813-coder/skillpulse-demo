"""Static smoke checks for the active SkillPulse Lego page."""
import pathlib
import re
import sys


PAGE = pathlib.Path(__file__).resolve().parent / 'frontend' / 'lego' / 'index.html'
text = PAGE.read_text(encoding='utf-8')
style = (PAGE.parent / 'ui.css').read_text(encoding='utf-8')
script_match = re.search(r'<script>([\s\S]*?)</script>', text)
script = script_match.group(1) if script_match else ''

checks = {
    'active_page_is_skill_lego': 'Skill Lego' in text,
    'search_scope_is_honest': '本地匹配 · GitHub 候选需隔离验证' in text,
    'online_discovery_and_source_visible': 'id="online-results"' in text and 'function renderOnlineResults(data)' in script and 'item.source_url' in script and "'/search/online'" in script,
    'stepfun_explicit_opt_in': 'onclick="interpretTask()"' in text and 'async function interpretTask()' in script and "'/ai/interpret'" in script and '点击后向 StepFun 发送' in text,
    'stepfun_auto_local_search_keeps_online_explicit': 'await searchSkills({localOnly:true})' in script and "const source = localOnly?'local':" in script and 'GitHub 尚未查询' in script,
    'dgx_semantic_requires_real_endpoint': 'value="dgx_semantic"' in text and "'/search/semantic'" in script and 'CUDA 检索未运行' in script,
    'dgx_success_not_styled_as_error': '#search-status[data-state="success"]' in text and "statusEl.dataset.state='success'" in script and 'style="margin-bottom:8px;color:#b91c1c"' not in text,
    'remote_import_is_quarantined': 'async function importOnlineSkill(index)' in script and "'/repository/import-github'" in script and '隔离区初审' in text,
    'apple_like_font_and_layout_loaded': 'ui.css?v=20260927-report-brick' in text and '"Noto Sans SC"' in style and '.health-dialog' in style,
    'single_edges_container': len(re.findall(r'<svg\s+id="edges"', text)) == 1,
    'single_nodes_container': len(re.findall(r'<div\s+id="nodes"', text)) == 1,
    'warnings_container': 'id="warnings"' in text,
    'backend_catalog_and_canonical_schema': "'/lego/skills'" in script and 'node_schema: schema' in script,
    'health_and_download_api_calls': "'/health'" in script and "'/download'" in script,
    'pipeline_run_and_export_handlers': 'async function runPipeline' in script and 'async function exportPipeline' in script,
    'port_connection_initialized': 'function initPortDrag()' in script and 'initPortDrag();' in script and 'markEdgeWarnings' not in script,
    'final_output_rendered': '查看完整运行结果' in script and 'finalOutput' in script and 'outputText' in script,
    'phase2_controls_wired': "onclick=\"addNode('if_branch')\"" in text and "onclick=\"addNode('loop')\"" in text and "fetch(API_BASE + '/controls')" in script,
    'phase2_export_and_report_wired': "exportPipeline('openapi')" in text and 'function exportAuditReport()' in script,
    'snapshot_and_audit_chain_wired': 'function createVersionSnapshot(name)' in script and 'async function verifyAudit()' in script and 'function recordOperation' in script,
    'phase3_login_before_workspace': 'await authStatus();' in script and 'async function submitAuth()' in script and 'id="auth-gate"' in text,
    'phase3_role_ui_and_upload': 'function toggleViewMode()' in script and 'async function quickStartTemplate(' in script and 'async function uploadPrivateSkill()' in script and 'id="repo-list"' in text,
    'curated_action_button_gated_by_health': 'id="curated-demo-button"' in text and "item.name==='nvidia-skill-card-validator'&&item.status==='HEALTHY'" in script and 'async function quickStartCuratedTemplate()' in script,
    'phase3_user_management': 'async function createLocalUser()' in script and "'/auth/users'" in script,
    'phase4_review_queue': 'async function loadReviewQueue()' in script and 'async function submitSelectedReview()' in script and 'id="review-detail"' in text,
    'phase4_revocation_is_explicit': 'onclick="revokeSelectedUpload()"' in text and 'async function revokeSelectedUpload()' in script and "'/revoke'" in script,
    'phase4_owned_history': 'async function saveCurrentWorkflow()' in script and 'async function loadMyWorkflows()' in script and 'async function loadMyRuns()' in script,
    'inspector_sections_do_not_shrink': 'class="panel inspector"' in text and '.inspector>div{flex:0 0 auto!important' in text,
    'advanced_controls_and_schema_accessible': 'class="advanced-actions expert-only"' in text and 'class="schema-disclosure expert-only"' in text and 'id="node-preview"' in text,
    'health_report_details_only_on_request': 'function renderHealthSummary(name, report)' in script and 'function showHealthDialog(name, report, reportNodeId=' in script and 'renderHealthSummary(name,report)' in script and 'showHealthDialog(report.skill||name,report)' in script and 'id="health-dialog"' in text,
    'actual_run_report_on_brick': 'function reportForBrick(node)' in script and 'doctorRun.outputs.result' in script and 'showBrickReport(' in script and 'brick-report-result' in style,
    'secondary_inspector_sections_collapsed': 'class="disclosure"><summary>我的流程与运行摘要</summary>' in text and 'class="disclosure expert-only"><summary>本地操作/运行审计</summary>' in text,
}

ids = re.findall(r'\bid="([^"]+)"', text)
literal_ids = [value for value in ids if "' +" not in value]
checks['static_dom_ids_unique'] = len(literal_ids) == len(set(literal_ids))

handlers = re.findall(r'onclick="([^"]+)"', text)
defined = set(re.findall(r'(?:async\s+)?function\s+([\w$]+)\s*\(', script))
called = {match.group(1) for handler in handlers if (match := re.match(r'\s*([\w$]+)\s*\(', handler))}
missing = sorted(called - defined)
checks['inline_handlers_resolve'] = not missing

for name, ok in checks.items():
    print(f'{name}: {"PASS" if ok else "FAIL"}')
if missing:
    print('unresolved inline handlers:', ', '.join(missing))
if not all(checks.values()):
    sys.exit(1)
