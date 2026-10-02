from functools import wraps
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4
import warnings

from flask import (
    Flask,
    abort,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash


STATUS_LABELS = {
    "want_to_read": "読みたい",
    "reading": "読書中",
    "finished": "読了",
}

VIEWS = {
    "all": {
        "endpoint": "index",
        "label": "すべて",
        "title": "すべての本",
        "status": None,
    },
    "reading": {
        "endpoint": "reading_books",
        "label": "読書中",
        "title": "読んでいる本",
        "status": "reading",
    },
    "want_to_read": {
        "endpoint": "want_to_read_books",
        "label": "読みたい",
        "title": "読みたい本",
        "status": "want_to_read",
    },
    "finished": {
        "endpoint": "finished_books",
        "label": "読了",
        "title": "読み終えた本",
        "status": "finished",
    },
}

BOOKS_TABLE_SQL = """
CREATE TABLE books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    author TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'want_to_read'
        CHECK (status IN ('want_to_read', 'reading', 'finished')),
    cover TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

BOOKS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_books_status_updated_at
ON books (status, updated_at DESC)
"""

ADMINS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

app = Flask(__name__, instance_relative_config=True)
app.config.from_mapping(
    DATABASE=os.environ.get(
        "TSUNDOKU_DATABASE", str(Path(app.instance_path) / "books.db")
    ),
    SECRET_KEY=secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_NAME="tsundoku_session",
    SESSION_COOKIE_SECURE=os.environ.get("TSUNDOKU_COOKIE_SECURE") == "1",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)
Path(app.instance_path).mkdir(parents=True, exist_ok=True)
Path(app.static_folder, "covers").mkdir(parents=True, exist_ok=True)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    table = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'books'"
    ).fetchone()

    if table is None:
        db.execute(BOOKS_TABLE_SQL)
        db.execute(BOOKS_INDEX_SQL)
    else:
        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(books)").fetchall()
        }
        needs_migration = (
            "want_to_read" not in table["sql"] or "updated_at" not in columns
        )

        if needs_migration:
            updated_at_source = (
                "updated_at" if "updated_at" in columns else "created_at"
            )
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("ALTER TABLE books RENAME TO books_previous")
                db.execute(BOOKS_TABLE_SQL)
                db.execute(
                    f"""
                    INSERT INTO books (
                        id, title, author, status, cover, created_at, updated_at
                    )
                    SELECT
                        id,
                        title,
                        author,
                        CASE status
                            WHEN 'unread' THEN 'want_to_read'
                            ELSE status
                        END,
                        cover,
                        created_at,
                        {updated_at_source}
                    FROM books_previous
                    """
                )
                db.execute("DROP TABLE books_previous")
                db.execute(BOOKS_INDEX_SQL)
                db.commit()
            except Exception:
                db.rollback()
                raise
        else:
            db.execute(BOOKS_INDEX_SQL)

    db.execute(ADMINS_TABLE_SQL)
    db.execute(SETTINGS_TABLE_SQL)
    db.execute(
        """
        INSERT OR IGNORE INTO app_settings (key, value)
        VALUES ('session_secret', ?)
        """,
        (secrets.token_hex(32),),
    )
    db.execute("PRAGMA user_version = 3")
    session_secret = db.execute(
        "SELECT value FROM app_settings WHERE key = 'session_secret'"
    ).fetchone()["value"]
    db.commit()
    return session_secret


def get_books(status_filter=None):
    sql = """
        SELECT id, title, author, status, cover, created_at, updated_at
        FROM books
    """
    parameters = ()
    if status_filter:
        sql += " WHERE status = ?"
        parameters = (status_filter,)
    sql += """
        ORDER BY
            CASE status
                WHEN 'reading' THEN 0
                WHEN 'want_to_read' THEN 1
                ELSE 2
            END,
            updated_at DESC,
            id DESC
    """
    return get_db().execute(sql, parameters).fetchall()


def save_cover(upload):
    if upload is None or not upload.filename:
        return "", None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload.stream) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    return "", "表紙はJPEG、PNG、WebPのいずれかを選択してください。"

                image = ImageOps.exif_transpose(source)
                image.load()
                image.thumbnail((320, 480), Image.Resampling.LANCZOS)

                has_alpha = image.mode in {"RGBA", "LA"} or (
                    image.mode == "P" and "transparency" in image.info
                )
                image = image.convert("RGBA" if has_alpha else "RGB")
                filename = f"{uuid4().hex}.webp"
                destination = Path(app.static_folder) / "covers" / filename
                image.save(destination, "WEBP", quality=82, method=6)
                return filename, None
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        return "", "画像を読み込めませんでした。別の画像を選択してください。"


def remove_cover_if_unused(filename):
    if not filename or Path(filename).name != filename:
        return
    if get_db().execute(
        "SELECT 1 FROM books WHERE cover = ? LIMIT 1", (filename,)
    ).fetchone():
        return

    try:
        (Path(app.static_folder) / "covers" / filename).unlink(missing_ok=True)
    except OSError:
        app.logger.warning("Could not remove unused cover: %s", filename)


def cover_exists(filename):
    if not filename:
        return False
    cover_path = Path(app.static_folder) / "covers" / filename
    return cover_path.is_file()


def get_current_admin():
    if "current_admin" not in g:
        admin_id = session.get("admin_id")
        if admin_id is None:
            g.current_admin = None
        else:
            g.current_admin = get_db().execute(
                "SELECT id, username FROM admins WHERE id = ?", (admin_id,)
            ).fetchone()
    return g.current_admin


def is_logged_in():
    return get_current_admin() is not None


def admin_exists():
    return get_db().execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def protect_post_requests():
    if request.method != "POST":
        return None

    expected = session.get("csrf_token", "")
    received = request.form.get("csrf_token", "")
    if not expected or not received or not hmac.compare_digest(expected, received):
        abort(400)
    return None


app.jinja_env.globals["status_labels"] = STATUS_LABELS
app.jinja_env.globals["cover_exists"] = cover_exists
app.jinja_env.globals["csrf_token"] = csrf_token


def normalize_view(view_name):
    return view_name if view_name in VIEWS else "all"


def redirect_to_view(view_name):
    view = VIEWS[normalize_view(view_name)]
    return redirect(url_for(view["endpoint"]), code=303)


def render_books(view_name="all", errors=None, form=None, status_code=200):
    view_name = normalize_view(view_name)
    view = VIEWS[view_name]
    current_admin = get_current_admin()
    return (
        render_template(
            "index.html",
            books=get_books(view["status"]),
            errors=errors or [],
            form=form or {},
            logged_in=current_admin is not None,
            admin_user=current_admin["username"] if current_admin else "",
            registration_open=not admin_exists(),
            views=VIEWS,
            current_view=view_name,
            page_title=view["title"],
            default_status=view["status"] or "want_to_read",
        ),
        status_code,
    )


@app.errorhandler(413)
def upload_too_large(_error):
    if is_logged_in():
        return render_books(
            errors=["画像は8MB以下のファイルを選択してください。"],
            status_code=413,
        )
    return "画像は8MB以下のファイルを選択してください。", 413


@app.get("/")
def index():
    return render_books()


@app.get("/reading")
def reading_books():
    return render_books("reading")


@app.get("/want-to-read")
def want_to_read_books():
    return render_books("want_to_read")


@app.get("/finished")
def finished_books():
    return render_books("finished")


@app.route("/register", methods=["GET", "POST"])
def register():
    if admin_exists():
        destination = "index" if is_logged_in() else "login"
        return redirect(url_for(destination))

    form = {"user": request.form.get("user", "").strip()}
    errors = []
    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")

        if not form["user"]:
            errors.append("IDを入力してください。")
        elif len(form["user"]) > 100:
            errors.append("IDは100文字以内で入力してください。")
        if len(password) < 8:
            errors.append("パスワードは8文字以上で入力してください。")
        elif len(password) > 200:
            errors.append("パスワードは200文字以内で入力してください。")
        if password != password_confirmation:
            errors.append("確認用パスワードが一致しません。")

        if not errors:
            db = get_db()
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM admins LIMIT 1").fetchone():
                    db.rollback()
                    return redirect(url_for("login"), code=303)
                result = db.execute(
                    "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
                    (form["user"], generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                errors.append("そのIDは登録できません。")
            else:
                session.clear()
                session["admin_id"] = result.lastrowid
                csrf_token()
                return redirect(url_for("index"), code=303)

    return render_template("register.html", errors=errors, form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("index"))

    db = get_db()
    auth_configured = admin_exists()
    error = None
    if request.method == "POST":
        user = request.form.get("user", "").strip()
        password = request.form.get("password", "")
        admin = db.execute(
            """
            SELECT id, username, password_hash
            FROM admins
            WHERE username = ? COLLATE NOCASE
            """,
            (user,),
        ).fetchone()

        try:
            valid_password = admin is not None and check_password_hash(
                admin["password_hash"], password
            )
        except ValueError:
            valid_password = False

        if admin is not None and valid_password:
            session.clear()
            session["admin_id"] = admin["id"]
            csrf_token()
            return redirect(url_for("index"), code=303)
        error = "IDまたはパスワードが違います。"

    return render_template(
        "login.html",
        error=error,
        auth_configured=auth_configured,
    )


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("index"), code=303)


@app.get("/admin")
def admin():
    if not is_logged_in():
        return redirect(url_for("login"))
    return redirect(url_for("index"))


@app.post("/books")
@login_required
def create_book():
    view_name = normalize_view(request.form.get("view", "all"))
    form = {
        "title": request.form.get("title", "").strip(),
        "author": request.form.get("author", "").strip(),
        "status": request.form.get("status", "want_to_read"),
    }
    errors = []

    if not form["title"]:
        errors.append("タイトルを入力してください。")
    elif len(form["title"]) > 200:
        errors.append("タイトルは200文字以内で入力してください。")
    if len(form["author"]) > 200:
        errors.append("著者名は200文字以内で入力してください。")
    if form["status"] not in STATUS_LABELS:
        errors.append("正しい状態を選択してください。")

    cover_filename = ""
    cover_error = None
    if not errors:
        cover_filename, cover_error = save_cover(request.files.get("cover"))
    if cover_error:
        errors.append(cover_error)

    if errors:
        return render_books(
            view_name=view_name,
            errors=errors,
            form=form,
            status_code=400,
        )

    db = get_db()
    try:
        db.execute(
            "INSERT INTO books (title, author, status, cover) VALUES (?, ?, ?, ?)",
            (form["title"], form["author"], form["status"], cover_filename),
        )
        db.commit()
    except Exception:
        db.rollback()
        remove_cover_if_unused(cover_filename)
        raise
    return redirect_to_view(view_name)


@app.post("/books/<int:book_id>/status")
@login_required
def update_book_status(book_id):
    status = request.form.get("status", "")
    if status not in STATUS_LABELS:
        abort(400)

    db = get_db()
    result = db.execute(
        """
        UPDATE books
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, book_id),
    )
    db.commit()
    if result.rowcount == 0:
        abort(404)
    return redirect_to_view(request.form.get("view", "all"))


@app.post("/books/<int:book_id>/delete")
@login_required
def delete_book(book_id):
    db = get_db()
    book = db.execute("SELECT cover FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        abort(404)
    result = db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    db.commit()
    if result.rowcount:
        remove_cover_if_unused(book["cover"])
    return redirect_to_view(request.form.get("view", "all"))


with app.app_context():
    app.config["SECRET_KEY"] = init_db()
