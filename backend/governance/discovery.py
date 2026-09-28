"""公开 GitHub Skill 发现与隔离导入；网络内容永不直接进入可执行目录。"""
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import PurePosixPath


class DiscoveryError(ValueError):
    pass


_SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
_COMMIT = re.compile(r'^[0-9a-f]{40}$')
_CACHE = {}
_CACHE_SECONDS = 300
_ALIASES = {
    '视觉': 'vision', '图片': 'image', '图像': 'image', '视频': 'video',
    '检索': 'rag', '知识库': 'rag', '安全': 'security', '机器人': 'robotics',
    '优化': 'optimization', '诊断': 'diagnostics', '报告': 'report',
}


def _request(url: str, limit: int = 8 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'SkillPulse-Demo/0.4',
    })
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise DiscoveryError('GitHub API 限流或拒绝访问；请稍后重试') from exc
        raise DiscoveryError(f'GitHub 请求失败 HTTP {exc.code}') from exc
    except (OSError, TimeoutError) as exc:
        raise DiscoveryError('GitHub 暂时不可连接；本地 Skill 仍可使用') from exc
    if len(data) > limit:
        raise DiscoveryError('线上响应过大，已拒绝处理')
    return data


def _api(path: str) -> dict:
    try:
        data = json.loads(_request('https://api.github.com/' + path))
    except (ValueError, UnicodeError) as exc:
        raise DiscoveryError('GitHub 返回了无效数据') from exc
    if not isinstance(data, dict):
        raise DiscoveryError('GitHub 返回格式不正确')
    return data


def _terms(query: str) -> list[str]:
    text = (query or '').lower()[:200]
    words = re.findall(r'[a-z0-9][a-z0-9_-]{1,31}', text)
    words += [value for key, value in _ALIASES.items() if key in text]
    return list(dict.fromkeys(words))[:8]


def _repo_tree(owner: str, repo: str, branch: str) -> tuple[str, list[dict]]:
    if not all(_SLUG.fullmatch(part) for part in (owner, repo, branch)):
        raise DiscoveryError('仓库标识无效')
    key = (owner.lower(), repo.lower(), branch)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    commit = _api(f'repos/{owner}/{repo}/commits/{branch}').get('sha', '')
    if not _COMMIT.fullmatch(commit):
        raise DiscoveryError('仓库提交版本无效')
    tree = _api(f'repos/{owner}/{repo}/git/trees/{commit}?recursive=1')
    if tree.get('truncated') or not isinstance(tree.get('tree'), list):
        raise DiscoveryError('仓库文件清单不完整，未展示候选 Skill')
    result = (commit, tree['tree'])
    _CACHE[key] = (time.monotonic(), result)
    return result


def _skill_entries(owner: str, repo: str, commit: str, tree: list[dict], terms: list[str], limit: int) -> list[dict]:
    results = []
    # 只发现仓库根目录的许可证文件，不推断许可证类型或授权范围。
    license_path = next((entry['path'] for entry in tree
                         if entry.get('type') == 'blob'
                         and entry.get('path') in {'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'COPYING', 'COPYING.md'}
                         and _COMMIT.fullmatch(entry.get('sha', ''))), None)
    license_url = (f'https://github.com/{owner}/{repo}/blob/{commit}/{license_path}'
                   if license_path else None)
    for entry in tree:
        path = entry.get('path', '')
        if entry.get('type') != 'blob' or not isinstance(path, str) or not path.endswith('/SKILL.md'):
            continue
        parts = PurePosixPath(path).parts
        if len(parts) < 3 or parts[0] not in {'skills', '.agents'}:
            continue
        if parts[0] == '.agents' and (len(parts) < 4 or parts[1] != 'skills'):
            continue
        skill_name = parts[-2]
        if not _SLUG.fullmatch(skill_name) or not _COMMIT.fullmatch(entry.get('sha', '')):
            continue
        haystack = (skill_name + ' ' + path + ' ' + repo).lower().replace('-', ' ')
        matches = [term for term in terms if re.search(r'(?<![a-z0-9])' + re.escape(term.replace('-', ' ')) + r'(?![a-z0-9])', haystack)]
        if terms and not matches:
            continue
        results.append({
            'name': skill_name, 'source_platform': 'GitHub', 'publisher': owner,
            'repository': f'{owner}/{repo}', 'repository_url': f'https://github.com/{owner}/{repo}',
            'source_url': f'https://github.com/{owner}/{repo}/blob/{commit}/{path}',
            'skill_path': str(PurePosixPath(path).parent), 'commit_sha': commit,
            'skill_file_sha': entry['sha'], 'status': 'DISCOVERED', 'risk': 'UNKNOWN',
            'license': 'UNKNOWN',
            'license_status': 'FILE_PRESENT_UNVERIFIED' if license_path else 'UNKNOWN',
            'license_file_url': license_url,
            'origin_verified': False, 'signature_verified': False,
            'can_download': False, 'can_run': False,
            'match_hint': 'GitHub 路径匹配：' + (' / '.join(matches) if matches else '目录浏览'),
        })
    return results[:limit]


def discover_github_skills(query: str, source: str = 'online', limit: int = 8) -> dict:
    """搜索明确的 Skill 文件，不把普通仓库搜索结果冒充为 Skill。"""
    terms = _terms(query)
    repositories = [('NVIDIA', 'skills', 'main')]
    warnings = []
    if source != 'nvidia_online':
        repositories += [('anthropics', 'skills', 'main'), ('openai', 'skills', 'main'),
                         ('vercel-labs', 'agent-skills', 'main')]
        search = urllib.parse.urlencode({'q': 'agent skills ' + ' '.join(terms[:3]) + ' in:name,description', 'per_page': 4})
        try:
            repos = _api('search/repositories?' + search).get('items', [])
            for item in repos[:4]:
                full = item.get('full_name', '')
                branch = item.get('default_branch', '')
                if isinstance(full, str) and '/' in full and isinstance(branch, str):
                    owner, repo = full.split('/', 1)
                    if all(_SLUG.fullmatch(part) for part in (owner, repo, branch)):
                        repositories.append((owner, repo, branch))
        except DiscoveryError as exc:
            warnings.append(str(exc))
    results = []
    seen = set()
    for owner, repo, branch in repositories[:7]:
        key = (owner.lower(), repo.lower())
        if key in seen:
            continue
        seen.add(key)
        try:
            commit, tree = _repo_tree(owner, repo, branch)
            results.extend(_skill_entries(owner, repo, commit, tree, terms, limit))
        except DiscoveryError as exc:
            warnings.append(f'{owner}/{repo}：{exc}')
        if len(results) >= limit:
            break
    return {'results': results[:limit], 'warnings': warnings, 'mode': 'github-skill-file-discovery',
            'scope': '公开 GitHub 仓库中可识别的 SKILL.md；不是全互联网索引，也不是安全认证'}


def build_quarantine_zip(owner: str, repo: str, commit: str, skill_path: str) -> bytes:
    """按固定提交下载单个 Skill，逐文件核对 Git blob 哈希后打包。"""
    if not all(_SLUG.fullmatch(part) for part in (owner, repo)) or not _COMMIT.fullmatch(commit):
        raise DiscoveryError('仓库或提交版本无效')
    parts = PurePosixPath(skill_path).parts
    if (not parts or '..' in parts or '\\' in skill_path or ':' in skill_path
            or parts[0] not in {'skills', '.agents'} or not _SLUG.fullmatch(parts[-1])):
        raise DiscoveryError('Skill 路径无效')
    if parts[0] == '.agents' and (len(parts) < 3 or parts[1] != 'skills'):
        raise DiscoveryError('Skill 路径无效')
    tree = _api(f'repos/{owner}/{repo}/git/trees/{commit}?recursive=1')
    if tree.get('truncated') or not isinstance(tree.get('tree'), list):
        raise DiscoveryError('仓库清单不完整，禁止导入')
    prefix = skill_path.rstrip('/') + '/'
    entries = [entry for entry in tree['tree'] if isinstance(entry.get('path'), str)
               and entry['path'].startswith(prefix) and entry.get('type') == 'blob']
    if not any(entry['path'] == prefix + 'SKILL.md' for entry in entries):
        raise DiscoveryError('所选目录不是 Skill')
    if not entries or len(entries) > 60 or any(entry.get('mode') == '120000' for entry in entries):
        raise DiscoveryError('Skill 文件数过多或包含符号链接')
    if sum(max(0, entry.get('size', 0)) for entry in entries) > 4 * 1024 * 1024:
        raise DiscoveryError('Skill 超过 4 MiB，禁止自动导入')
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            path = entry['path']
            relative = path[len(prefix):]
            path_parts = PurePosixPath(relative).parts
            if not path_parts or any(part in {'.', '..'} for part in path_parts) or '\\' in path or ':' in path:
                raise DiscoveryError('Skill 包含不安全路径')
            raw_path = '/'.join(urllib.parse.quote(part, safe='') for part in PurePosixPath(path).parts)
            data = _request(f'https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{raw_path}', limit=1024 * 1024)
            digest = hashlib.sha1(f'blob {len(data)}\0'.encode('ascii') + data).hexdigest()
            if digest != entry.get('sha'):
                raise DiscoveryError('下载内容与固定提交哈希不一致')
            archive.writestr(str(PurePosixPath(parts[-1]) / relative), data)
    payload = archive_bytes.getvalue()
    if len(payload) > 5 * 1024 * 1024:
        raise DiscoveryError('压缩包超过隔离区大小限制')
    return payload
