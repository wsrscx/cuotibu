from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import sys
import threading
import time
import uuid
import webbrowser
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import event, or_
from sqlalchemy.engine import Engine
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from ai import (
    AIError,
    analyze_error_with_ai,
    check_answer_with_ai,
    generate_practice_question_with_ai,
    generate_questions_from_pdf_with_ai,
    generate_review_plan_with_ai,
    ocr_with_ai,
)
from database import db
from models import (
    Assignment,
    Note,
    Notebook,
    PracticeRecord,
    Question,
    StudentAnswer,
    StudentAssignment,
    Textbook,
    User,
)
from utils import (
    calculate_next_review_date,
    get_note_statistics,
    get_practice_statistics,
    get_todays_reviews,
    get_user_tags,
)
from utils import (
    update_review_schedule as save_review_schedule,
)

APP_NAME = "错题簿"
APP_SLUG = "cuotibu"
APP_VERSION = "2.0.0"
AUTO_NOTEBOOK_TITLE = "自动收录的错题"
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
QUESTION_TYPES = [
    ("single_choice", "单选题"),
    ("multiple_choice", "多选题"),
    ("true_false", "判断题"),
    ("fill_blank", "填空题"),
    ("short_answer", "简答题"),
]

BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
FROZEN = bool(getattr(sys, "frozen", False))


def _writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_data_dir() -> Path:
    configured = os.environ.get("CUOTIBU_DATA_DIR") or os.environ.get("APP_DATA_DIR")
    if configured:
        path = Path(configured).expanduser().resolve()
        if _writable_directory(path):
            return path

    candidates = []
    if FROZEN:
        candidates.append(Path(sys.executable).resolve().parent / "data")
    else:
        candidates.append(BASE_DIR / "data")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Cuotibu")

    for path in candidates:
        if _writable_directory(path):
            return path.resolve()

    raise RuntimeError("无法创建数据目录，请把程序放到可写目录后重试。")


DATA_DIR = _resolve_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "notebook.db"
SECRET_PATH = DATA_DIR / "secret.key"
LOG_PATH = DATA_DIR / "app.log"


def _load_secret_key() -> str:
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured
    try:
        if SECRET_PATH.exists():
            value = SECRET_PATH.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_hex(32)
        SECRET_PATH.write_text(value, encoding="utf-8")
        return value
    except OSError:
        return secrets.token_hex(32)


app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "templates"),
    static_folder=str(RESOURCE_DIR / "static"),
)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DATABASE_PATH.as_posix()}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    UPLOAD_FOLDER=str(UPLOAD_DIR),
    DATA_DIR=str(DATA_DIR),
    APP_VERSION=APP_VERSION,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
    except Exception as exc:
        # Non-SQLite engines do not understand these pragmas.
        logging.getLogger(__name__).debug("SQLite pragma skipped: %s", exc)


db.init_app(app)

with app.app_context():
    db.create_all()


def _setup_logging() -> None:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        file_handler = RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    if not FROZEN and not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)


_setup_logging()
app.logger.info("Starting %s %s; data dir: %s", APP_NAME, APP_VERSION, DATA_DIR)


# ---------------------------------------------------------------------------
# Request lifecycle and security
# ---------------------------------------------------------------------------


def wants_json() -> bool:
    return bool(
        request.is_json
        or request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best == "application/json"
    )


def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


@app.before_request
def load_user():
    g.user = None
    if request.endpoint in {"static", "uploaded_file", "health", "favicon"}:
        return
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if user is None or session.get("sv", 0) != (user.session_version or 0):
            session.clear()
        else:
            g.user = user


@app.before_request
def ensure_csrf_token():
    if request.endpoint in {"static", "uploaded_file", "health", "favicon"}:
        return
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)


@app.before_request
def protect_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request.endpoint in {"static", "uploaded_file", "health"}:
        return None
    expected = session.get("_csrf")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return None
    if wants_json():
        return jsonify({"error": "页面校验失败，请刷新后重试"}), 400
    flash("页面已过期，请刷新后重试。", "error")
    return redirect(request.referrer or url_for("index"))


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.endpoint not in {"static", "uploaded_file"}:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if wants_json():
                return jsonify({"error": "请先登录"}), 401
            flash("请先登录。", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role not in roles:
                if wants_json():
                    return jsonify({"error": "没有权限执行此操作"}), 403
                flash("没有权限访问该页面。", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


# ---------------------------------------------------------------------------
# Template helpers and formatting
# ---------------------------------------------------------------------------


@app.template_filter("dt")
def format_dt(value, fmt: str = "%Y-%m-%d %H:%M"):
    return value.strftime(fmt) if value else "—"


@app.template_filter("from_json")
def from_json_filter(value):
    try:
        data = json.loads(value) if isinstance(value, str) else value
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


@app.template_filter("image_url")
def image_url_filter(value: str | None):
    if not value:
        return ""
    if value.startswith(("http://", "https://", "/")):
        return value
    return url_for("uploaded_file", filename=value)


@app.template_filter("mask_key")
def mask_key_filter(value: str | None):
    if not value:
        return ""
    return f"••••••{value[-4:]}" if len(value) > 8 else "已设置"


@app.template_filter("num")
def num_filter(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return str(int(number)) if number.is_integer() else f"{number:g}"


@app.context_processor
def inject_globals():
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "current_year": datetime.now().year,
        "question_types": QUESTION_TYPES,
        "csrf_token": csrf_token,
    }


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def get_user_api_key(user: User | None) -> str | None:
    if user is None:
        return None
    if user.openrouter_api_key:
        return user.openrouter_api_key
    if user.is_student and user.teacher_id:
        teacher = db.session.get(User, user.teacher_id)
        if teacher and teacher.openrouter_api_key:
            return teacher.openrouter_api_key
    return None


def _validate_image(file_storage) -> tuple[str, bytes]:
    # Use the original name for the extension: secure_filename() can turn a
    # Chinese-only filename such as "错题.png" into "png" and lose the dot.
    extension = Path(file_storage.filename or "").suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("仅支持 PNG、JPG、GIF、BMP、WebP 图片")

    data = file_storage.read()
    if not data:
        raise ValueError("图片文件为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能超过 10 MB")

    signature = data[:16]
    valid = signature.startswith(
        (
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"GIF87a",
            b"GIF89a",
            b"BM",
        )
    ) or (signature.startswith(b"RIFF") and signature[8:12] == b"WEBP")
    if not valid:
        raise ValueError("文件内容不是有效的图片")
    return extension, data


def save_upload(file_storage) -> str:
    extension, data = _validate_image(file_storage)
    filename = f"{uuid.uuid4().hex}{extension}"
    (Path(app.config["UPLOAD_FOLDER"]) / filename).write_bytes(data)
    return filename


def delete_upload(filename: str | None) -> None:
    if not filename:
        return
    path = Path(app.config["UPLOAD_FOLDER"]) / Path(filename).name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        app.logger.warning("Could not remove upload %s", path)


def upload_path(filename: str | None) -> str | None:
    if not filename:
        return None
    if filename.startswith("/static/uploads/"):
        filename = filename.rsplit("/", 1)[-1]
    path = Path(app.config["UPLOAD_FOLDER"]) / Path(filename).name
    return str(path) if path.is_file() else None


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _get_notebook_or_404(notebook_id: int) -> Notebook:
    notebook = db.session.get(Notebook, notebook_id)
    if notebook is None:
        abort(404)
    return notebook


def _get_note_or_404(note_id: int) -> Note:
    note = db.session.get(Note, note_id)
    if note is None:
        abort(404)
    return note


def _ensure_notebook_owner(notebook: Notebook) -> None:
    if notebook.user_id != g.user.id:
        abort(403)


def _ensure_note_owner(note: Note) -> None:
    if note.notebook.user_id != g.user.id:
        abort(403)


def _get_teacher_submission(submission_id: int) -> StudentAssignment:
    submission = db.session.get(StudentAssignment, submission_id)
    if submission is None:
        abort(404)
    if submission.assignment.user_id != g.user.id:
        abort(403)
    return submission


def _get_assignment_for_teacher(assignment_id: int) -> Assignment:
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    if assignment.user_id != g.user.id:
        abort(403)
    return assignment


def _get_textbook_for_teacher(textbook_id: int) -> Textbook:
    textbook = db.session.get(Textbook, textbook_id)
    if textbook is None:
        abort(404)
    if textbook.user_id != g.user.id:
        abort(403)
    return textbook


def _parse_due_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Small in-memory brake for password guessing. It resets when the app restarts,
# which is acceptable for a single-user local desktop application.
_login_failures: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 6


def _login_key(identifier: str) -> str:
    address = request.remote_addr or "local"
    return f"{address}|{identifier.strip().lower()}"


def _login_is_blocked(key: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        attempts = _login_failures[key]
        while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= LOGIN_MAX_FAILURES


def _record_login_failure(key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        attempts = _login_failures[key]
        while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        attempts.append(now)


def _clear_login_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)


def _is_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError:
        return False
    for info in addresses:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def fetch_link_content(url: str) -> tuple[str, str]:
    """Fetch a public http(s) page without allowing SSRF or huge downloads."""
    current = url.strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    for _ in range(5):
        if not _is_public_url(current):
            raise ValueError("只支持公开的 http(s) 链接")
        try:
            response = requests.get(
                current,
                headers=headers,
                timeout=(5, 12),
                allow_redirects=False,
                stream=True,
                verify=True,
            )
        except requests.RequestException as exc:
            raise ValueError(f"链接访问失败：{exc.__class__.__name__}") from exc

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("链接重定向失败")
            current = urljoin(current, location)
            continue

        if not response.ok:
            response.close()
            raise ValueError(f"链接返回 HTTP {response.status_code}")

        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "text/plain" not in content_type:
            response.close()
            raise ValueError("链接不是网页内容")

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(8192):
                total += len(chunk)
                if total > 2 * 1024 * 1024:
                    raise ValueError("网页内容过大，无法分析")
                chunks.append(chunk)
        except requests.RequestException as exc:
            raise ValueError(f"读取网页失败：{exc.__class__.__name__}") from exc
        finally:
            response.close()

        encoding = response.encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")

        soup = BeautifulSoup(html, "html.parser")
        title = (
            soup.title.get_text(" ", strip=True)
            if soup.title
            else "未命名网页"
        )
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        node = None
        for selector in (
            "article",
            "main",
            ".article-content",
            ".post-content",
            ".content",
            "body",
        ):
            node = soup.select_one(selector)
            if node:
                break
        text = node.get_text(" ", strip=True) if node else ""
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("没有从链接中提取到文字")
        return title[:200], text[:6000]

    raise ValueError("链接重定向次数过多")


def add_wrong_answer_to_notebook(
    submission: StudentAssignment, answer: StudentAnswer
) -> None:
    notebook = Notebook.query.filter_by(
        user_id=submission.student_id, title=AUTO_NOTEBOOK_TITLE
    ).first()
    if notebook is None:
        notebook = Notebook(
            title=AUTO_NOTEBOOK_TITLE,
            description="批改后自动收录的错题",
            user_id=submission.student_id,
            share_link=secrets.token_urlsafe(24),
        )
        db.session.add(notebook)
        db.session.flush()

    question = answer.question
    options = "\n".join(question.options_list)
    pieces = [
        f"题目：{question.content}",
        f"选项：\n{options}" if options else "",
        f"正确答案：{question.correct_answer}",
        f"我的答案：{answer.answer_content or '（未作答）'}",
        f"解析：{question.explanation}" if question.explanation else "",
    ]
    text = "\n".join(piece for piece in pieces if piece)
    digest = content_hash(text)
    existing = Note.query.filter_by(
        notebook_id=notebook.id, content_hash=digest
    ).first()
    if existing:
        return

    db.session.add(
        Note(
            title=f"{question.type_label} · {submission.assignment.title}",
            content=text,
            content_hash=digest,
            notebook_id=notebook.id,
            category=question.type_label,
            answer=question.correct_answer,
            error_reason="作业批改自动收录",
            next_review_date=calculate_next_review_date(3, 0),
            difficulty_level=3,
        )
    )


def _ai_error(exc: AIError):
    return jsonify({"error": str(exc)}), 502


# ---------------------------------------------------------------------------
# Public and auth pages
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    return jsonify({"app": APP_SLUG, "status": "ok", "version": APP_VERSION})


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(
        app.config["UPLOAD_FOLDER"], filename, max_age=3600, as_attachment=False
    )


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/dashboard")
@login_required
def dashboard():
    if g.user.is_teacher:
        return redirect(url_for("teacher_dashboard"))
    return redirect(url_for("student_dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        role = request.form.get("role") or "student"
        api_key = (request.form.get("api_key") or "").strip()

        errors = []
        if not re.fullmatch(r"[\w.\-]{3,30}", username, flags=re.UNICODE):
            errors.append("用户名需为 3 到 30 个字符，只能包含字母、数字、下划线、点或短横线。")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors.append("请输入有效的邮箱地址。")
        if len(password) < 8:
            errors.append("密码至少需要 8 个字符。")
        if password != confirm:
            errors.append("两次输入的密码不一致。")
        if role not in {"student", "teacher"}:
            errors.append("请选择有效的账户类型。")
        if role == "teacher" and not api_key:
            errors.append("教师账户需要填写 OpenRouter API 密钥。")
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            errors.append("用户名已被使用。")
        if User.query.filter(db.func.lower(User.email) == email).first():
            errors.append("邮箱已被注册。")

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("register.html", form=request.form), 400

        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role=role,
            openrouter_api_key=api_key or None,
        )
        db.session.add(user)
        db.session.commit()
        flash("账户已创建，请登录。", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form=None)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        key = _login_key(identifier)
        if _login_is_blocked(key):
            flash("尝试次数过多，请 15 分钟后再试。", "error")
            return render_template("login.html", form=request.form), 429

        user = User.query.filter(
            or_(
                db.func.lower(User.username) == identifier.lower(),
                db.func.lower(User.email) == identifier.lower(),
            )
        ).first()
        if user and check_password_hash(user.password, password):
            _clear_login_failures(key)
            session.clear()
            session["user_id"] = user.id
            session["sv"] = user.session_version or 0
            session["_csrf"] = secrets.token_urlsafe(32)
            session.permanent = True
            flash(f"欢迎回来，{user.username}。", "success")
            return redirect(url_for("dashboard"))
        _record_login_failure(key)
        flash("账户或密码不正确。", "error")
        return render_template("login.html", form=request.form), 401

    return render_template("login.html", form=None)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("已退出登录。", "info")
    return redirect(url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action", "api_key")
        if action == "password":
            current = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            if not check_password_hash(g.user.password, current):
                flash("当前密码不正确。", "error")
            elif len(new_password) < 8:
                flash("新密码至少需要 8 个字符。", "error")
            elif new_password != confirm:
                flash("两次输入的新密码不一致。", "error")
            else:
                g.user.password = generate_password_hash(new_password)
                g.user.session_version = (g.user.session_version or 0) + 1
                db.session.commit()
                # Keep this browser signed in; other sessions become invalid.
                session["sv"] = g.user.session_version
                flash("密码已更新，其他登录会话已失效。", "success")
        else:
            new_key = (request.form.get("openrouter_api_key") or "").strip()
            if request.form.get("clear_api_key"):
                g.user.openrouter_api_key = None
                db.session.commit()
                flash("API 密钥已清除。", "success")
            elif new_key:
                g.user.openrouter_api_key = new_key
                db.session.commit()
                flash("API 密钥已保存。", "success")
            else:
                flash("没有填写新的 API 密钥。", "info")
        return redirect(url_for("settings"))

    return render_template(
        "settings.html",
        user=g.user,
        has_key=bool(g.user.openrouter_api_key),
        masked_key=mask_key_filter(g.user.openrouter_api_key),
        data_dir=str(DATA_DIR),
    )


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


@app.route("/teacher_dashboard")
@role_required("teacher")
def teacher_dashboard():
    textbooks = (
        Textbook.query.filter_by(user_id=g.user.id)
        .order_by(Textbook.created_at.desc())
        .all()
    )
    assignments = (
        Assignment.query.filter_by(user_id=g.user.id)
        .order_by(Assignment.created_at.desc())
        .all()
    )
    students = (
        User.query.filter_by(teacher_id=g.user.id, role="student")
        .order_by(User.username)
        .all()
    )
    recent_submissions = (
        StudentAssignment.query.join(Assignment)
        .filter(Assignment.user_id == g.user.id)
        .order_by(StudentAssignment.id.desc())
        .limit(8)
        .all()
    )
    pending_count = StudentAssignment.query.join(Assignment).filter(
        Assignment.user_id == g.user.id,
        StudentAssignment.status == "submitted",
    ).count()
    return render_template(
        "teacher_dashboard.html",
        textbooks=textbooks,
        assignments=assignments,
        students=students,
        recent_submissions=recent_submissions,
        pending_count=pending_count,
        has_api_key=bool(get_user_api_key(g.user)),
    )


@app.route("/student_dashboard")
@role_required("student")
def student_dashboard():
    notebooks = (
        Notebook.query.filter_by(user_id=g.user.id)
        .order_by(Notebook.updated_at.desc())
        .all()
    )
    notebook_ids = [notebook.id for notebook in notebooks]
    notes = (
        Note.query.filter(Note.notebook_id.in_(notebook_ids)).all()
        if notebook_ids
        else []
    )
    assignments = (
        StudentAssignment.query.filter_by(student_id=g.user.id)
        .join(Assignment)
        .order_by(Assignment.due_date.is_(None), Assignment.due_date, Assignment.id.desc())
        .all()
    )
    pending = [item for item in assignments if item.can_submit]
    graded = [
        item
        for item in assignments
        if item.status == "graded" and item.score is not None
    ]
    average_score = (
        round(sum(item.score for item in graded) / len(graded), 1) if graded else 0
    )
    wrong_answers = (
        StudentAnswer.query.join(StudentAssignment)
        .filter(
            StudentAssignment.student_id == g.user.id,
            StudentAnswer.is_correct.is_(False),
        )
        .count()
    )
    category_counts = Counter(note.category or "未分类" for note in notes)
    score_history = sorted(
        [item for item in graded if item.submitted_at],
        key=lambda item: item.submitted_at,
    )[-8:]
    due_reviews = get_todays_reviews(g.user.id)
    stats = get_note_statistics(g.user.id)
    practice_stats = get_practice_statistics(g.user.id)

    return render_template(
        "student_dashboard.html",
        notebooks=notebooks,
        notes=notes,
        assignments=assignments,
        pending_assignments=pending,
        graded_assignments=graded,
        average_score=average_score,
        wrong_answers=wrong_answers,
        category_counts=category_counts.most_common(),
        score_history=score_history,
        due_reviews=due_reviews[:5],
        due_reviews_total=len(due_reviews),
        stats=stats,
        practice_stats=practice_stats,
        has_api_key=bool(get_user_api_key(g.user)),
    )


# ---------------------------------------------------------------------------
# Notebooks and notes
# ---------------------------------------------------------------------------


@app.post("/create_notebook")
@role_required("student")
def create_notebook():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not title:
        flash("请填写错题本名称。", "error")
        return redirect(url_for("student_dashboard"))
    notebook = Notebook(
        title=title[:120],
        description=description[:1000] or None,
        user_id=g.user.id,
        share_link=secrets.token_urlsafe(24),
    )
    db.session.add(notebook)
    db.session.commit()
    flash("错题本已创建。", "success")
    return redirect(url_for("view_notebook", notebook_id=notebook.id))


@app.post("/delete_notebook/<int:notebook_id>")
@role_required("student")
def delete_notebook(notebook_id: int):
    notebook = _get_notebook_or_404(notebook_id)
    _ensure_notebook_owner(notebook)
    for note in notebook.notes:
        delete_upload(note.image_path)
    db.session.delete(notebook)
    db.session.commit()
    if wants_json():
        return jsonify({"success": True})
    flash("错题本已删除。", "success")
    return redirect(url_for("student_dashboard"))


@app.route("/notebook/<int:notebook_id>")
@role_required("student")
def view_notebook(notebook_id: int):
    notebook = _get_notebook_or_404(notebook_id)
    _ensure_notebook_owner(notebook)
    query_text = (request.args.get("q") or "").strip()
    query = Note.query.filter_by(notebook_id=notebook.id)
    if query_text:
        pattern = f"%{query_text}%"
        query = query.filter(
            or_(
                Note.title.ilike(pattern),
                Note.content.ilike(pattern),
                Note.category.ilike(pattern),
                Note.tags.ilike(pattern),
            )
        )
    notes = query.order_by(Note.updated_at.desc()).all()
    return render_template(
        "notebook.html",
        notebook=notebook,
        notes=notes,
        query_text=query_text,
        share_url=url_for(
            "share_notebook", share_link=notebook.share_link, _external=True
        ),
        has_api_key=bool(get_user_api_key(g.user)),
    )


@app.post("/notebook/<int:notebook_id>/add_note")
@role_required("student")
def add_note(notebook_id: int):
    notebook = _get_notebook_or_404(notebook_id)
    _ensure_notebook_owner(notebook)

    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    image_path = None
    image = request.files.get("image")
    if image and image.filename:
        try:
            image_path = save_upload(image)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("view_notebook", notebook_id=notebook.id))

    api_key = get_user_api_key(g.user)
    if image_path and api_key:
        try:
            ocr_text = ocr_with_ai(api_key, upload_path(image_path))
            if ocr_text:
                content = (content + "\n\n" + ocr_text).strip()
        except AIError as exc:
            flash(f"图片已保存，但文字识别失败：{exc}", "warning")

    if not title and content:
        title = content.splitlines()[0][:40]
    if not title and image_path:
        title = f"图片错题 · {datetime.now().strftime('%m-%d')}"
    if not title:
        delete_upload(image_path)
        flash("请填写标题，或先添加内容。", "error")
        return redirect(url_for("view_notebook", notebook_id=notebook.id))
    if not content and not image_path:
        flash("请填写错题内容或上传图片。", "error")
        return redirect(url_for("view_notebook", notebook_id=notebook.id))
    if not content:
        content = "（图片错题，尚未补充文字）"

    note = Note(
        title=title[:160],
        content=content,
        content_hash=content_hash(content),
        image_path=image_path,
        notebook_id=notebook.id,
        next_review_date=calculate_next_review_date(1, 0),
    )
    db.session.add(note)
    db.session.commit()

    if api_key:
        try:
            result = analyze_error_with_ai(
                api_key, content, upload_path(image_path)
            )
            note.category = result["category"]
            note.improvement_suggestions = result["improvement_suggestions"]
            note.error_reason = result["error_reason"]
            note.answer = result["answer"]
            note.tags = result["tags"]
            note.next_review_date = calculate_next_review_date(
                note.difficulty_level, 0
            )
            db.session.commit()
            flash("错题已添加，AI 分析完成。", "success")
        except AIError as exc:
            app.logger.warning("AI analysis failed for note %s: %s", note.id, exc)
            flash(f"错题已保存，但 AI 分析失败：{exc}", "warning")
    else:
        flash("错题已保存。设置 API 密钥后可进行 AI 分析。", "success")

    return redirect(url_for("view_notebook", notebook_id=notebook.id))


@app.post("/notebook/<int:notebook_id>/edit_note/<int:note_id>")
@role_required("student")
def edit_note(notebook_id: int, note_id: int):
    notebook = _get_notebook_or_404(notebook_id)
    _ensure_notebook_owner(notebook)
    note = _get_note_or_404(note_id)
    if note.notebook_id != notebook.id:
        abort(404)

    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    if not title or not content:
        if wants_json():
            return jsonify({"error": "标题和内容不能为空"}), 400
        flash("标题和内容不能为空。", "error")
        return redirect(url_for("view_notebook", notebook_id=notebook.id))

    changed = content != note.content
    note.title = title[:160]
    note.content = content
    if changed:
        note.content_hash = content_hash(content)
        note.category = None
        note.improvement_suggestions = None
        note.error_reason = None
        note.answer = None
        note.tags = None

    image = request.files.get("image")
    if image and image.filename:
        try:
            new_image = save_upload(image)
        except ValueError as exc:
            if wants_json():
                return jsonify({"error": str(exc)}), 400
            flash(str(exc), "error")
            return redirect(url_for("view_notebook", notebook_id=notebook.id))
        delete_upload(note.image_path)
        note.image_path = new_image

    db.session.commit()
    if wants_json():
        return jsonify({"success": True})
    flash("错题已更新。", "success")
    return redirect(url_for("view_notebook", notebook_id=notebook.id))


@app.post("/notebook/<int:notebook_id>/delete_note/<int:note_id>")
@role_required("student")
def delete_note(notebook_id: int, note_id: int):
    notebook = _get_notebook_or_404(notebook_id)
    _ensure_notebook_owner(notebook)
    note = _get_note_or_404(note_id)
    if note.notebook_id != notebook.id:
        abort(404)
    delete_upload(note.image_path)
    db.session.delete(note)
    db.session.commit()
    if wants_json():
        return jsonify({"success": True})
    flash("错题已删除。", "success")
    return redirect(url_for("view_notebook", notebook_id=notebook.id))


@app.get("/note/<int:note_id>")
@role_required("student")
def view_note(note_id: int):
    note = _get_note_or_404(note_id)
    _ensure_note_owner(note)
    return jsonify(
        {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "image_url": image_url_filter(note.image_path),
            "category": note.category,
            "improvement_suggestions": note.improvement_suggestions,
            "error_reason": note.error_reason,
            "answer": note.answer,
            "tags": note.tags_list,
            "created_at": format_dt(note.created_at),
            "updated_at": format_dt(note.updated_at),
            "notebook_id": note.notebook_id,
        }
    )


@app.post("/analyze_note/<int:note_id>")
@role_required("student")
def analyze_note(note_id: int):
    note = _get_note_or_404(note_id)
    _ensure_note_owner(note)
    digest = content_hash(note.content)
    if note.content_hash == digest and note.category:
        return jsonify(
            {
                "category": note.category,
                "improvement_suggestions": note.improvement_suggestions,
                "error_reason": note.error_reason,
                "answer": note.answer,
                "tags": note.tags_list,
                "cached": True,
            }
        )

    api_key = get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "未设置 API 密钥", "redirect_to_settings": True}), 400
    try:
        result = analyze_error_with_ai(
            api_key, note.content, upload_path(note.image_path)
        )
    except AIError as exc:
        return _ai_error(exc)

    note.category = result["category"]
    note.improvement_suggestions = result["improvement_suggestions"]
    note.error_reason = result["error_reason"]
    note.answer = result["answer"]
    note.tags = result["tags"]
    note.content_hash = digest
    if note.next_review_date is None:
        note.next_review_date = calculate_next_review_date(
            note.difficulty_level, note.review_count or 0
        )
    db.session.commit()
    result["tags"] = note.tags_list
    return jsonify(result)


@app.post("/analyze_note_with_apikey")
@role_required("student")
def analyze_note_with_apikey():
    data = request.get_json(silent=True) or {}
    note_id = _as_int(data.get("note_id"))
    api_key = str(data.get("api_key") or "").strip()
    if not note_id or not api_key:
        return jsonify({"error": "缺少必要参数"}), 400
    note = _get_note_or_404(note_id)
    _ensure_note_owner(note)
    try:
        result = analyze_error_with_ai(
            api_key, note.content, upload_path(note.image_path)
        )
    except AIError as exc:
        return _ai_error(exc)

    note.category = result["category"]
    note.improvement_suggestions = result["improvement_suggestions"]
    note.error_reason = result["error_reason"]
    note.answer = result["answer"]
    note.tags = result["tags"]
    note.content_hash = content_hash(note.content)
    db.session.commit()
    result["tags"] = note.tags_list
    return jsonify(result)


@app.post("/notebook/<int:notebook_id>/rotate_share")
@role_required("student")
def rotate_share(notebook_id: int):
    notebook = _get_notebook_or_404(notebook_id)
    _ensure_notebook_owner(notebook)
    notebook.share_link = secrets.token_urlsafe(24)
    db.session.commit()
    return jsonify(
        {
            "success": True,
            "share_url": url_for(
                "share_notebook", share_link=notebook.share_link, _external=True
            ),
        }
    )


@app.route("/share/<share_link>")
def share_notebook(share_link: str):
    notebook = Notebook.query.filter_by(share_link=share_link).first()
    if notebook is None:
        abort(404)
    response = app.make_response(
        render_template("share.html", notebook=notebook, notes=notebook.notes)
    )
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.post("/analyze_error_link")
@role_required("student")
def analyze_error_link():
    data = request.get_json(silent=True) or {}
    link = (data.get("link") or "").strip()
    if not link:
        return jsonify({"error": "请填写链接"}), 400
    api_key = get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "未设置 API 密钥", "redirect_to_settings": True}), 400
    try:
        title, text = fetch_link_content(link)
        result = analyze_error_with_ai(api_key, text)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except AIError as exc:
        return _ai_error(exc)
    return jsonify(
        {
            "title": title,
            "content": text,
            "category": result["category"],
            "improvement_suggestions": result["improvement_suggestions"],
            "error_reason": result["error_reason"],
            "answer": result["answer"],
            "tags": result["tags"],
        }
    )


# ---------------------------------------------------------------------------
# Practice, review and dashboard data
# ---------------------------------------------------------------------------


@app.post("/generate_practice_question")
@role_required("student")
def generate_practice_question():
    api_key = get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "未设置 API 密钥", "redirect_to_settings": True}), 400

    data = request.get_json(silent=True) or {}
    note_id = _as_int(data.get("note_id"))
    if data.get("note_id") is not None and note_id is None:
        return jsonify({"error": "错题 ID 无效"}), 400
    if note_id:
        note = _get_note_or_404(note_id)
        _ensure_note_owner(note)
    else:
        notes = get_user_notes_for_practice(g.user.id)
        if not notes:
            return jsonify({"error": "还没有错题，先添加一道吧"}), 400
        note = secrets.choice(notes)

    try:
        question = generate_practice_question_with_ai(api_key, note)
    except AIError as exc:
        return _ai_error(exc)
    question["note_id"] = note.id
    question["note_title"] = note.title
    return jsonify(question)


def get_user_notes_for_practice(user_id: int) -> list[Note]:
    from utils import get_user_notes

    return get_user_notes(user_id)


@app.post("/check_answer")
@role_required("student")
def check_answer():
    data = request.get_json(silent=True) or {}
    question_data = data.get("question_data")
    user_answer = data.get("user_answer")
    if not isinstance(question_data, dict) or user_answer is None:
        return jsonify({"error": "缺少必要参数"}), 400

    note_id = _as_int(question_data.get("note_id"))
    if not note_id:
        return jsonify({"error": "练习题缺少来源错题"}), 400
    note = _get_note_or_404(note_id)
    _ensure_note_owner(note)

    api_key = get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "未设置 API 密钥", "redirect_to_settings": True}), 400
    try:
        result = check_answer_with_ai(api_key, question_data, str(user_answer))
    except AIError as exc:
        return _ai_error(exc)

    db.session.add(
        PracticeRecord(
            user_id=g.user.id,
            note_id=note.id,
            is_correct=bool(result.get("is_correct")),
        )
    )
    db.session.commit()
    return jsonify(result)


@app.post("/update_review_schedule")
@role_required("student")
def update_review_schedule():
    data = request.get_json(silent=True) or {}
    note_id = _as_int(data.get("note_id"))
    if not note_id:
        return jsonify({"error": "缺少错题 ID"}), 400
    note = _get_note_or_404(note_id)
    _ensure_note_owner(note)
    difficulty = data.get("difficulty_level")
    if difficulty is not None:
        try:
            difficulty = int(difficulty)
        except (TypeError, ValueError):
            return jsonify({"error": "难度必须是 1 到 5"}), 400
        if not 1 <= difficulty <= 5:
            return jsonify({"error": "难度必须是 1 到 5"}), 400
    note = save_review_schedule(note.id, difficulty)
    return jsonify(
        {
            "success": True,
            "next_review_date": note.next_review_date.isoformat()
            if note.next_review_date
            else None,
            "review_count": note.review_count,
        }
    )


@app.get("/get_user_notebooks")
@role_required("student")
def get_user_notebooks():
    notebooks = Notebook.query.filter_by(user_id=g.user.id).all()
    return jsonify(
        {
            "notebooks": [
                {
                    "id": notebook.id,
                    "title": notebook.title,
                    "description": notebook.description,
                    "note_count": notebook.note_count,
                }
                for notebook in notebooks
            ]
        }
    )


@app.get("/get_dashboard_data")
@role_required("student")
def get_dashboard_data():
    stats = get_note_statistics(g.user.id)
    reviews = get_todays_reviews(g.user.id)
    stats["practice_stats"] = get_practice_statistics(g.user.id)
    return jsonify(
        {
            "stats": stats,
            "todays_reviews": [
                {
                    "id": note.id,
                    "title": note.title,
                    "category": note.category,
                    "next_review_date": note.next_review_date.isoformat()
                    if note.next_review_date
                    else None,
                }
                for note in reviews
            ],
            "tags": [
                {"id": tag.id, "name": tag.name, "color": tag.color}
                for tag in get_user_tags(g.user.id)
            ],
        }
    )


@app.post("/generate_review_plan")
@role_required("student")
def generate_review_plan():
    reviews = get_todays_reviews(g.user.id)
    if not reviews:
        return jsonify(
            {
                "review_order": [],
                "overall_strategy": "今天没有到期的错题，可以自由练习。",
                "estimated_time": 0,
            }
        )
    api_key = get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "未设置 API 密钥", "redirect_to_settings": True}), 400
    titles = {note.id: note.title for note in reviews}
    try:
        plan = generate_review_plan_with_ai(api_key, reviews)
        for item in plan.get("review_order", []):
            item["note_title"] = titles.get(item.get("note_id"), f"错题 #{item.get('note_id')}")
        plan["fallback"] = False
        return jsonify(plan)
    except AIError as exc:
        app.logger.warning("Review plan AI failed: %s", exc)
        return jsonify(
            {
                "review_order": [
                    {
                        "note_id": note.id,
                        "note_title": note.title,
                        "priority": "中",
                        "review_suggestion": "重做一遍并对照答案，写下出错原因。",
                    }
                    for note in reviews
                ],
                "overall_strategy": "按到期顺序逐题复习，先做后看解析。",
                "estimated_time": len(reviews) * 8,
                "fallback": True,
                "message": f"AI 暂时不可用，已给出本地计划：{exc}",
            }
        )


# ---------------------------------------------------------------------------
# Teacher: textbooks, assignments, students and grading
# ---------------------------------------------------------------------------


@app.post("/upload_textbook")
@role_required("teacher")
def upload_textbook():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    image = request.files.get("image_file")
    if not title:
        flash("请填写教材名称。", "error")
        return redirect(url_for("teacher_dashboard"))
    if not image or not image.filename:
        flash("请选择教材图片。", "error")
        return redirect(url_for("teacher_dashboard"))
    try:
        filename = save_upload(image)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("teacher_dashboard"))

    textbook = Textbook(
        title=title[:160],
        description=description[:1000] or None,
        file_path=filename,
        page_count=1,
        user_id=g.user.id,
    )
    db.session.add(textbook)
    db.session.commit()
    flash("教材已上传。", "success")
    return redirect(url_for("view_textbook", textbook_id=textbook.id))


@app.route("/view_textbook/<int:textbook_id>")
@role_required("teacher")
def view_textbook(textbook_id: int):
    textbook = _get_textbook_for_teacher(textbook_id)
    assignments = (
        Assignment.query.filter_by(textbook_id=textbook.id)
        .order_by(Assignment.created_at.desc())
        .all()
    )
    return render_template(
        "textbook.html", textbook=textbook, assignments=assignments
    )


@app.post("/create_assignment")
@role_required("teacher")
def create_assignment():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    textbook_id = request.form.get("textbook_id")
    due_date_raw = request.form.get("due_date")
    if not title or not textbook_id:
        flash("请填写作业名称并选择教材。", "error")
        return redirect(url_for("teacher_dashboard"))
    try:
        textbook = _get_textbook_for_teacher(int(textbook_id))
    except (TypeError, ValueError):
        flash("教材不存在。", "error")
        return redirect(url_for("teacher_dashboard"))

    due_date = _parse_due_date(due_date_raw)
    if due_date_raw and due_date is None:
        flash("截止日期格式不正确。", "error")
        return redirect(url_for("teacher_dashboard"))

    assignment = Assignment(
        title=title[:160],
        description=description[:2000] or None,
        textbook_id=textbook.id,
        user_id=g.user.id,
        due_date=due_date,
    )
    db.session.add(assignment)
    db.session.commit()
    flash("作业已创建，下一步可以生成题目。", "success")
    return redirect(url_for("view_assignment", assignment_id=assignment.id))


@app.route("/view_assignment/<int:assignment_id>")
@role_required("teacher")
def view_assignment(assignment_id: int):
    assignment = _get_assignment_for_teacher(assignment_id)
    students = (
        User.query.filter_by(teacher_id=g.user.id, role="student")
        .order_by(User.username)
        .all()
    )
    assigned_ids = {item.student_id for item in assignment.student_assignments}
    return render_template(
        "assignment.html",
        assignment=assignment,
        questions=assignment.questions,
        student_assignments=assignment.student_assignments,
        students=students,
        assigned_ids=assigned_ids,
        has_api_key=bool(get_user_api_key(g.user)),
    )


@app.post("/generate_questions")
@role_required("teacher")
def generate_questions():
    data = request.get_json(silent=True) or {}
    try:
        assignment = _get_assignment_for_teacher(int(data.get("assignment_id")))
    except (TypeError, ValueError):
        return jsonify({"error": "作业不存在"}), 404

    question_types = data.get("question_types") or []
    if not isinstance(question_types, list) or not question_types:
        return jsonify({"error": "请至少选择一种题型"}), 400
    valid_types = {code for code, _label in QUESTION_TYPES}
    question_types = [code for code in question_types if code in valid_types]
    if not question_types:
        return jsonify({"error": "题型无效"}), 400
    try:
        question_count = int(data.get("question_count", 1))
    except (TypeError, ValueError):
        question_count = 1
    question_count = min(max(question_count, 1), 15)

    page_number = data.get("page_number", 1)
    try:
        page_number = int(page_number)
    except (TypeError, ValueError):
        page_number = 1
    page_count = assignment.textbook.page_count or 1
    if page_number < 1 or page_number > page_count:
        return jsonify({"error": f"页码需要在 1 到 {page_count} 之间"}), 400

    api_key = (data.get("api_key") or "").strip() or get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "未设置 API 密钥", "redirect_to_settings": True}), 400

    image_path = upload_path(assignment.textbook.file_path)
    if not image_path:
        return jsonify({"error": "教材图片不存在，请重新上传"}), 400

    try:
        page_content = ocr_with_ai(api_key, image_path)
        generated = generate_questions_from_pdf_with_ai(
            api_key, page_content, question_types, question_count
        )
    except AIError as exc:
        return _ai_error(exc)

    created = []
    for item in generated:
        question = Question(
            question_type=item["question_type"],
            content=item["content"],
            options=json.dumps(item.get("options") or [], ensure_ascii=False),
            correct_answer=item["correct_answer"],
            explanation=item.get("explanation"),
            points=1,
            page_number=page_number,
            assignment_id=assignment.id,
        )
        db.session.add(question)
        created.append(question)
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "count": len(created),
            "questions": [
                {
                    "id": question.id,
                    "type_label": question.type_label,
                    "content": question.content,
                }
                for question in created
            ],
            "page_content": page_content[:500] + ("…" if len(page_content) > 500 else ""),
        }
    )


@app.post("/assign_to_students")
@role_required("teacher")
def assign_to_students():
    assignment_id = request.form.get("assignment_id")
    try:
        assignment = _get_assignment_for_teacher(int(assignment_id))
    except (TypeError, ValueError):
        flash("作业不存在。", "error")
        return redirect(url_for("teacher_dashboard"))

    raw_ids = request.form.getlist("student_ids")
    ids = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except ValueError:
            continue
    if not ids:
        flash("请选择至少一名学生。", "error")
        return redirect(url_for("view_assignment", assignment_id=assignment.id))

    students = User.query.filter(
        User.id.in_(ids),
        User.teacher_id == g.user.id,
        User.role == "student",
    ).all()
    if len(students) != len(set(ids)):
        flash("只能分配给你管理的学生。", "error")
        return redirect(url_for("view_assignment", assignment_id=assignment.id))

    existing_ids = {
        item.student_id for item in assignment.student_assignments
    }
    added = 0
    for student in students:
        if student.id not in existing_ids:
            db.session.add(
                StudentAssignment(
                    student_id=student.id,
                    assignment_id=assignment.id,
                    status="assigned",
                )
            )
            added += 1
    db.session.commit()
    flash(f"已分配给 {added} 名学生。" if added else "所选学生已分配过。", "success")
    return redirect(url_for("view_assignment", assignment_id=assignment.id))


@app.route("/manage_students")
@role_required("teacher")
def manage_students():
    students = (
        User.query.filter_by(teacher_id=g.user.id, role="student")
        .order_by(User.username)
        .all()
    )
    unassigned_students = (
        User.query.filter_by(role="student", teacher_id=None)
        .order_by(User.username)
        .all()
    )
    return render_template(
        "manage_students.html",
        students=students,
        unassigned_students=unassigned_students,
    )


@app.post("/add_student")
@role_required("teacher")
def add_student():
    student_id = request.form.get("student_id")
    username = (request.form.get("username") or "").strip()
    student = None
    if student_id:
        try:
            student = db.session.get(User, int(student_id))
        except (TypeError, ValueError):
            student = None
    elif username:
        student = User.query.filter(
            db.func.lower(User.username) == username.lower()
        ).first()

    if student is None:
        flash("没有找到该学生。", "error")
    elif student.role != "student" or student.teacher_id is not None:
        flash("该账户不是可添加的学生。", "error")
    else:
        student.teacher_id = g.user.id
        db.session.commit()
        flash(f"已将 {student.username} 添加到你的班级。", "success")
    return redirect(url_for("manage_students"))


@app.post("/remove_student/<int:student_id>")
@role_required("teacher")
def remove_student(student_id: int):
    student = db.session.get(User, student_id)
    if student is None or student.teacher_id != g.user.id:
        if wants_json():
            return jsonify({"error": "没有权限管理此学生"}), 403
        abort(403)
    student.teacher_id = None
    db.session.commit()
    if wants_json():
        return jsonify({"success": True})
    flash("已将该学生移出班级。", "success")
    return redirect(url_for("manage_students"))


@app.route("/grade_assignment/<int:submission_id>")
@role_required("teacher")
def grade_assignment(submission_id: int):
    submission = _get_teacher_submission(submission_id)
    answers_map = {answer.question_id: answer for answer in submission.answers}
    return render_template(
        "grade_assignment.html",
        submission=submission,
        questions=submission.assignment.questions,
        answers_map=answers_map,
    )


@app.post("/submit_grades")
@role_required("teacher")
def submit_grades():
    submission_id = request.form.get("submission_id")
    try:
        submission = _get_teacher_submission(int(submission_id))
    except (TypeError, ValueError):
        abort(404)

    total_score = 0.0
    total_points = 0.0
    answers_map = {answer.question_id: answer for answer in submission.answers}

    for question in submission.assignment.questions:
        answer = answers_map.get(question.id)
        if answer is None:
            answer = StudentAnswer(
                question_id=question.id,
                student_assignment_id=submission.id,
                answer_content="",
            )
            db.session.add(answer)
            answers_map[question.id] = answer

        score = _safe_float(request.form.get(f"score_{question.id}"), 0)
        score = min(max(score, 0), question.points or 0)
        answer.score = score
        total_score += score
        total_points += question.points or 0
        answer.feedback = (request.form.get(f"feedback_{question.id}") or "").strip() or None
        answer.is_correct = bool(request.form.get(f"is_correct_{question.id}"))
        if not answer.is_correct:
            add_wrong_answer_to_notebook(submission, answer)

    submission.score = round(total_score / total_points * 100, 1) if total_points else 0
    submission.status = "graded"
    submission.feedback = (request.form.get("feedback") or "").strip() or None
    db.session.commit()
    flash("批改结果已保存。", "success")
    return redirect(url_for("view_assignment", assignment_id=submission.assignment_id))


@app.post("/auto_grade_assignment/<int:submission_id>")
@role_required("teacher")
def auto_grade_assignment(submission_id: int):
    submission = _get_teacher_submission(submission_id)
    if submission.status != "submitted":
        return jsonify({"error": "只有已提交的作业可以自动判题"}), 400
    api_key = get_user_api_key(g.user)
    if not api_key:
        return jsonify({"error": "请先设置 API 密钥", "redirect_to_settings": True}), 400

    answers_map = {answer.question_id: answer for answer in submission.answers}
    total_score = 0.0
    total_points = 0.0
    graded_count = 0
    try:
        for question in submission.assignment.questions:
            answer = answers_map.get(question.id)
            if answer is None:
                answer = StudentAnswer(
                    question_id=question.id,
                    student_assignment_id=submission.id,
                    answer_content="",
                )
                db.session.add(answer)
                answers_map[question.id] = answer

            result = check_answer_with_ai(
                api_key,
                {
                    "question": question.content,
                    "question_type": question.question_type,
                    "options": question.options_list,
                    "correct_answer": question.correct_answer,
                    "explanation": question.explanation,
                },
                answer.answer_content or "",
            )
            answer.is_correct = bool(result.get("is_correct"))
            answer.score = question.points if answer.is_correct else 0
            answer.feedback = result.get("explanation") or None
            total_score += answer.score
            total_points += question.points or 0
            graded_count += 1
            if not answer.is_correct:
                add_wrong_answer_to_notebook(submission, answer)

        submission.score = (
            round(total_score / total_points * 100, 1) if total_points else 0
        )
        submission.status = "graded"
        submission.feedback = f"AI 自动批改完成，共 {graded_count} 道题目。"
        db.session.commit()
    except AIError as exc:
        db.session.rollback()
        return _ai_error(exc)

    return jsonify(
        {
            "success": True,
            "message": f"已批改 {graded_count} 道题目",
            "score": submission.score,
        }
    )


@app.route("/view_student_assignment/<int:assignment_id>")
@role_required("student")
def view_student_assignment(assignment_id: int):
    submission = StudentAssignment.query.filter_by(
        student_id=g.user.id, assignment_id=assignment_id
    ).first()
    if submission is None:
        abort(404)
    answers_map = {answer.question_id: answer for answer in submission.answers}
    return render_template(
        "student_assignment.html",
        assignment=submission.assignment,
        questions=submission.assignment.questions,
        student_assignment=submission,
        answers_map=answers_map,
    )


@app.post("/submit_assignment_answers")
@role_required("student")
def submit_assignment_answers():
    data = request.get_json(silent=True) or {}
    assignment_id = data.get("assignment_id")
    try:
        assignment_id = int(assignment_id)
    except (TypeError, ValueError):
        return jsonify({"error": "缺少作业 ID"}), 400

    submission = StudentAssignment.query.filter_by(
        student_id=g.user.id, assignment_id=assignment_id
    ).first()
    if submission is None:
        return jsonify({"error": "没有找到该作业"}), 404
    if not submission.can_submit:
        return jsonify({"error": "这份作业已经提交，不能重复提交"}), 400

    answers_payload = data.get("answers") or []
    if not isinstance(answers_payload, list):
        return jsonify({"error": "答案格式不正确"}), 400
    supplied: dict[int, str] = {}
    for item in answers_payload:
        if not isinstance(item, dict):
            return jsonify({"error": "答案格式不正确"}), 400
        question_id = _as_int(item.get("question_id"))
        if question_id is None:
            return jsonify({"error": "题目 ID 无效"}), 400
        supplied[question_id] = str(item.get("answer") or "")
    question_ids = {question.id for question in submission.assignment.questions}
    if not set(supplied).issubset(question_ids):
        return jsonify({"error": "答案中包含不属于该作业的题目"}), 400

    StudentAnswer.query.filter_by(student_assignment_id=submission.id).delete()
    for question in submission.assignment.questions:
        db.session.add(
            StudentAnswer(
                student_assignment_id=submission.id,
                question_id=question.id,
                answer_content=supplied.get(question.id, ""),
            )
        )
    submission.status = "submitted"
    submission.submitted_at = datetime.now()
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Error handling and entry point
# ---------------------------------------------------------------------------


@app.errorhandler(403)
def forbidden(_error):
    if wants_json():
        return jsonify({"error": "没有权限执行此操作"}), 403
    return render_template("403.html"), 403


@app.errorhandler(404)
def not_found(_error):
    if wants_json():
        return jsonify({"error": "没有找到该内容"}), 404
    return render_template("404.html"), 404


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    if wants_json():
        return jsonify({"error": "文件超过 25 MB 限制"}), 413
    flash("文件超过 25 MB 限制。", "error")
    return redirect(request.referrer or url_for("index"))


@app.errorhandler(500)
def server_error(error):
    app.logger.exception("Unhandled error: %s", error)
    if wants_json():
        return jsonify({"error": "服务器内部错误"}), 500
    return render_template("500.html"), 500


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _existing_instance(port: int) -> bool:
    try:
        response = requests.get(f"http://127.0.0.1:{port}/health", timeout=0.6)
        return response.ok and response.json().get("app") == APP_SLUG
    except (requests.RequestException, ValueError):
        return False


def _open_browser(url: str) -> None:
    if os.environ.get("CUOTIBU_NO_BROWSER", "").lower() in {"1", "true", "yes"}:
        return
    webbrowser.open(url)


def _open_browser_when_ready(port: int) -> None:
    for _ in range(80):
        try:
            response = requests.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
            if response.ok:
                _open_browser(f"http://127.0.0.1:{port}")
                return
        except requests.RequestException:
            time.sleep(0.15)
    _open_browser(f"http://127.0.0.1:{port}")


def _show_fatal_error(message: str) -> None:
    if FROZEN and os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            return
        except Exception:
            # There is no console in a windowed build; the log remains.
            return
    if not FROZEN:
        print(message, file=sys.stderr)


def run() -> None:
    debug = (
        not FROZEN
        and os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    )
    try:
        desired = int(os.environ.get("PORT", "88"))
    except ValueError:
        desired = 88
    port = None
    for candidate in range(desired, desired + 20):
        if _existing_instance(candidate):
            _open_browser(f"http://127.0.0.1:{candidate}")
            return
        if _port_available(candidate):
            port = candidate
            break
    if port is None:
        _show_fatal_error("没有可用的本地端口，请关闭占用 88-107 端口的程序后重试。")
        return

    app.logger.info("Serving on http://127.0.0.1:%s", port)
    if not debug:
        threading.Thread(
            target=_open_browser_when_ready, args=(port,), daemon=True
        ).start()
    app.run(
        host="127.0.0.1",
        port=port,
        debug=debug,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # pragma: no cover - last-resort desktop UX
        app.logger.exception("Fatal error")
        _show_fatal_error(f"程序启动失败：{exc}\n\n日志：{LOG_PATH}")
