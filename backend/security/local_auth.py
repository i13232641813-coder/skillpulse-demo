"""单机演示账号。无 SSO、MFA 或公网防护，不可作为生产身份系统。"""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

USERNAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{2,31}$')
SESSION_SECONDS = 8 * 60 * 60
CURRENT_ACTOR: ContextVar[int | None] = ContextVar('skillpulse_actor_id', default=None)


class AuthError(ValueError):
    pass


@contextmanager
def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    connection.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, role TEXT NOT NULL CHECK(role IN (\'engineer\', \'business\')), salt BLOB NOT NULL, password_hash BLOB NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL)')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)


def _validate(username: str, password: str):
    if not USERNAME.fullmatch(username):
        raise AuthError('用户名须为 3–32 位，以字母开头，仅含字母、数字、_、-')
    if len(password) < 12 or len(password) > 256:
        raise AuthError('密码长度须为 12–256 位')


def has_users(path: Path) -> bool:
    with _connect(path) as db:
        return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None


def create_user(path: Path, username: str, password: str, role: str, *, bootstrap=False) -> dict:
    _validate(username, password)
    if role not in {'engineer', 'business'}:
        raise AuthError('不支持的角色')
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if bootstrap and db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise AuthError('管理员已创建，不能再次初始化')
        salt = secrets.token_bytes(16)
        try:
            cursor = db.execute('INSERT INTO users (username, role, salt, password_hash) VALUES (?, ?, ?, ?)', (username, role, salt, _password_hash(password, salt)))
        except sqlite3.IntegrityError as exc:
            raise AuthError('用户名已存在') from exc
        return {'id': cursor.lastrowid, 'username': username, 'role': role}


def login(path: Path, username: str, password: str) -> tuple[str, dict]:
    with _connect(path) as db:
        row = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if row is None or not hmac.compare_digest(_password_hash(password, row['salt']), row['password_hash']):
            raise AuthError('用户名或密码错误')
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)', (_token_hash(token), row['id'], int(time.time()) + SESSION_SECONDS))
        return token, {'id': row['id'], 'username': row['username'], 'role': row['role']}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def get_user(path: Path, token: str | None) -> dict | None:
    if not token:
        return None
    with _connect(path) as db:
        row = db.execute('SELECT users.id, users.username, users.role FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token_hash = ? AND sessions.expires_at > ?', (_token_hash(token), int(time.time()))).fetchone()
        return dict(row) if row else None


def logout(path: Path, token: str | None):
    if token:
        with _connect(path) as db:
            db.execute('DELETE FROM sessions WHERE token_hash = ?', (_token_hash(token),))


def list_local_users(path: Path) -> list[dict]:
    """供本机恢复工具列出账号；只读打开，不创建新的账号库。"""
    if not path.is_file():
        raise AuthError('本地账号库不存在')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return [{'username': row[0], 'role': row[1]}
                for row in db.execute('SELECT username, role FROM users ORDER BY id')]
    finally:
        db.close()


def reset_local_password(path: Path, username: str, new_password: str):
    """仅供本机交互式工具调用；保留账号 ID 和数据，撤销该账号全部会话。"""
    _validate(username, new_password)
    if not path.is_file():
        raise AuthError('本地账号库不存在')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if row is None:
            raise AuthError('账号不存在')
        salt = secrets.token_bytes(16)
        db.execute('UPDATE users SET salt = ?, password_hash = ? WHERE id = ?',
                   (salt, _password_hash(new_password, salt), row[0]))
        db.execute('DELETE FROM sessions WHERE user_id = ?', (row[0],))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
