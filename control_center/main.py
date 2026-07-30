from __future__ import annotations

import hashlib
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import account_payload, job_payload, user_payload
from .api import router as api_router
from .auth import get_db, optional_user, require_scope, write_audit
from .catalog import catalog_payload
from .config import Settings, get_settings
from .db import Database
from .event_broker import EventBroker
from .media import verify_outbound_signature
from .models import (
    ApiKey,
    Archive,
    AuditLog,
    Event,
    GifLibraryItem,
    Job,
    MediaUpload,
    User,
    ZaloAccount,
    utcnow,
)
from .security import (
    ROLE_LABELS,
    Principal,
    SecretBox,
    generate_csrf_token,
    hash_secret,
    is_expired,
    safe_child_path,
    verify_secret,
)

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: https://*.zadn.vn https://*.zdn.vn https://*.dlfl.vn "
            "https://*.dlmd.me; media-src 'self' https://*.zadn.vn https://*.zdn.vn "
            "https://*.dlfl.vn https://*.dlmd.me; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        if request.app.state.settings.cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings
        self.buckets: dict[str, tuple[float, int]] = {}
        self.lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        is_login = request.url.path == "/login" and request.method == "POST"
        is_api = request.url.path.startswith("/api/")
        if (not is_api and not is_login) or request.url.path in {
            "/api/openapi.json",
            "/api/docs",
        }:
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        if is_login:
            host = request.client.host if request.client else "unknown"
            key = f"login:{host}"
            limit = self.settings.login_attempts_per_minute
        elif authorization.lower().startswith("bearer "):
            digest = hashlib.sha256(authorization[7:].encode()).hexdigest()[:24]
            key = f"key:{digest}"
            limit = self.settings.api_key_requests_per_minute
        else:
            host = request.client.host if request.client else "unknown"
            key = f"ip:{host}"
            limit = self.settings.session_requests_per_minute
        now = time.monotonic()
        with self.lock:
            started, count = self.buckets.get(key, (now, 0))
            if now - started >= 60:
                started, count = now, 0
            count += 1
            self.buckets[key] = (started, count)
            if len(self.buckets) > 10_000:
                self.buckets = {
                    bucket_key: value
                    for bucket_key, value in self.buckets.items()
                    if now - value[0] < 60
                }
        if count > limit:
            retry_after = max(1, int(60 - (now - started)))
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


def ensure_bootstrap_admin(database: Database, settings: Settings) -> None:
    if not settings.bootstrap_admin_password:
        return
    with database.session_factory() as session:
        if session.scalar(select(func.count(User.id))) == 0:
            session.add(
                User(
                    username=settings.bootstrap_admin_username.lower(),
                    display_name="System Administrator",
                    password_hash=hash_secret(settings.bootstrap_admin_password),
                    role="super_admin",
                )
            )
            session.commit()


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()
    database = database or Database(settings, pool_size=3, max_overflow=1)
    secret_box = SecretBox(settings)
    broker = EventBroker(database)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.auto_create_schema:
            database.create_schema()
        ensure_bootstrap_admin(database, settings)
        await broker.start()
        yield
        await broker.stop()
        database.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.secret_box = secret_box
    app.state.event_broker = broker

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="zlapi_session",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
    app.include_router(api_router)

    @app.api_route(
        "/media/outbound/{upload_id}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def outbound_media(
        upload_id: str,
        expires: int,
        signature: str,
        request: Request,
        session: Session = Depends(get_db),
    ) -> FileResponse:
        if not verify_outbound_signature(settings, upload_id, expires, signature):
            raise HTTPException(status_code=403, detail="Liên kết media không hợp lệ hoặc đã hết hạn")
        upload = session.get(MediaUpload, upload_id)
        if upload is None or is_expired(upload.expires_at):
            raise HTTPException(status_code=404, detail="Media không còn tồn tại")
        path = Path(upload.stored_path)
        if not safe_child_path(settings.upload_dir, path) or not path.is_file():
            raise HTTPException(status_code=404, detail="Media không còn tồn tại")
        response = FileResponse(
            path,
            media_type=upload.content_type or "application/octet-stream",
            filename=upload.original_name,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )
        if request.method == "HEAD":
            response.headers.setdefault("Accept-Ranges", "bytes")
        return response

    @app.api_route(
        "/media/gif-library/{gif_id}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def gif_library_media(
        gif_id: str,
        expires: int,
        signature: str,
        request: Request,
        session: Session = Depends(get_db),
    ) -> FileResponse:
        if not verify_outbound_signature(settings, gif_id, expires, signature):
            raise HTTPException(
                status_code=403,
                detail="Liên kết GIF không hợp lệ hoặc đã hết hạn",
            )
        item = session.get(GifLibraryItem, gif_id)
        if item is None:
            raise HTTPException(status_code=404, detail="GIF không còn tồn tại")
        path = Path(item.stored_path)
        if not safe_child_path(settings.gif_library_dir, path) or not path.is_file():
            raise HTTPException(status_code=404, detail="GIF không còn tồn tại")
        response = FileResponse(
            path,
            media_type=item.content_type or "image/gif",
            filename=item.original_name,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )
        if request.method == "HEAD":
            response.headers.setdefault("Accept-Ranges", "bytes")
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == 401:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive", "service": "zlapi-web"}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/openapi.json", include_in_schema=False)
    def openapi_schema(_: Principal = Depends(require_scope("read"))) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/docs", include_in_schema=False)
    def api_docs(
        request: Request,
        _: Principal = Depends(require_scope("read")),
    ):
        return templates.TemplateResponse(
            request=request,
            name="docs.html",
            context={"title": "OpenAPI Docs"},
        )

    @app.get("/health/ready")
    def ready(request: Request, session: Session = Depends(get_db)) -> dict:
        try:
            session.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
        return {
            "status": "ready",
            "database": session.bind.dialect.name,
            "production": request.app.state.settings.app_env == "production",
        }

    @app.get("/login")
    def login_page(request: Request, session: Session = Depends(get_db)):
        if optional_user(request, session):
            return RedirectResponse("/", status_code=303)
        request.session.setdefault("csrf_token", generate_csrf_token())
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": request.session["csrf_token"],
                "error": request.query_params.get("error"),
                "changed": request.query_params.get("changed"),
            },
        )

    @app.post("/login")
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
        session: Session = Depends(get_db),
    ):
        expected = request.session.get("csrf_token")
        if not expected or not secrets.compare_digest(expected, csrf_token):
            return RedirectResponse("/login?error=invalid-request", status_code=303)
        user = session.scalar(select(User).where(User.username == username.lower()))
        if user is None:
            return RedirectResponse("/login?error=invalid-credentials", status_code=303)
        if user.locked_until and not is_expired(user.locked_until):
            return RedirectResponse("/login?error=locked", status_code=303)
        if not user.active or not verify_secret(user.password_hash, password):
            user.failed_logins += 1
            if user.failed_logins >= 5:
                user.locked_until = utcnow() + timedelta(minutes=15)
                user.failed_logins = 0
            session.commit()
            write_audit(
                session,
                principal=None,
                action="auth.login_failed",
                resource_type="user",
                resource_id=user.id,
                details={"username": user.username},
                request=request,
            )
            return RedirectResponse("/login?error=invalid-credentials", status_code=303)
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = utcnow()
        session.commit()
        request.session.clear()
        request.session["user_id"] = user.id
        request.session["csrf_token"] = generate_csrf_token()
        write_audit(
            session,
            principal=Principal(
                kind="user",
                id=user.id,
                name=user.display_name,
                role=user.role,
                scopes=frozenset(),
            ),
            action="auth.login",
            resource_type="user",
            resource_id=user.id,
            request=request,
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(
        request: Request,
        csrf_token: str = Form(...),
        session: Session = Depends(get_db),
    ):
        expected = request.session.get("csrf_token")
        if expected and secrets.compare_digest(expected, csrf_token):
            user = optional_user(request, session)
            if user:
                write_audit(
                    session,
                    principal=Principal(
                        kind="user",
                        id=user.id,
                        name=user.display_name,
                        role=user.role,
                        scopes=frozenset(),
                    ),
                    action="auth.logout",
                    resource_type="user",
                    resource_id=user.id,
                    request=request,
                )
            request.session.clear()
        return RedirectResponse("/login", status_code=303)

    def page_context(request: Request, session: Session, active: str, title: str) -> dict:
        user = optional_user(request, session)
        if user is None:
            raise HTTPException(status_code=401)
        csrf_token = request.session.setdefault("csrf_token", generate_csrf_token())
        return {
            "request": request,
            "user": user,
            "role_label": ROLE_LABELS.get(user.role, user.role),
            "active": active,
            "title": title,
            "csrf_token": csrf_token,
        }

    @app.get("/")
    def dashboard(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "dashboard", "Tổng quan")
        account_counts = dict(
            session.execute(
                select(ZaloAccount.status, func.count()).group_by(ZaloAccount.status)
            ).all()
        )
        job_counts = dict(
            session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        )
        recent_jobs = session.scalars(select(Job).order_by(desc(Job.created_at)).limit(8)).all()
        recent_events = session.scalars(select(Event).order_by(desc(Event.id)).limit(8)).all()
        context.update(
            account_counts=account_counts,
            job_counts=job_counts,
            account_total=sum(account_counts.values()),
            online_total=account_counts.get("online", 0),
            queued_total=job_counts.get("queued", 0),
            failed_total=job_counts.get("failed", 0),
            recent_jobs=[job_payload(job) for job in recent_jobs],
            recent_events=recent_events,
        )
        return templates.TemplateResponse(request=request, name="dashboard.html", context=context)

    @app.get("/accounts")
    def accounts_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "accounts", "Tài khoản Zalo")
        accounts = session.scalars(select(ZaloAccount).order_by(ZaloAccount.name)).all()
        context["accounts"] = [account_payload(account) for account in accounts]
        return templates.TemplateResponse(request=request, name="accounts.html", context=context)

    @app.get("/chat")
    def chat_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "chat", "ZChat Desk")
        accounts = session.scalars(
            select(ZaloAccount)
            .where(ZaloAccount.enabled.is_(True))
            .order_by(ZaloAccount.name)
        ).all()
        context.update(
            accounts=[account_payload(account) for account in accounts],
            can_send=context["user"].role in {"operator", "admin", "super_admin"},
            can_manage_jobs=context["user"].role in {
                "operator",
                "admin",
                "super_admin",
            },
            can_manage_reactions=context["user"].role in {"admin", "super_admin"},
            can_manage_gif_library=context["user"].role in {"admin", "super_admin"},
            can_manage_groups=context["user"].role in {"admin", "super_admin"},
            max_upload_mb=settings.max_upload_mb,
        )
        return templates.TemplateResponse(
            request=request,
            name="chat.html",
            context=context,
        )

    @app.get("/profile")
    def profile_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "profile", "Tài khoản cá nhân")
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context=context,
        )

    @app.get("/explorer")
    def explorer_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "explorer", "API Explorer")
        context["catalog"] = catalog_payload()
        context["accounts"] = [
            account_payload(account)
            for account in session.scalars(
                select(ZaloAccount).where(ZaloAccount.enabled.is_(True)).order_by(ZaloAccount.name)
            ).all()
        ]
        return templates.TemplateResponse(request=request, name="explorer.html", context=context)

    @app.get("/jobs")
    def jobs_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "jobs", "Jobs & Events")
        jobs = session.scalars(select(Job).order_by(desc(Job.created_at)).limit(100)).all()
        events = session.scalars(select(Event).order_by(desc(Event.id)).limit(50)).all()
        context.update(jobs=[job_payload(job) for job in jobs], events=events)
        return templates.TemplateResponse(request=request, name="jobs.html", context=context)

    @app.get("/users")
    def users_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "users", "Người dùng & vai trò")
        if context["user"].role not in {"admin", "super_admin"}:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý người dùng")
        context["users"] = [
            user_payload(user) for user in session.scalars(select(User).order_by(User.username)).all()
        ]
        return templates.TemplateResponse(request=request, name="users.html", context=context)

    @app.get("/api-keys")
    def keys_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "api-keys", "API Keys")
        if context["user"].role not in {"admin", "super_admin"}:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý API key")
        context["keys"] = session.scalars(select(ApiKey).order_by(desc(ApiKey.created_at))).all()
        return templates.TemplateResponse(request=request, name="api_keys.html", context=context)

    @app.get("/audit")
    def audit_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "audit", "Audit Logs")
        context["entries"] = session.scalars(
            select(AuditLog).order_by(desc(AuditLog.id)).limit(200)
        ).all()
        return templates.TemplateResponse(request=request, name="audit.html", context=context)

    @app.get("/system")
    def system_page(request: Request, session: Session = Depends(get_db)):
        context = page_context(request, session, "system", "System Health")
        context.update(
            db_dialect=session.bind.dialect.name,
            event_cursor=int(session.scalar(select(func.max(Event.id))) or 0),
            archive_count=int(session.scalar(select(func.count(Archive.id))) or 0),
            services=["zlapi-web", "zlapi-worker", "zlapi-listener", "caddy"],
        )
        return templates.TemplateResponse(request=request, name="system.html", context=context)

    return app


app = create_app()
