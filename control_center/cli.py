from __future__ import annotations

import argparse
import getpass
import json

from sqlalchemy import func, select, text

from .config import get_settings
from .db import Database
from .models import User
from .security import ROLE_SCOPES, hash_secret
from .video import resolve_media_executable


def create_user(args, database: Database) -> None:
    password = args.password or getpass.getpass("Password (minimum 12 characters): ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    with database.session_factory() as session:
        if session.scalar(select(User).where(User.username == args.username.lower())):
            raise SystemExit("Username already exists")
        user = User(
            username=args.username.lower(),
            display_name=args.display_name,
            password_hash=hash_secret(password),
            role=args.role,
        )
        session.add(user)
        session.commit()
        print(f"Created {user.username} ({user.role})")


def doctor(database: Database, settings) -> None:
    checks = {
        "database": False,
        "schema": False,
        "ffmpeg": False,
        "ffprobe": False,
    }
    try:
        with database.session_factory() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = True
            session.scalar(select(func.count(User.id)))
            checks["schema"] = True
        resolve_media_executable(settings.ffmpeg_path, "ffmpeg")
        checks["ffmpeg"] = True
        resolve_media_executable(settings.ffprobe_path, "ffprobe")
        checks["ffprobe"] = True
    except Exception as exc:
        checks["error"] = str(exc)
    print(json.dumps(checks, indent=2))
    if not all(checks.get(name) for name in ("database", "schema", "ffmpeg", "ffprobe")):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="ZLAPI Control Center administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("display_name")
    create.add_argument("--role", choices=sorted(ROLE_SCOPES), default="viewer")
    create.add_argument("--password", help="Prefer interactive input; command-line values may be logged")
    subparsers.add_parser("doctor")
    subparsers.add_parser("create-schema", help="Development/test only")
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()
    database = Database(settings, pool_size=1, max_overflow=1)
    if args.command == "create-user":
        create_user(args, database)
    elif args.command == "doctor":
        doctor(database, settings)
    elif args.command == "create-schema":
        if settings.app_env == "production":
            raise SystemExit("Use Alembic migrations in production")
        database.create_schema()
        print("Schema created")


if __name__ == "__main__":
    main()
