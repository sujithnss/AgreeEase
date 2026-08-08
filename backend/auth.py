"""
Simple session-based auth for the staff dashboard.

Uses stdlib hashlib (PBKDF2) for password hashing so there's no extra
native-dependency (e.g. bcrypt) to install — fine for a small team's
tool. Sessions are signed cookies via Starlette's SessionMiddleware.
"""

import hashlib
import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from db import AdminUser

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest_hex = stored_hash.split("$")
    except ValueError:
        return False
    new_digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(new_digest.hex(), digest_hex)


def get_current_user(request: Request):
    """Returns the logged-in username, or None."""
    return request.session.get("user")


def require_login(request: Request):
    """
    Call this at the top of any dashboard route. Returns a RedirectResponse
    to /login if not authenticated, or None if the user is logged in —
    callers should `if redirect: return redirect`.
    """
    if not get_current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    return None


def ensure_default_admin(db: Session):
    """
    On first run (no admin users exist yet), create a default admin account
    so the dashboard is reachable immediately. Prints the generated
    password to the console ONCE — change it after first login.
    """
    existing = db.query(AdminUser).first()
    if existing:
        return

    username = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
    password = os.environ.get("DEFAULT_ADMIN_PASSWORD") or secrets.token_urlsafe(9)

    admin = AdminUser(username=username, password_hash=hash_password(password))
    db.add(admin)
    db.commit()

    print("=" * 60)
    print("No admin account found — created a default one:")
    print(f"  username: {username}")
    print(f"  password: {password}")
    print("Log in at /login and consider creating your own account via")
    print("scripts/create_admin.py, then remove this default one.")
    print("=" * 60)
