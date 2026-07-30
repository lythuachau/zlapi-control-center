from __future__ import annotations

from collections.abc import Callable, Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiKey, AuditLog, User, utcnow
from .security import ROLE_SCOPES, Principal, is_expired, parse_api_key, verify_secret


def get_db(request: Request) -> Generator[Session, None, None]:
    yield from request.app.state.database.session()


def get_principal(
    request: Request,
    session: Session = Depends(get_db),
) -> Principal:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        raw_key = authorization[7:].strip()
        parsed = parse_api_key(raw_key)
        if parsed is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        prefix, complete = parsed
        api_key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
        if (
            api_key is None
            or api_key.revoked_at is not None
            or is_expired(api_key.expires_at)
            or not verify_secret(api_key.secret_hash, complete)
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        api_key.last_used_at = utcnow()
        session.commit()
        return Principal(
            kind="api_key",
            id=api_key.id,
            name=api_key.name,
            scopes=frozenset(api_key.scopes),
        )

    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user = session.get(User, user_id)
    if user is None or not user.active:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return Principal(
        kind="user",
        id=user.id,
        name=user.display_name,
        role=user.role,
        scopes=frozenset(ROLE_SCOPES.get(user.role, set())),
    )


def optional_user(request: Request, session: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = session.get(User, user_id)
    return user if user and user.active else None


def require_scope(scope: str) -> Callable:
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(scope):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing scope: {scope}")
        return principal

    return dependency


def require_short_lived_scope(scope: str) -> Callable:
    """Authenticate a streaming request without retaining a DB session for its lifetime."""

    def dependency(request: Request) -> Principal:
        with request.app.state.database.session_factory() as session:
            principal = get_principal(request, session)
            if not principal.can(scope):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing scope: {scope}",
                )
            return principal

    return dependency


def verify_csrf(request: Request, principal: Principal = Depends(get_principal)) -> None:
    if principal.kind == "api_key":
        return
    expected = request.session.get("csrf_token")
    provided = request.headers.get("x-csrf-token")
    if not expected or not provided or not secrets_compare(expected, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def secrets_compare(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(left.encode(), right.encode())


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def write_audit(
    session: Session,
    *,
    principal: Principal | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_type=principal.kind if principal else "system",
        actor_id=principal.id if principal else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=client_ip(request) if request else None,
    )
    session.add(entry)
    session.commit()
    return entry
