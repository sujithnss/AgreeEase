"""
Create (or reset) a staff login account for the dashboard.

Usage (from the project root, with the backend venv activated):
    python3 scripts/create_admin.py
"""

import sys
import os
import getpass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from db import init_db, SessionLocal, AdminUser  # noqa: E402
from auth import hash_password  # noqa: E402


def main():
    init_db()
    db = SessionLocal()

    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords don't match. Aborting.")
        return

    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        existing.password_hash = hash_password(password)
        db.commit()
        print(f"Password updated for existing user '{username}'.")
    else:
        db.add(AdminUser(username=username, password_hash=hash_password(password)))
        db.commit()
        print(f"Created new admin user '{username}'.")

    db.close()


if __name__ == "__main__":
    main()
