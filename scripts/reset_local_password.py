"""在项目所在电脑的终端交互式重置演示账号；不经网络传输密码。"""
import getpass
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from security.local_auth import AuthError, list_local_users, reset_local_password  # noqa: E402


def main() -> int:
    if not sys.stdin.isatty():
        print('请在本机终端中交互运行；不要把新密码写在命令行参数里。')
        return 1
    db_path = ROOT / 'workspace' / 'outbox' / 'accounts.sqlite3'
    try:
        users = list_local_users(db_path)
        if not users:
            raise AuthError('账号库中没有可重置的账号')
        print('本地 SkillPulse 账号：' + '、'.join(user['username'] for user in users))
        default = users[0]['username'] if len(users) == 1 else ''
        prompt = f'要重置的用户名 [{default}]：' if default else '要重置的用户名：'
        username = input(prompt).strip() or default
        if username not in {user['username'] for user in users}:
            raise AuthError('账号不存在')
        new_password = getpass.getpass('输入新密码（12–256 位，不会显示）：')
        confirmation = getpass.getpass('再次输入新密码：')
        if new_password != confirmation:
            raise AuthError('两次输入不一致；账号未修改')
        reset_local_password(db_path, username, new_password)
    except (AuthError, EOFError, KeyboardInterrupt) as exc:
        print(f'未重置：{exc}')
        return 1
    print(f'账号 {username} 的密码已重置；旧登录会话已失效。请重新登录。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
