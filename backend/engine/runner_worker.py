"""子进程入口：只通过 stdin/stdout 接收/返回 JSON。"""
import importlib.util
import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    skill_dir = request['skill_dir']
    runner_path = skill_dir + '/run_local.py'
    spec = importlib.util.spec_from_file_location('skillpulse_isolated_runner', runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('无法加载本地 Skill runner')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'run', None)):
        raise RuntimeError('runner 缺少 run(inputs) 函数')
    value = module.run(request.get('inputs') or {})
    if not isinstance(value, dict):
        raise RuntimeError('runner 必须返回对象')
    response = {
        'ok': bool(value.get('ok')),
        'status': 'passed' if bool(value.get('ok')) else 'failed',
        'outputs': value.get('outputs') or {},
        'artifacts': value.get('artifacts') or [],
        'error': value.get('error'),
    }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(f'{type(exc).__name__}: {exc}')
        raise SystemExit(1)
