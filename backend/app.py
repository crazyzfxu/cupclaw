#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClawChat - 后端服务
Flask + WebSocket 实现多角色实时聊天
"""

from flask import Flask, render_template, request, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import sqlite3
import os
import socket
import sys
import time
import requests
import logging
import uuid
import json
import re
import hashlib
from functools import wraps
from threading import Lock
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=True)
app.secret_key = os.environ.get("CLAWCHAT_SECRET_KEY", "change-me-in-production")

# 配置会话 Cookie
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=3600  # 1小时
)

# 禁用缓存，确保浏览器始终加载最新页面
@app.after_request
def disable_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')

# OpenClaw 配置
OPENCLAW_URL = os.environ.get("OPENCLAW_URL", "http://localhost:18789")
OPENCLAW_TOKEN = os.environ.get("OPENCLAW_TOKEN", "")

# Agent 配置
AGENTS = {
    'main': {'name': '不周🏔️', 'color': '#1e3c72'}
}

# 文件上传配置
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
# 临时存储目录（用户上传的文件先放这里，等用户决定如何处理）
TEMP_FOLDER = os.path.join(os.path.dirname(__file__), 'temp')
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'md', 'json', 'csv', 'xlsx', 'docx', 'doc', 'mhtml', 'mht'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
MAX_QUERY_ROWS = int(os.environ.get("CLAWCHAT_MAX_QUERY_ROWS", "200"))

# 是否允许 ops 查看所有消息（默认允许）；可通过环境变量关闭以限制 ops 可见范围
OPS_FULL_ACCESS = os.environ.get("CLAWCHAT_OPS_FULL_ACCESS", "1") in ("1", "true", "True", "yes", "on")

# 默认账号配置（用于体验）
DEFAULT_ADMIN_USERNAME = os.environ.get("CLAWCHAT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("CLAWCHAT_ADMIN_PASSWORD", "admin123")
DEFAULT_USER_USERNAME = os.environ.get("CLAWCHAT_USER_USERNAME", "user")
DEFAULT_USER_PASSWORD = os.environ.get("CLAWCHAT_USER_PASSWORD", "user123")

# 默认数据源（可通过环境变量指定）
DEFAULT_SOURCE_PATH = os.environ.get("CLAWCHAT_DEFAULT_DB_PATH", DB_PATH)
DEFAULT_SOURCE_TABLES = os.environ.get("CLAWCHAT_DEFAULT_ALLOWED_TABLES", "")
PENDING_REQUEST_TTL = int(os.environ.get("CLAWCHAT_PENDING_REQUEST_TTL", "300"))
POLICY_VERSION = os.environ.get("CLAWCHAT_POLICY_VERSION", "v1")
POLICY_TEXT_ENABLED = os.environ.get("CLAWCHAT_POLICY_TEXT_ENABLED", "1") in ("1", "true", "True", "yes", "on")
OPENCLAW_CMD = os.environ.get("CLAWCHAT_OPENCLAW_CMD", "/Users/buz/openclaw/openclaw.mjs")
OPENCLAW_CMD_TIMEOUT = int(os.environ.get("CLAWCHAT_OPENCLAW_TIMEOUT", "120"))
OPENCLAW_TRANSPORT = os.environ.get("CLAWCHAT_OPENCLAW_TRANSPORT", "gateway").strip().lower()
OPENCLAW_CLI_FALLBACK = os.environ.get("CLAWCHAT_OPENCLAW_CLI_FALLBACK", "0") in ("1", "true", "True", "yes", "on")
OPENCLAW_SCOPES = os.environ.get("CLAWCHAT_OPENCLAW_SCOPES", "operator.write").strip()
OPENCLAW_HISTORY_LIMIT = int(os.environ.get("CLAWCHAT_OPENCLAW_HISTORY_LIMIT", "12"))
OPENCLAW_SESSION_PREFIX = os.environ.get("CLAWCHAT_OPENCLAW_SESSION_PREFIX", "clawchat")
QUICK_LOGIN_ENABLED = os.environ.get("CLAWCHAT_QUICK_LOGIN_ENABLED", "1") in ("1", "true", "True", "yes", "on")
ALLOW_SELF_REGISTER = os.environ.get("CLAWCHAT_ALLOW_SELF_REGISTER", "1") in ("1", "true", "True", "yes", "on")

# 用户级请求处理中锁，防止网络慢时重复提交导致重复转发
PENDING_REQUESTS = {}
PENDING_REQUESTS_LOCK = Lock()

# 创建目录
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 消息表 - 添加 agent_id 字段
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'main',
            sender TEXT NOT NULL,
            owner TEXT,
            user_id INTEGER,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'sent',
            type TEXT DEFAULT 'text',
            file_url TEXT,
            file_name TEXT
        )
    ''')
    
    # 索引提高查询性能
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_agent_timestamp ON messages(agent_id, timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_agent_user ON messages(agent_id, user_id)')

    # 用户表（体验版）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 添加用户表索引以优化查询性能
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_role_active ON users(role, is_active)')

    # 数据源注册表（目前仅支持 sqlite）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            db_type TEXT NOT NULL DEFAULT 'sqlite',
            connection TEXT NOT NULL,
            allowed_tables TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 审计日志（简版）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 待审批请求表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            submitter TEXT NOT NULL,
            approver TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    seed_default_users()
    seed_default_source()
    # 兼容迁移：旧版本 messages 表字段补齐
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(messages)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'owner' not in cols:
            cursor.execute('ALTER TABLE messages ADD COLUMN owner TEXT')
        if 'user_id' not in cols:
            cursor.execute('ALTER TABLE messages ADD COLUMN user_id INTEGER')
            conn.commit()
        
        # 检查 users 表是否有 name 字段
        cursor.execute("PRAGMA table_info(users)")
        user_cols = [row[1] for row in cursor.fetchall()]
        if 'name' not in user_cols:
            cursor.execute('ALTER TABLE users ADD COLUMN name TEXT')
            conn.commit()
        
        conn.close()
    except Exception:
        pass

    print("✅ 数据库初始化完成")

def get_conn(path=DB_PATH):
    """获取 SQLite 连接（带 Row 工厂）"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # 注册北京时间函数（返回 ISO 格式的 UTC+8 时间）
    import datetime as dt
    def beijing_now():
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    conn.create_function("beijing_now", 0, beijing_now)
    return conn

def write_audit(action, detail=None):
    """记录简单审计日志"""
    username = session.get('username', 'anonymous')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO audit_logs (username, action, detail, created_at) VALUES (?, ?, ?, ?)',
        (username, action, detail or '', beijing_now_str())
    )
    conn.commit()
    conn.close()

def seed_default_users():
    """创建体验账号（不存在时）"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for username, password, role in [
        (DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, 'ops'),
        (DEFAULT_USER_USERNAME, DEFAULT_USER_PASSWORD, 'user'),
        ('dept_head', 'dept123', 'dept_head'),
        ('buzhouai', 'buzhou123', 'dept_head')
    ]:
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cursor.fetchone() is None:
            cursor.execute(
                'INSERT INTO users (username, password_hash, role, is_active) VALUES (?, ?, ?, 1)',
                (username, generate_password_hash(password), role)
            )

    conn.commit()
    conn.close()

def validate_password(password):
    """验证密码复杂度"""
    if len(password) < 8:
        return False, "密码长度至少8位"
    if not re.search(r'[A-Za-z]', password):
        return False, "密码必须包含字母"
    if not re.search(r'[0-9]', password):
        return False, "密码必须包含数字"
    return True, ""

def create_user_account(username, password, role='user', name=None, is_active=1):
    """创建用户账号并返回用户基本信息"""
    # 验证密码
    valid, msg = validate_password(password)
    if not valid:
        return None, msg

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
    if cursor.fetchone() is not None:
        conn.close()
        return None, "用户名已存在"

    cursor.execute(
        'INSERT INTO users (username, password_hash, name, role, is_active) VALUES (?, ?, ?, ?, ?)',
        (username, generate_password_hash(password), name, role, int(is_active))
    )
    user_id = cursor.lastrowid
    conn.commit()
    cursor.execute('SELECT id, username, name, role, is_active FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None, ""

# ========== 待审批请求管理 ==========

def add_pending_request(request_type, title, content, submitter):
    """添加待审批请求"""
    conn = get_conn()
    cursor = conn.cursor()
    ts = beijing_now_str()
    cursor.execute(
        'INSERT INTO pending_requests (request_type, title, content, submitter, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (request_type, title, content, submitter, 'pending', ts, ts)
    )
    conn.commit()
    req_id = cursor.lastrowid
    conn.close()
    return req_id

def get_pending_requests(status='pending'):
    """获取待审批请求列表"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM pending_requests WHERE status = ? ORDER BY created_at DESC',
        (status,)
    )
    requests = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return requests

def get_pending_request_by_id(req_id):
    """获取单个请求"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM pending_requests WHERE id = ?', (req_id,))
    req = cursor.fetchone()
    conn.close()
    return dict(req) if req else None

def approve_pending_request(req_id, approver):
    """审批通过请求"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE pending_requests SET status = ?, approver = ?, updated_at = ? WHERE id = ?',
        ('approved', approver, beijing_now_str(), req_id)
    )
    conn.commit()
    rows = cursor.rowcount
    conn.close()
    return rows > 0

def reject_pending_request(req_id, approver):
    """审批拒绝请求"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE pending_requests SET status = ?, approver = ?, updated_at = ? WHERE id = ?',
        ('rejected', approver, beijing_now_str(), req_id)
    )
    conn.commit()
    rows = cursor.rowcount
    conn.close()
    return rows > 0

def seed_default_source():
    """创建默认数据源（不存在时）"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM data_sources WHERE name = ?', ('default',))
    if cursor.fetchone() is None and os.path.exists(DEFAULT_SOURCE_PATH):
        cursor.execute(
            'INSERT INTO data_sources (name, db_type, connection, allowed_tables, is_active) VALUES (?, ?, ?, ?, 1)',
            ('default', 'sqlite', DEFAULT_SOURCE_PATH, DEFAULT_SOURCE_TABLES)
        )
    conn.commit()
    conn.close()

def get_current_user():
    """读取当前登录用户信息"""
    user_id = session.get('user_id')
    if not user_id:
        return None

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, name, role, is_active FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()

    if user and user['is_active'] == 1:
        return dict(user)
    return None

def login_required(func):
    """接口登录校验"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'error': '请先登录'}), 401
        return func(*args, **kwargs)
    return wrapper

def role_required(*allowed_roles):
    """接口角色校验"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({'error': '请先登录'}), 401
            if user.get('role') not in allowed_roles:
                return jsonify({'error': '无权访问该能力'}), 403
            return func(*args, **kwargs)
        return wrapper
    return decorator

def build_user_payload(user):
    """统一返回前端用户信息和能力"""
    role = user['role']
    return {
        'id': user['id'],
        'username': user['username'],
        'name': user.get('name'),
        'role': role,
        'capabilities': {
            'chat': True,
            'upload': True,
            'ops_data_debug': role == 'ops'
        }
    }

def build_openclaw_context(user_payload, agent_id):
    """构建传递给 OpenClaw 的请求上下文"""
    if not user_payload:
        return {
            'user_id': None,
            'username': 'anonymous',
            'role': 'unknown',
            'capabilities': {'chat': True},
            'agent': agent_id,
            'source': 'clawchat'
        }

    return {
        'user_id': user_payload.get('id'),
        'username': user_payload.get('username', 'anonymous'),
        'role': user_payload.get('role', 'unknown'),
        'capabilities': user_payload.get('capabilities', {}),
        'agent': agent_id,
        'source': 'clawchat'
    }

def build_openclaw_policy(user_payload, agent_id):
    """构建传递给 OpenClaw 的结构化策略合同（机器可解析）"""
    role = (user_payload or {}).get('role', 'unknown')
    username = (user_payload or {}).get('username', 'anonymous')
    request_id = str(uuid.uuid4())

    if role == 'dept_head':
        allowed_actions = [
            'chat.ask',
            'db.read',
            'db.write'
        ]
        pending_actions = ['email.send']
        denied_actions = [
            'rule.modify',
            'ops.high_risk',
            'system.config'
        ]
        user_label = '部门主管'
    elif role == 'ops':
        allowed_actions = [
            'chat.ask',
            'db.read',
            'db.write',
            'code.modify'
        ]
        pending_actions = []
        denied_actions = [
            'rule.modify',
            'system.config'
        ]
        user_label = '运维人员'
    else:
        allowed_actions = [
            'chat.ask',
            'db.read'
        ]
        pending_actions = ['db.write', 'email.send']
        denied_actions = [
            'rule.modify',
            'ops.high_risk',
            'system.config'
        ]
        user_label = '普通用户'

    return {
        'policy_version': POLICY_VERSION,
        'request_id': request_id,
        'subject': {
            'username': username,
            'role': role,
            'label': user_label
        },
        'agent': agent_id,
        'enforcement': {
            'must_deny_on_violation': True,
            'deny_template': '该请求超出当前用户权限范围（原因：{reason}）。你可以改为：{alternative}'
        },
        'allowed_actions': allowed_actions,
        'pending_actions': pending_actions if role != 'ops' else [],
        'denied_actions': denied_actions
    }

def build_openclaw_policy_text(policy, user_message):
    """构建传递给 OpenClaw 的大白话规则（模型更易理解）"""
    subject = policy.get('subject', {})
    label = subject.get('label', '某部门普通用户')
    allowed_text = "\n".join([f"- {item}" for item in policy.get('allowed_actions', [])]) or '- chat.ask'
    denied_text = "\n".join([f"- {item}" for item in policy.get('denied_actions', [])]) or '- db.write'
    pending_text = "\n".join([f"- {item}" for item in policy.get('pending_actions', [])]) or '- 无'
    role = subject.get('role', 'unknown')

    # 审批流程说明（仅 dept_head 和 ops 需要知道）
    approval_note = ""
    if role in ('dept_head', 'ops'):
        approval_note = (
            "\n【审批流程】\n"
            "当其他用户提交了需要审批的请求时，你会收到一条转发消息。\n"
            "审批方式：直接回复「批准」或「拒绝」即可，审批请求会通过关键词自动匹配并执行。\n"
            "批准关键词：批准/确认/ok/yes/好的/reject（通用语均可）\n"
            "拒绝关键词：拒绝/no/reject不行\n"
        )
    else:
        approval_note = (
            "\n【暂存审批流程】\n"
            "如果你的请求需要审批（如 db.write），系统会自动转发给部门主管。\n"
            "部门主管回复「批准」后，请求会自动执行并通知你结果。\n"
        )

    return (
        f"你当前服务对象：{label}（role={role}）\n"
        "该用户允许：\n"
        f"{allowed_text}\n\n"
        "该用户需要审批后执行（暂存）：\n"
        f"{pending_text}\n\n"
        "该用户不允许：\n"
        f"{denied_text}\n"
        f"{approval_note}\n\n"
        "当前用户需求：\n"
        f"\"{(user_message or '').strip()}\"\n\n"
        "执行要求：\n"
        "1) 先判断该需求是否在允许范围内\n"
        "2) 若满足权限，继续执行并返回结果\n"
        "3) 若需要审批（pending_actions），将请求转发给部门主管，不要直接执行\n"
        "4) 若不满足权限，必须明确拒绝，并说明拒绝原因与可替代做法\n"
        "拒绝模板：\n"
        "该请求超出当前用户权限范围（原因：...）。你可以改为：..."
    )

def is_duplicate_submission(agent_id, message, file_name=None, window_seconds=20):
    """基于会话做短时间重复点击提交拦截（仅按原始内容精确匹配）"""
    signature = f"{agent_id}|{(message or '').strip()}|{(file_name or '').strip()}"
    now = time.time()
    last_signature = session.get('last_submit_signature')
    last_ts = session.get('last_submit_ts', 0)

    is_dup = last_signature == signature and (now - float(last_ts)) < window_seconds
    session['last_submit_signature'] = signature
    session['last_submit_ts'] = now
    return is_dup

def get_pending_key(user, agent_id):
    """生成用户+agent 的处理中键"""
    username = (user or {}).get('username', 'anonymous')
    return f"{username}|{agent_id}"

def has_pending_request(user, agent_id):
    """检查是否存在未完成请求"""
    key = get_pending_key(user, agent_id)
    now = time.time()
    with PENDING_REQUESTS_LOCK:
        last_ts = PENDING_REQUESTS.get(key)
        if last_ts is None:
            return False
        if now - float(last_ts) > PENDING_REQUEST_TTL:
            # 超时自动回收，避免异常情况下永久锁死
            PENDING_REQUESTS.pop(key, None)
            return False
        return True

def mark_pending_request(user, agent_id):
    """标记请求处理中"""
    key = get_pending_key(user, agent_id)
    with PENDING_REQUESTS_LOCK:
        PENDING_REQUESTS[key] = time.time()

def clear_pending_request(user, agent_id):
    """清理请求处理中标记"""
    key = get_pending_key(user, agent_id)
    with PENDING_REQUESTS_LOCK:
        PENDING_REQUESTS.pop(key, None)

def get_first_active_user_by_role(role):
    """按角色获取可用用户（用于免账号快速登录）"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, username, role, is_active FROM users WHERE role = ? AND is_active = 1 ORDER BY id ASC LIMIT 1',
        (role,)
    )
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def socket_logged_in():
    """Socket 登录状态校验"""
    return session.get('user_id') is not None

def parse_allowed_tables(allowed_tables):
    """将配置串解析为表名集合"""
    if not allowed_tables:
        return set()
    return {item.strip().lower() for item in allowed_tables.split(',') if item.strip()}

def extract_sql_tables(sql):
    """提取 SQL 中 FROM/JOIN 的表名（简版）"""
    table_names = set()
    pattern = re.compile(r'\b(?:from|join)\s+([a-zA-Z_][\w\.]*)', re.IGNORECASE)
    for match in pattern.finditer(sql):
        table = match.group(1).split('.')[-1].strip('"`[]').lower()
        if table:
            table_names.add(table)
    return table_names

def is_readonly_sql(sql):
    """只允许 SELECT/WITH 查询"""
    cleaned = (sql or '').strip().lower()
    if not cleaned:
        return False

    # 防止多语句执行
    if ';' in cleaned[:-1]:
        return False

    if not (cleaned.startswith('select') or cleaned.startswith('with')):
        return False

    blocked = ['insert', 'update', 'delete', 'drop', 'alter', 'create', 'attach', 'detach', 'vacuum', 'pragma']
    return all(keyword not in cleaned for keyword in blocked)

def enforce_limit(sql, max_rows=MAX_QUERY_ROWS):
    """自动追加 LIMIT，避免全表扫"""
    normalized = sql.strip().rstrip(';')
    if re.search(r'\blimit\s+\d+', normalized, re.IGNORECASE):
        return normalized
    return f"{normalized} LIMIT {max_rows}"

def get_source_by_id(source_id):
    """按 ID 获取数据源"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, name, db_type, connection, allowed_tables, is_active FROM data_sources WHERE id = ?',
        (source_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def query_sqlite_source(source, sql):
    """执行只读 SQLite 查询"""
    query_sql = enforce_limit(sql)
    conn = sqlite3.connect(source['connection'])
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(query_sql)
    rows = [dict(row) for row in cursor.fetchall()]
    columns = list(rows[0].keys()) if rows else [desc[0] for desc in (cursor.description or [])]
    conn.close()
    return {
        'columns': columns,
        'rows': rows,
        'row_count': len(rows),
        'limited': 'limit' not in sql.lower()
    }

_UNSET = object()

def beijing_now_str():
    """返回北京时间的 ISO 格式字符串"""
    import datetime as dt
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

def save_message(agent_id, sender, content, msg_type='text', file_url=None, file_name=None, user_id=_UNSET):
    """保存消息到数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # If user_id not provided at all, try to read from session
    if user_id is _UNSET:
        try:
            user_id = session.get('user_id')
        except Exception:
            user_id = None
    # If explicitly passed None, keep it as None (public message)

    ts = beijing_now_str()

    # Insert with user_id if column exists
    try:
        cursor.execute('''
            INSERT INTO messages (agent_id, sender, content, type, status, file_url, file_name, user_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (agent_id, sender, content, msg_type, 'sent', file_url, file_name, user_id, ts))
    except sqlite3.OperationalError:
        # Fallback if column doesn't exist
        cursor.execute('''
            INSERT INTO messages (agent_id, sender, content, type, status, file_url, file_name, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (agent_id, sender, content, msg_type, 'sent', file_url, file_name, ts))

    msg_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return msg_id

def get_messages(agent_id='main', limit=50, user_id=None):
    """获取指定 agent 的最近消息，可按 user_id 过滤"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if user_id is None:
        # 仅限内部系统调用或运维查看，不应直接暴露给普通前端 API
        cursor.execute('''
            SELECT * FROM messages 
            WHERE agent_id = ?
            ORDER BY id DESC 
            LIMIT ?
        ''', (agent_id, limit))
    else:
        # 严格隔离：仅查询属于该用户的消息，以及由系统明确发送给所有人的公告(如审批请求)
        # 注意：此处使用 type 而非 msg_type（原代码列名为 type）
        cursor.execute('''
            SELECT * FROM messages 
            WHERE agent_id = ? AND (
                user_id = ? OR 
                (user_id IS NULL AND (type = 'pending_request' OR sender = 'system'))
            )
            ORDER BY id DESC 
            LIMIT ?
        ''', (agent_id, user_id, limit))

    messages = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return messages[::-1]  # 反转，按时间正序

def build_openclaw_messages_history(agent_id, user_id, current_message, limit=OPENCLAW_HISTORY_LIMIT):
    """构建发给 OpenClaw 的对话历史（OpenAI messages 格式）"""
    if not user_id:
        return []

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        '''
        SELECT sender, content
        FROM messages
        WHERE agent_id = ? AND (user_id IS NULL OR user_id = ?)
        ORDER BY id DESC
        LIMIT ?
        ''',
        (agent_id, user_id, max(1, int(limit)))
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    rows.reverse()

    # 当前用户消息已在 process_message 里入库，避免重复拼接一次
    if rows:
        last = rows[-1]
        if last.get('sender') == 'user' and (last.get('content') or '').strip() == (current_message or '').strip():
            rows = rows[:-1]

    history = []
    for item in rows:
        sender = item.get('sender')
        content = (item.get('content') or '').strip()
        if not content:
            continue
        if sender == 'user':
            history.append({'role': 'user', 'content': content})
        elif sender in ('assistant', 'system'):
            history.append({'role': 'assistant', 'content': content})

    return history

def build_openclaw_session_id(user_id, username, agent_id):
    """按 user + agent 生成稳定会话 ID，避免每次请求丢上下文"""
    raw_user = str(user_id) if user_id not in (None, '', 'None') else (username or 'anonymous')
    safe_user = re.sub(r'[^A-Za-z0-9_-]', '_', raw_user)[:24] or 'anonymous'
    safe_agent = re.sub(r'[^A-Za-z0-9_-]', '_', str(agent_id or 'main'))[:24] or 'main'
    short_hash = hashlib.sha1(f"{safe_user}:{safe_agent}".encode('utf-8')).hexdigest()[:10]
    return f"{OPENCLAW_SESSION_PREFIX}_{safe_user}_{safe_agent}_{short_hash}"

def get_all_agents_with_last_message(user_id=None):
    """获取所有 agent 及其最后一条消息，支持按用户隔离"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    agents_data = {}
    
    # 获取当前用户的角色，决定是否显示全局待处理请求预览
    user_role = None
    if user_id:
        cursor.execute('SELECT role FROM users WHERE id = ?', (user_id,))
        role_row = cursor.fetchone()
        if role_row:
            user_role = role_row['role']

    for agent_id, agent_info in AGENTS.items():
        # 这里仅提供 Agent 基础元数据，预览信息由前端 updatePreviews 独立获取，
        # 从而避免 loadAgents (GET /agents) 携带任何可能跨用户的消息内容。
        agents_data[agent_id] = {
            'name': agent_info['name'],
            'color': agent_info['color'],
            'last_message': '正在加载...',
            'last_time': None,
            'unread_count': 0
        }
    
    conn.close()
    return agents_data

def get_message_by_id(msg_id):
    """根据 ID 获取单条消息"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM messages WHERE id = ?
    ''', (msg_id,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None

def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_file(file, to_temp=False):
    """保存上传的文件
    
    Args:
        file: 上传的文件对象
        to_temp: 是否保存到临时目录（默认 False，保存到 uploads）
    """
    if file and allowed_file(file.filename):
        original_filename = file.filename
        # 保留原始文件名（包括中文）
        # 添加到临时目录时不添加时间戳，保持原始文件名
        if to_temp:
            # 临时目录：保持原始文件名，方便用户识别
            safe_name = "".join(c for c in original_filename if c.isalnum() or c in '._-()（）')
            filepath = os.path.join(TEMP_FOLDER, safe_name)
            # 如果文件已存在，添加序号
            base, ext = os.path.splitext(safe_name)
            counter = 1
            while os.path.exists(filepath):
                filepath = os.path.join(TEMP_FOLDER, f"{base}_{counter}{ext}")
                counter += 1
            unique_filename = os.path.basename(filepath)
        else:
            # uploads 目录：添加时间戳避免重名
            timestamp = str(int(time.time()))
            name_parts = original_filename.rsplit('.', 1)
            if len(name_parts) == 2:
                name, ext = name_parts
                safe_name = "".join(c for c in name if c.isalnum() or c in '._-()（）')
                unique_filename = f"{timestamp}_{safe_name}.{ext}"
            else:
                safe_name = "".join(c for c in original_filename if c.isalnum() or c in '._-()（）')
                unique_filename = f"{timestamp}_{safe_name}"
            filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        file.save(filepath)
        return {
            'filename': original_filename,
            'unique_filename': unique_filename,
            'filepath': filepath,
            'url': f'/temp/{unique_filename}' if to_temp else f'/uploads/{unique_filename}',
            'is_temp': to_temp
        }
    return None

def check_openclaw_status():
    """检测 OpenClaw 服务可用性"""
    try:
        # 主检测走 Gateway HTTP（版本变化下最稳定）
        response = requests.get(f"{OPENCLAW_URL}/", timeout=3)
        if response.status_code in [200, 301, 302]:
            return True

        if OPENCLAW_CLI_FALLBACK:
            import subprocess
            result = subprocess.run(
                ['openclaw', 'gateway', 'status'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0 and 'running' in result.stdout.lower()

        return False
    except Exception:
        return False

def get_webchat_session_id():
    """获取 webchat 会话 ID"""
    import subprocess
    try:
        result = subprocess.run(
            ['openclaw', 'sessions', '--json'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout.strip())
            sessions = data.get('sessions', [])
            # 返回第一个会话（webchat 使用主会话）
            if sessions:
                return sessions[0].get('sessionId')
    except Exception:
        pass
    return None

def send_via_gateway(session_id, full_message, agent_id='main', messages_history=None):
    """通过 OpenClaw Gateway 的 /v1/chat/completions 接口发送消息。

    使用 OpenAI-compatible API 直接调用 OpenClaw agent。
    返回与原有 send_to_openclaw 兼容的字典结构，失败时返回 None。
    """
    import urllib.request
    import urllib.error

    headers = {
        'Content-Type': 'application/json',
    }
    if OPENCLAW_TOKEN:
        headers['Authorization'] = f'Bearer {OPENCLAW_TOKEN}'
    if OPENCLAW_SCOPES:
        headers['x-openclaw-scopes'] = OPENCLAW_SCOPES
    # If caller provided a session id, pass it to Gateway/MCP for server-side session routing
    try:
        if session_id:
            headers['x-openclaw-session-key'] = str(session_id)
            logger.info(f"send_via_gateway: attaching x-openclaw-session-key={session_id} for agent={agent_id}")
    except Exception:
        # be defensive: don't fail the request due to logging/session formatting
        pass

    # 使用 OpenAI-compatible endpoint，model 字段指定 agent
    messages = list(messages_history or [])
    messages.append({'role': 'user', 'content': full_message})

    payload = {
        'model': f'openclaw/{agent_id}' if agent_id != 'main' else 'openclaw/main',
        'messages': messages
    }

    try:
        req = urllib.request.Request(
            f"{OPENCLAW_URL}/v1/chat/completions",
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            # OpenAI 格式：data.choices[0].message.content
            if isinstance(data, dict):
                choices = data.get('choices', [])
                if choices and len(choices) > 0:
                    msg = choices[0].get('message', {})
                    content = msg.get('content', '')
                    if content:
                        return {'message': content}
                # 完整返回
                return data
            return None
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8')
            print(f"Gateway HTTP {e.code}: {err_body[:200]}")
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"Gateway error: {e}")
        return None

def call_sub_agent(agent_id, message):
    """调用子 agent（小墨/小文/小然）"""
    import subprocess
    
    ROLE_PROMPTS = {
        'code': '你现在是程墨（小墨），男性，编程专家/技术顾问。性格沉稳话少，技术扎实。说话直接，不废话，代码优先。请用这个身份回复用户。',
        'data': '你现在是舒文（小文），女性，数据分析师。性格细心严谨，说话条理清晰，用数据说话。请用这个身份回复用户。',
        'files': '你现在是井然（小然），男性，文件管家。性格稳重靠谱，做事有条理。请用这个身份回复用户。'
    }
    
    role_msg = f"{ROLE_PROMPTS.get(agent_id, '')}\n\n用户消息：{message}"
    cmd = [OPENCLAW_CMD, 'agent', '--agent', agent_id, '--message', role_msg, '--json']
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout.strip())
            result_obj = data.get('result')
            if isinstance(result_obj, dict):
                payloads = result_obj.get('payloads')
                if isinstance(payloads, list):
                    texts = [item.get('text', '') for item in payloads if isinstance(item, dict) and isinstance(item.get('text'), str)]
                    if texts:
                        return "\n\n".join(texts)
        return f"调用 {agent_id} 失败"
    except Exception as e:
        return f"调用 {agent_id} 出错：{str(e)}"

def send_to_openclaw(message, agent_id='main', file_url=None, file_name=None, requester_context=None):
    """发送消息到 OpenClaw，支持多 agent 路由和任务协调
    
    Args:
        message: 用户消息
        agent_id: agent 标识
        file_url: 文件 URL（如果有）
        file_name: 文件名（如果有）
    """
    import subprocess
    import json
    
    logger.info(f"开始处理消息到 OpenClaw: agent={agent_id}, user={requester_context.get('username', 'unknown') if requester_context else 'unknown'}")
    
    # 构建完整消息（包括文件信息）
    full_message = message or ''
    if file_url and file_name:
        stored_filename = os.path.basename((file_url or '').split('?', 1)[0])
        file_path = TEMP_FOLDER if '/temp/' in file_url else UPLOAD_FOLDER
        actual_file_path = os.path.join(file_path, stored_filename)
        file_info = (
            f"\n\n[用户上传了文件：{file_name}]"
            f"\n[存储文件名：{stored_filename}]"
            f"\n[文件路径：{actual_file_path}]"
            f"\n[请根据上下文理解用户意图，不要机械回复]"
        )
        full_message = (full_message + file_info) if full_message else file_info
    
    # 仅传递简单用户标识，权限由不周本地 ACL 独立判定
    username = (requester_context or {}).get('username', 'unknown')
    role = (requester_context or {}).get('role', 'user')
    user_id = (requester_context or {}).get('user_id')
    user_tag = f"[CLAWCHAT_USER]user_id={user_id}&username={username}&role={role}[/CLAWCHAT_USER]"

    chunks = [user_tag, f"用户需求：{full_message}"]
    full_message = "\n\n".join(chunks)

    messages_history = build_openclaw_messages_history(
        agent_id=agent_id,
        user_id=user_id,
        current_message=message,
        limit=OPENCLAW_HISTORY_LIMIT
    )

    # 同一用户+agent 复用稳定 session，保证上下文连续
    session_id = build_openclaw_session_id(user_id, username, agent_id)

    def parse_openclaw_json(payload):
        if not isinstance(payload, dict):
            return None

        # Handle OpenAI-compatible format first
        choices = payload.get('choices')
        if isinstance(choices, list) and len(choices) > 0:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get('message')
                if isinstance(message, dict):
                    content = message.get('content')
                    if isinstance(content, str) and content.strip():
                        return content.strip()

        result_obj = payload.get('result')
        if isinstance(result_obj, dict):
            payloads = result_obj.get('payloads')
            if isinstance(payloads, list):
                texts = [
                    item.get('text', '').strip()
                    for item in payloads
                    if isinstance(item, dict) and isinstance(item.get('text'), str) and item.get('text').strip()
                ]
                if texts:
                    return "\n\n".join(texts)

        payloads = payload.get('payloads')
        if isinstance(payloads, list):
            texts = [
                item.get('text', '').strip()
                for item in payloads
                if isinstance(item, dict) and isinstance(item.get('text'), str) and item.get('text').strip()
            ]
            if texts:
                return "\n\n".join(texts)

        for key in ('message', 'reply', 'text'):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    try:
        if OPENCLAW_TRANSPORT in ('gateway', 'auto'):
            logger.info(f"尝试通过 Gateway 发送消息: transport={OPENCLAW_TRANSPORT}")
            gw_resp = send_via_gateway(
                session_id,
                full_message,
                agent_id=agent_id,
                messages_history=messages_history
            )
            parsed = parse_openclaw_json(gw_resp) if isinstance(gw_resp, dict) else None
            if parsed:
                logger.info(f"Gateway 调用成功，返回内容长度: {len(parsed)}")
                return {'message': parsed}
            # gw_resp 为 None 表示超时（gateway 还在处理中），否则是实际错误
            if gw_resp is None:
                logger.warning(f"Gateway 调用超时（60s），可能正在处理长任务")
                return {'error': '正在处理中，请稍候...'}
            else:
                logger.warning(f"Gateway 调用失败，无法解析响应: {gw_resp}")
                if OPENCLAW_TRANSPORT == 'gateway' and not OPENCLAW_CLI_FALLBACK:
                    return {'error': 'Gateway 调用失败，未返回有效内容'}

        if OPENCLAW_TRANSPORT in ('cli', 'auto') and OPENCLAW_CLI_FALLBACK:
            logger.info(f"尝试通过 CLI 发送消息: agent={target_agent}")
            target_agent = agent_id if agent_id in ['main', 'code', 'data', 'files'] else 'main'
            result = subprocess.run(
                [OPENCLAW_CMD, 'agent', '--agent', target_agent, '--message', full_message, '--json', '--session-id', session_id],
                capture_output=True,
                text=True,
                timeout=OPENCLAW_CMD_TIMEOUT
            )
            if result.returncode != 0:
                logger.error(f"CLI 执行失败: {result.stderr or '未知错误'}")
                return {'error': (result.stderr or 'OpenClaw CLI 执行失败').strip()}

            try:
                data = json.loads((result.stdout or '').strip())
                parsed = parse_openclaw_json(data)
                if parsed:
                    logger.info(f"CLI 调用成功，返回内容长度: {len(parsed)}")
                    return {'message': parsed}
            except json.JSONDecodeError:
                logger.error(f"CLI 返回 JSON 解析失败: {result.stdout}")
                pass
            return {'message': (result.stdout or '').strip()}

        logger.error("OpenClaw 调用未配置可用通道")
        return {'error': 'OpenClaw 调用未配置可用通道，请检查 CLAWCHAT_OPENCLAW_TRANSPORT'}
    
    except subprocess.TimeoutExpired:
        logger.error("OpenClaw 响应超时")
        return {"error": "OpenClaw 响应超时"}
    except Exception as e:
        logger.error(f"调用 OpenClaw 失败: {str(e)}")
        return {"error": f"调用 OpenClaw 失败：{str(e)}"}

def check_file_exists(file_name, archive_dir='/Users/buz/Documents/专利数据/归档'):
    """检查文件是否已存在"""
    import glob
    
    # 检查目标目录
    dst_path = os.path.join(archive_dir, file_name)
    if os.path.exists(dst_path):
        return {
            'exists': True,
            'path': dst_path,
            'size': os.path.getsize(dst_path),
            'mtime': os.path.getmtime(dst_path)
        }
    
    # 检查子目录（按日期分类的情况）
    for subdir in os.listdir(archive_dir):
        subdir_path = os.path.join(archive_dir, subdir)
        if os.path.isdir(subdir_path):
            sub_path = os.path.join(subdir_path, file_name)
            if os.path.exists(sub_path):
                return {
                    'exists': True,
                    'path': sub_path,
                    'size': os.path.getsize(sub_path),
                    'mtime': os.path.getmtime(sub_path),
                    'subdir': subdir
                }
    
    return {'exists': False}

def suggest_archive_dir(file_name):
    """根据文件名建议归档目录"""
    # 提取公司名
    name_parts = file_name.replace('.csv', '').split('_')
    if name_parts:
        company = name_parts[0]
        # 检查是否已有该公司目录
        archive_dir = '/Users/buz/Documents/专利数据'
        if os.path.exists(archive_dir):
            for item in os.listdir(archive_dir):
                if company in item and os.path.isdir(os.path.join(archive_dir, item)):
                    return os.path.join(archive_dir, item)
    
    # 默认归档目录
    return '/Users/buz/Documents/专利数据/归档'

def archive_file(file_url, file_name, auto_archive=False):
    """归档文件到专利数据目录"""
    import shutil
    from datetime import datetime
    
    try:
        # 源文件路径
        src_path = file_url.replace('/uploads/', '/Users/buz/Documents/clawchat/backend/uploads/')
        
        # 建议目录
        suggested_dir = suggest_archive_dir(file_name)
        
        # 检查文件是否存在
        file_check = check_file_exists(file_name, suggested_dir)
        
        if file_check['exists'] and not auto_archive:
            # 文件已存在，需要确认
            existing_info = {
                'filename': file_name,
                'path': file_check['path'],
                'size': file_check['size'],
                'mtime': datetime.fromtimestamp(file_check['mtime']).strftime('%Y-%m-%d %H:%M:%S'),
                'suggested_dir': suggested_dir
            }
            return {
                'needs_confirmation': True,
                'existing_file': existing_info,
                'message': f"⚠️ 文件已存在\n\n📁 {file_name}\n📍 位置：{file_check['path']}\n📊 大小：{file_check['size']} bytes\n🕒 归档时间：{existing_info['mtime']}\n\n是否覆盖？"
            }
        
        # 文件不存在，直接归档
        os.makedirs(suggested_dir, exist_ok=True)
        dst_path = os.path.join(suggested_dir, file_name)
        
        shutil.copy2(src_path, dst_path)
        
        return {
            'success': True,
            'path': dst_path,
            'filename': file_name,
            'directory': suggested_dir,
            'message': f"✅ 文件已归档\n\n📁 {file_name}\n📍 位置：{dst_path}"
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def build_assistant_response(agent_id, message, file_url=None, file_name=None, requester_context=None, user_id=None):
    """生成助手回复并保存到数据库 - 所有回复都通过大模型处理"""
    
    # 所有消息（包括文件上传）都通过 OpenClaw 处理
    # 大模型会根据上下文理解用户意图，而不是机械回复
    response = send_to_openclaw(message, agent_id, file_url, file_name, requester_context=requester_context)

    # 若 OpenClaw 返回错误，保存错误消息并返回
    if isinstance(response, dict) and 'error' in response:
        error_msg = f"错误：{response['error']}"
        err_msg_id = save_message(agent_id, 'assistant', error_msg, 'error', user_id=user_id)
        return {
            "status": "error",
            "assistant_message": get_message_by_id(err_msg_id),
            "error": response['error']
        }

    # 检测 OpenClaw 是否指示需要创建 pending 请求
    pending_info = None
    try:
        if isinstance(response, dict) and isinstance(response.get('pending_request'), dict):
            pending_info = response.get('pending_request')
        else:
            # 支持在文本中嵌入 JSON 标记的兼容方式： [PENDING_JSON]{...}
            raw_msg = (response.get('message') if isinstance(response, dict) else str(response)) or ''
            m = re.search(r'\[PENDING_JSON\](\{.*\})', raw_msg, re.S)
            if m:
                try:
                    pending_info = json.loads(m.group(1))
                except Exception:
                    pending_info = None
    except Exception:
        pending_info = None

    # 若检测到 pending 请求，由后端自动创建 pending 并保存为公开聊天消息，随后通知线上审批者
    if pending_info:
        try:
            req_type = pending_info.get('request_type', 'unknown')
            title = pending_info.get('title') or pending_info.get('summary') or '待审批请求'
            content = pending_info.get('content') or pending_info.get('detail') or (response.get('message') if isinstance(response, dict) else str(response))
            submitter = (requester_context or {}).get('username') if requester_context else (session.get('username') or 'anonymous')
            new_req_id = add_pending_request(req_type, title, content, submitter)

            pending_message = f"[PENDING:{new_req_id}] {title}\n{content}"
            # 公开保存，便于审批者在聊天中看到（user_id NULL）
            pending_msg_id = save_message(agent_id, 'system', pending_message, msg_type='pending_request', user_id=None)
            pending_msg = get_message_by_id(pending_msg_id)

            # 广播到 agent 房间（所有在线用户）
            try:
                socketio.emit('new_message', {**(pending_msg or {}), 'agent_id': agent_id}, room=f'agent_{agent_id}')
            except Exception:
                pass

            # 主动推送到所有部门主管/ops 的用户房间（在线者会立即收到）
            try:
                conn = get_conn()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM users WHERE role IN ('dept_head','ops') AND is_active = 1")
                rows = cursor.fetchall()
                conn.close()
                for r in rows:
                    try:
                        uid = r['id'] if isinstance(r, dict) else r[0]
                        socketio.emit('new_message', {**(pending_msg or {}), 'agent_id': agent_id}, room=f'agent_{agent_id}_user_{uid}')
                    except Exception:
                        continue
            except Exception:
                pass
        except Exception:
            # 创建 pending 时不阻断主流程
            pass

    # 正常保存助手回复（若没有 pending 信息，这仍会保存模型的回复）
    reply = (response.get('message') if isinstance(response, dict) else str(response)) or '...'
    assistant_msg_id = save_message(agent_id, 'assistant', reply, user_id=user_id)
    return {
        "status": "success",
        "assistant_message": get_message_by_id(assistant_msg_id)
    }

def process_message(agent_id, message, file_url=None, file_name=None, requester_context=None, user_id=None):
    """处理一条用户消息并返回助手回复"""
    user_msg_id = save_message(agent_id, 'user', message, file_url=file_url, file_name=file_name, user_id=user_id)
    user_msg = get_message_by_id(user_msg_id)
    result = build_assistant_response(agent_id, message, file_url, file_name, requester_context=requester_context, user_id=user_id)
    result["user_message"] = user_msg
    return result

def login_required(f):
    """登录限制装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({'error': '未登录', 'authenticated': False}), 401
            from flask import redirect, url_for
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
def index():
    """主程序入口"""
    return render_template('index.html')

@app.route('/login')
def login_page():
    """登录页面"""
    if 'user_id' in session:
        from flask import redirect, url_for
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/auth/register')
def register_page():
    """注册页面"""
    if not ALLOW_SELF_REGISTER:
        from flask import redirect, url_for
        return redirect(url_for('login_page'))
    return render_template('register.html')

@app.route('/auth/register', methods=['POST'])
def auth_register():
    """用户自助注册"""
    if not ALLOW_SELF_REGISTER:
        return jsonify({'error': '注册通道已关闭'}), 403

    data = request.json
    username = (data.get('username') or '').strip()
    password = data.get('password')
    name = (data.get('name') or '').strip()
    role = data.get('role', 'user')

    if role not in ('user', 'dept_head'):
        return jsonify({'error': '无效的角色选择'}), 400
    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    user, msg = create_user_account(username, password, role=role, name=name if name else None)
    if not user:
        return jsonify({'error': msg}), 409

    write_audit('register', f"new user={username}; name={name}; role={role}")
    return jsonify({
        'status': 'success',
        'user': {'id': user['id'], 'username': user['username'], 'name': user['name'], 'role': user['role']}
    })

@app.route('/auth/login', methods=['POST'])
def auth_login():
    """传统账密登录"""
    data = request.json
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': '请输入用户名和密码'}), 400

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ? AND is_active = 1', (username,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user['password_hash'], password):
        session.clear()
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        write_audit('login', f'User {username} logged in via password')
        return jsonify({
            'status': 'success',
            'user': {'id': user['id'], 'username': user['username'], 'role': user['role']}
        })

    return jsonify({'error': '用户名或密码错误'}), 401

@app.route('/auth/quick-login', methods=['POST'])
@app.route('/api/auth/quick-login', methods=['POST'])
def quick_login():
    """免密快速登录（体验/测试用）"""
    if not QUICK_LOGIN_ENABLED:
        return jsonify({'error': '快速登录已禁用'}), 403

    data = request.json
    role = data.get('role')
    
    user = get_first_active_user_by_role(role)
    if not user:
        return jsonify({'error': f'未找到角色为 {role} 的可用账号'}), 404

    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    
    write_audit('quick_login', f'User {user["username"]} logged in via quick-login ({role})')
    
    return jsonify({
        'status': 'success', 
        'user': {'id': user['id'], 'username': user['username'], 'role': user['role']}
    })

@app.route('/auth/logout', methods=['POST'])
@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """注销登录"""
    username = session.get('username')
    write_audit('logout', f'User {username} logged out')
    session.clear()
    return jsonify({'status': 'success'})

@app.route('/auth/me')
@app.route('/api/auth/me')
def auth_me():
    """获取当前登录用户信息"""
    user = get_current_user()
    if not user:
        return jsonify({'authenticated': False}), 401
    
    return jsonify({
        'authenticated': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'name': user['name'],
            'role': user['role']
        }
    })

# ========== 核心 API 路由 ==========

@app.route('/agents', methods=['GET'])
@app.route('/api/agents', methods=['GET'])
@login_required
def api_get_agents():
    """获取所有可用 Agent（按登录用户隔离消息预览）"""
    user_id = session.get('user_id')
    return jsonify(get_all_agents_with_last_message(user_id=user_id))

@app.route('/messages', methods=['GET'])
@app.route('/api/messages', methods=['GET'])
@login_required
def api_get_messages():
    """获取历史消息"""
    agent_id = request.args.get('agent', 'main')
    limit = int(request.args.get('limit', 10))
    user_id = session.get('user_id')
    
    # 历史分页支持：获取比 before_id 更旧的消息
    before_id = request.args.get('before_id')
    
    conn = get_conn()
    cursor = conn.cursor()
    
    query = '''
        SELECT * FROM messages 
        WHERE agent_id = ? AND (
            user_id = ? OR 
            (user_id IS NULL AND (type = 'pending_request' OR sender = 'system'))
        )
    '''
    params = [agent_id, user_id]
    
    if before_id:
        query += ' AND id < ?'
        params.append(before_id)
        
    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    
    cursor.execute(query, tuple(params))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # 返回给前端时按时间正序
    return jsonify(rows[::-1])

@app.route('/send', methods=['POST'])
@app.route('/api/send', methods=['POST'])
@login_required
def api_send_message():
    """发送消息接口 (兼容旧 API)"""
    data = request.json
    agent_id = data.get('agent', 'main')
    message = data.get('message', '').strip()
    file_url = data.get('file_url')
    file_name = data.get('file_name')
    user_id = session.get('user_id')
    
    if not message and not file_url:
        return jsonify({"error": "消息不能为空"}), 400

    # 这里可以复用下方的 process_message 逻辑或保持现有逻辑
    # 为了简化，假设 process_message 已经存在于下方
    # 注意：此处 role 需要从 session 获取
    user_context = {
        'id': user_id,
        'username': session.get('username'),
        'role': session.get('role')
    }
    
    # ... 原有发送逻辑 ...
    # 为了演示，直接返回 process_message 结果
    result = process_message(agent_id, message, file_url=file_url, file_name=file_name, user_id=user_id, requester_context=user_context)
    return jsonify(result)

@app.route('/api/status', methods=['GET'])
@login_required
def get_status():
    """获取系统状态"""
    current_user = get_current_user()
    msgs = get_messages('main', limit=MAX_QUERY_ROWS, user_id=current_user['id'])

    return jsonify({
        "status": "running",
        "openclaw": "connected" if check_openclaw_status() else "disconnected",
        "messages_count": len(msgs),
        "agents": list(AGENTS.keys())
    })

@app.route('/upload', methods=['POST'])
@app.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    """处理文件上传"""
    if 'file' not in request.files:
        return jsonify({"error": "没有文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "文件名为空"}), 400
    
    result = save_uploaded_file(file)
    if result:
        return jsonify({
            "status": "success",
            "filename": result['filename'],
            "url": result['url'],
            "filepath": result['filepath']
        })
    else:
        return jsonify({"error": "不支持的文件类型"}), 400

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """提供文件下载"""
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/temp/<filename>')
def temp_file(filename):
    """提供临时文件下载"""
    return send_from_directory(TEMP_FOLDER, filename)

@app.route('/api/auth/register', methods=['POST'])
def auth_register_api():
    """用户注册（API）"""
    if not ALLOW_SELF_REGISTER:
        return jsonify({'error': '当前环境不允许自助注册'}), 403

    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    if len(username) < 3 or len(username) > 32:
        return jsonify({'error': '用户名长度需在 3-32 之间'}), 400
    if not re.fullmatch(r'[A-Za-z0-9_\-\.]+', username):
        return jsonify({'error': '用户名仅支持字母、数字、下划线、中划线和点'}), 400

    user, msg = create_user_account(username, password, role='user', is_active=1)
    if not user:
        return jsonify({'error': msg}), 409

    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    write_audit('register', f"user={username}")
    return jsonify({'status': 'success', 'user': build_user_payload(user)}), 201

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout_api():
    """登出"""
    write_audit('logout', f"user={session.get('username', '')}")
    session.clear()
    return jsonify({'status': 'success'})

@app.route('/api/auth/me', methods=['GET'])
def auth_me_api():
    """当前登录用户"""
    user = get_current_user()
    if not user:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'user': build_user_payload(user)})

@app.route('/api/users', methods=['GET'])
@role_required('ops')
def list_users():
    """运维查看用户列表"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, name, role, is_active, created_at FROM users ORDER BY id ASC')
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/users', methods=['POST'])
@role_required('ops')
def create_user_by_ops():
    """运维创建用户"""
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()
    role = (data.get('role') or 'user').strip()

    if role not in ('user', 'dept_head', 'ops'):
        return jsonify({'error': '角色非法'}), 400
    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    user, msg = create_user_account(username, password, role=role, name=name if name else None, is_active=1)
    if not user:
        return jsonify({'error': msg}), 409

    write_audit('user_create', json.dumps({'id': user['id'], 'username': user['username'], 'name': user['name'], 'role': user['role']}, ensure_ascii=False))
    return jsonify({'status': 'success', 'user': build_user_payload(user)}), 201

@app.route('/api/users/<int:user_id>/status', methods=['PATCH'])
@role_required('ops')
def update_user_status(user_id):
    """运维启停用户"""
    data = request.json or {}
    is_active = data.get('is_active')
    if is_active not in (0, 1, True, False):
        return jsonify({'error': 'is_active 必须是布尔值'}), 400

    current_user = get_current_user()
    if not is_active and current_user and current_user['id'] == user_id:
        return jsonify({'error': '无法禁用当前登录的运维账号'}), 403
    
    # 禁止禁用默认用户（ID 1-4）
    if not is_active and user_id in [1, 2, 3, 4]:
        return jsonify({'error': '无法禁用默认用户'}), 403

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_active = ? WHERE id = ?', (1 if is_active else 0, user_id))
    conn.commit()
    changed = cursor.rowcount
    conn.close()
    if changed == 0:
        return jsonify({'error': '用户不存在'}), 404

    write_audit('user_status_update', json.dumps({'id': user_id, 'is_active': 1 if is_active else 0}, ensure_ascii=False))
    return jsonify({'status': 'success'})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@role_required('ops')
def delete_user(user_id):
    """运维删除用户"""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': '未登录'}), 401
    
    # 规则：运维人员无法删除自己
    if current_user['id'] == user_id:
        return jsonify({'error': '无法删除当前登录账号'}), 403
    
    # 禁止删除默认用户（ID 1-4）
    if user_id in [1, 2, 3, 4]:
        return jsonify({'error': '无法删除默认用户'}), 403
    
    conn = get_conn()
    cursor = conn.cursor()
    
    # 获取目标用户角色
    cursor.execute('SELECT username, role FROM users WHERE id = ?', (user_id,))
    target_user = cursor.fetchone()
    
    if not target_user:
        conn.close()
        return jsonify({'error': '用户不存在'}), 404
        
    target_role = target_user['role']
    target_username = target_user['username']
    
    # 获取默认（种子）用户列表
    seed_usernames = [
        DEFAULT_ADMIN_USERNAME, 
        DEFAULT_USER_USERNAME, 
        'dept_head', 
        'buzhouai'
    ]
    
    # 规则：禁止删除默认/种子用户（体验账号），避免误删系统基础账号
    if target_username in seed_usernames:
        conn.close()
        return jsonify({'error': '默认体验账号禁止删除'}), 403
        
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    write_audit('user_delete', json.dumps({'id': user_id, 'username': target_username, 'role': target_role}, ensure_ascii=False))
    return jsonify({'status': 'success'})

@app.route('/api/users/<int:user_id>', methods=['PATCH'])
@role_required('ops')
def update_user_info(user_id):
    """运维编辑用户信息"""
    data = request.json or {}
    name = (data.get('name') or '').strip()
    role = (data.get('role') or '').strip()

    if role and role not in ('user', 'dept_head', 'ops'):
        return jsonify({'error': '角色非法'}), 400

    conn = get_conn()
    cursor = conn.cursor()
    
    # 构建更新语句
    update_fields = []
    update_values = []
    if name is not None:
        update_fields.append('name = ?')
        update_values.append(name)
    if role:
        update_fields.append('role = ?')
        update_values.append(role)
    
    if not update_fields:
        conn.close()
        return jsonify({'error': '没有提供要更新的字段'}), 400
    
    update_values.append(user_id)
    cursor.execute(f'UPDATE users SET {", ".join(update_fields)} WHERE id = ?', update_values)
    conn.commit()
    changed = cursor.rowcount
    conn.close()
    
    if changed == 0:
        return jsonify({'error': '用户不存在'}), 404

    write_audit('user_info_update', json.dumps({'id': user_id, 'name': name, 'role': role}, ensure_ascii=False))
    return jsonify({'status': 'success'})

@app.route('/api/profile', methods=['PATCH'])
@login_required
def update_self_profile():
    """用户编辑自己的资料"""
    user_id = session.get('user_id')
    data = request.json or {}
    name = (data.get('name') or '').strip()
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    conn = get_conn()
    cursor = conn.cursor()

    # 获取当前用户信息
    cursor.execute('SELECT password_hash FROM users WHERE id = ?', (user_id,))
    user_row = cursor.fetchone()

    if not user_row:
        conn.close()
        return jsonify({'error': '用户不存在'}), 404

    updates = []
    params = []

    # 更新姓名
    if name:
        updates.append('name = ?')
        params.append(name)

    # 更新密码
    if new_password:
        if not current_password:
            conn.close()
            return jsonify({'error': '修改密码需要提供当前密码'}), 400

        if not check_password_hash(user_row['password_hash'], current_password):
            conn.close()
            return jsonify({'error': '当前密码不正确'}), 400

        if len(new_password) < 6:
            conn.close()
            return jsonify({'error': '新密码长度至少6位'}), 400

        updates.append('password_hash = ?')
        params.append(generate_password_hash(new_password))

    if not updates:
        conn.close()
        return jsonify({'error': '没有需要更新的内容'}), 400

    params.append(user_id)
    query = f'UPDATE users SET {", ".join(updates)} WHERE id = ?'

    cursor.execute(query, params)
    conn.commit()
    conn.close()

    audit_data = {'id': user_id}
    if name:
        audit_data['name'] = name
    if new_password:
        audit_data['password_changed'] = True

    write_audit('profile_update', json.dumps(audit_data, ensure_ascii=False))
    return jsonify({'status': 'success'})

@app.route('/api/data/sources', methods=['GET'])
@role_required('ops')
def list_data_sources():
    """列出可查询数据源"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, name, db_type, allowed_tables FROM data_sources WHERE is_active = 1 ORDER BY id ASC'
    )
    sources = [dict(row) for row in cursor.fetchall()]
    conn.close()

    for source in sources:
        source['allowed_tables'] = [t for t in source.get('allowed_tables', '').split(',') if t.strip()]

    return jsonify(sources)

@app.route('/api/data/query', methods=['POST'])
@role_required('ops')
def query_data_source():
    """执行只读查询"""
    payload = request.json or {}
    source_id = payload.get('source_id')
    sql = (payload.get('sql') or '').strip()

    if not source_id or not sql:
        return jsonify({'error': 'source_id 和 sql 都不能为空'}), 400

    source = get_source_by_id(source_id)
    if not source or source.get('is_active') != 1:
        return jsonify({'error': '数据源不存在或未启用'}), 404

    if source.get('db_type') != 'sqlite':
        return jsonify({'error': '当前仅支持 sqlite 数据源'}), 400

    if not is_readonly_sql(sql):
        return jsonify({'error': '仅允许 SELECT/WITH 只读查询'}), 400

    allowed = parse_allowed_tables(source.get('allowed_tables'))
    used_tables = extract_sql_tables(sql)
    if allowed and used_tables and not used_tables.issubset(allowed):
        return jsonify({
            'error': '查询包含未授权表',
            'unauthorized_tables': sorted(list(used_tables - allowed))
        }), 403

    try:
        result = query_sqlite_source(source, sql)
        write_audit('data_query', json.dumps({'source_id': source_id, 'row_count': result['row_count']}, ensure_ascii=False))
        return jsonify({'status': 'success', 'result': result})
    except Exception as exc:
        return jsonify({'error': f'查询失败: {str(exc)}'}), 500

# ========== 待审批请求 API ==========

@app.route('/api/pending', methods=['GET'])
@login_required
def list_pending_requests():
    """获取待审批请求列表（dept_head 可见自己提交的，ops 可见全部）"""
    current_user = get_current_user()
    role = current_user.get('role', '')
    status = request.args.get('status', 'pending')
    
    conn = get_conn()
    cursor = conn.cursor()
    
    # 设计要求：普通用户只能看到自己提交的待审批请求；
    # 部门主管/运维需要看到所有待审批项以便审批或运维介入
    if role in ('dept_head', 'ops'):
        cursor.execute('SELECT * FROM pending_requests WHERE status = ? ORDER BY created_at DESC', (status,))
    else:
        cursor.execute('SELECT * FROM pending_requests WHERE status = ? AND submitter = ? ORDER BY created_at DESC', (status, current_user['username']))
    
    requests = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(requests)

@app.route('/api/pending/lookup', methods=['GET'])
@role_required('dept_head', 'ops')
def lookup_pending_for_approval():
    """查找当前审批人名下最应该审批的待处理请求（用于自然语言审批匹配）"""
    current_user = get_current_user()
    conn = get_conn()
    cursor = conn.cursor()
    # 查找最新一条待审批、且不是自己提交的请求
    cursor.execute(
        'SELECT * FROM pending_requests WHERE status = ? AND submitter != ? ORDER BY created_at ASC LIMIT 1',
        ('pending', current_user['username'])
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return jsonify({'found': True, 'request': dict(row)})
    return jsonify({'found': False})

@app.route('/api/pending', methods=['POST'])
@login_required
def create_pending_request():
    """提交待审批请求"""
    current_user = get_current_user()
    data = request.json or {}
    
    request_type = data.get('type', 'unknown')
    title = data.get('title', '未命名请求')
    content = data.get('content', '')
    
    if not content:
        return jsonify({'error': '内容不能为空'}), 400
    
    req_id = add_pending_request(request_type, title, content, current_user['username'])
    write_audit('pending_create', json.dumps({'id': req_id, 'type': request_type}, ensure_ascii=False))

    # 以独立审批消息形式推送给部门主管（纯消息，非按钮）
    try:
        pending_message = f"📋 审批请求 [#{req_id}]\n\n申请人：{current_user['username']}\n类型：{request_type}\n内容：{title}\n{content}\n\n请回复「批准」或「拒绝」"
        save_message('main', 'system', pending_message, msg_type='pending_request', user_id=None)
    except Exception as e:
        print(f"⚠️ 保存待审批消息失败: {e}")

    return jsonify({'status': 'success', 'id': req_id, 'message': '请求已提交，等待审批'})

def execute_approved_request(req):
    """根据已批准的请求类型执行相应操作"""
    request_type = req.get('request_type', '')
    content = req.get('content', '')
    
    try:
        if request_type == 'db.write':
            # 解析内容，判断具体操作
            if '上海米哈游天命科技有限公司' in content and '专利监控' in content:
                # 添加企业到专利监控
                import sqlite3 as sqll
                conn = sqll.connect('/Users/buz/Documents/patent-api-collector/data/patent_monitor.db')
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT OR IGNORE INTO companies (name) VALUES (?)',
                    ('上海米哈游天命科技有限公司',)
                )
                conn.commit()
                conn.close()
                return {'success': True, 'action': 'add_company', 'company': '上海米哈游天命科技有限公司'}
            
            elif '广州贸易有限公司' in content and 'CRM' in content:
                # 从CRM删除客户
                import sqlite3 as sqll
                conn = sqll.connect('/Users/buz/Documents/CRM 系统/customers.db')
                cursor = conn.cursor()
                cursor.execute('DELETE FROM customers WHERE company = ?', ('广州贸易有限公司',))
                conn.commit()
                conn.close()
                return {'success': True, 'action': 'delete_customer', 'company': '广州贸易有限公司'}
            
            return {'success': False, 'error': '未知内容'}
        
        return {'success': False, 'error': '未知类型'}
    
    except Exception as e:
        return {'success': False, 'error': str(e)}

@app.route('/api/pending/<int:req_id>/approve', methods=['POST'])
@role_required('dept_head', 'ops')
def approve_request(req_id):
    """审批通过请求（部门主管批准后直接执行）"""
    current_user = get_current_user()
    req = get_pending_request_by_id(req_id)
    
    if not req:
        return jsonify({'error': '请求不存在'}), 404
    
    if req['status'] != 'pending':
        return jsonify({'error': '该请求已处理'}), 400
    
    # 执行实际操作
    exec_result = execute_approved_request(req)
    
    # 更新状态
    approve_pending_request(req_id, current_user['username'])
    write_audit('pending_approve', json.dumps({'id': req_id, 'type': req['request_type'], 'exec': exec_result}, ensure_ascii=False))
    
    return jsonify({'status': 'success', 'message': '已批准并执行', 'exec': exec_result})

@app.route('/api/pending/<int:req_id>/reject', methods=['POST'])
@role_required('dept_head', 'ops')
def reject_request(req_id):
    """审批拒绝请求"""
    current_user = get_current_user()
    req = get_pending_request_by_id(req_id)
    
    if not req:
        return jsonify({'error': '请求不存在'}), 404
    
    if req['status'] != 'pending':
        return jsonify({'error': '该请求已处理'}), 400
    
    success = reject_pending_request(req_id, current_user['username'])
    write_audit('pending_reject', json.dumps({'id': req_id, 'type': req['request_type']}, ensure_ascii=False))
    
    return jsonify({'status': 'success', 'message': '已拒绝该请求'})

@socketio.on('connect')
def handle_connect():
    """客户端连接时返回状态"""
    if not socket_logged_in():
        emit('message_error', {'error': '未登录，无法建立实时连接'})
        return False

    emit('status', {
        "status": "connected",
        "openclaw": "connected" if check_openclaw_status() else "disconnected"
    })

@socketio.on('join_room')
def handle_join_room(room):
    """处理加入聊天室事件"""
    if not socket_logged_in():
        emit('message_error', {'error': '未登录'})
        return

    room = (room or '').strip()
    # 提取基础 agent_id (例如从 agent_main 或 agent_main_user_2 中提取 main)
    parts = room.split('_')
    agent_id = parts[1] if len(parts) > 1 else 'main'
    
    from flask_socketio import join_room
    
    # 1. 加入请求的目标房间
    join_room(room)
    
    # 2. 自动加入该 Agent 下的个人私有房间（用于多端同步）
    try:
        user = get_current_user()
        if user:
            user_room = f"agent_{agent_id}_user_{user['id']}"
            if room != user_room:
                join_room(user_room)
                print(f"📡 已自动同步加入私有房间: {user_room}")
    except Exception as e:
        print(f"⚠️ 加入私有房间失败: {e}")
        
    print(f"🔌 用户加入房间：{room}")

@socketio.on('leave_room')
def handle_leave_room(room):
    """处理离开聊天室事件"""
    if not socket_logged_in():
        return

    from flask_socketio import leave_room
    leave_room(room)
    print(f"🔌 用户离开聊天室：{room}")

@socketio.on('switch_agent')
def handle_switch_agent(agent_id):
    """处理切换 agent 事件"""
    if not socket_logged_in():
        emit('message_error', {'error': '未登录'})
        return

    if agent_id in AGENTS:
        emit('agent_changed', {
            'agent_id': agent_id,
            'agent_name': AGENTS[agent_id]['name'],
            'agent_color': AGENTS[agent_id]['color']
        })

@socketio.on('send_message')
def handle_socket_message(data):
    """处理 WebSocket 消息并广播 - 所有回复都通过大模型"""
    if not socket_logged_in():
        emit('message_error', {'error': '未登录'})
        return

    data = data or {}
    agent_id = data.get('agent', 'main')
    message = data.get('message', '').strip()
    file_url = data.get('file_url')
    file_name = data.get('file_name')
    client_sid = request.sid
    current_user = get_current_user()
    
    if agent_id not in AGENTS:
        agent_id = 'main'
    
    if not message and not file_url:
        emit('message_error', {"error": "消息不能为空"})
        return

    if has_pending_request(current_user, agent_id):
        emit('message_ignored', {"message": "上一条请求仍在处理中，请等待回复后再发送"}, to=client_sid)
        return

    requester_context = build_openclaw_context(build_user_payload(current_user), agent_id)
    chat_session_id = build_openclaw_session_id(
        requester_context.get('user_id'),
        requester_context.get('username'),
        agent_id
    )
    write_audit(
        'chat_request',
        json.dumps(
            {
                'channel': 'websocket',
                'agent': agent_id,
                'username': requester_context.get('username'),
                'session_id': chat_session_id
            },
            ensure_ascii=False
        )
    )
    user_room = f"agent_{agent_id}_user_{current_user['id']}" if current_user else f"agent_{agent_id}"
    mark_pending_request(current_user, agent_id)
    emit('message_accepted', {
        'agent_id': agent_id,
        'status': 'queued',
        'message': '请求已提交，正在转发到 OpenClaw'
    }, to=client_sid)

    user_msg_id = save_message(agent_id, 'user', message if message else f'[文件] {file_name}', file_url=file_url, file_name=file_name, user_id=current_user['id'] if current_user else None)
    user_msg = get_message_by_id(user_msg_id)
    # 用户消息仅发送到当前用户私有房间，避免跨用户泄露
    emit('new_message', {**user_msg, 'agent_id': agent_id}, room=user_room)

    def emit_assistant_response():
        # 所有消息都通过大模型处理，包括文件上传
        try:
            result = build_assistant_response(
                agent_id,
                message,
                file_url,
                file_name,
                requester_context=requester_context,
                user_id=current_user['id'] if current_user else None
            )
            
            # 助手回复仅发给请求者私有房间
            socketio.emit('new_message', {**result["assistant_message"], 'agent_id': agent_id}, room=user_room)
            if result["status"] == "error":
                socketio.emit('message_error', {"error": result["error"]}, to=client_sid)
        finally:
            clear_pending_request(current_user, agent_id)

    socketio.start_background_task(emit_assistant_response)

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 ClawChat 后端服务启动中...")
    print("=" * 60)
    
    # 初始化数据库
    init_db()
    
    port = int(os.environ.get('CLAWCHAT_PORT', os.environ.get('PORT', '3000')))
    print(f"\n📍 访问地址：http://localhost:{port}")
    print("\n按 Ctrl+C 停止服务\n")

    # 提前检查端口占用，给出可操作提示，避免将占用误判为程序崩溃
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            print(f"❌ 端口 {port} 已被占用，服务未启动。")
            print("建议先检查占用进程：lsof -nP -iTCP:%d -sTCP:LISTEN" % port)
            print("如果已存在一个 ClawChat 实例，可直接访问上面的地址。")
            sys.exit(1)
    
    try:
        socketio.run(app, debug=False, port=port, host='0.0.0.0', allow_unsafe_werkzeug=True)
    except TypeError:
        # 兼容旧版 flask-socketio/werkzeug：不支持 allow_unsafe_werkzeug 参数
        socketio.run(app, debug=False, port=port, host='0.0.0.0')