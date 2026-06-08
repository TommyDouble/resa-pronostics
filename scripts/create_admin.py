#!/usr/bin/env python3
"""Create admin user. Usage: python scripts/create_admin.py <username> <password>"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
import aiosqlite
from app.config import settings
from app.auth import hash_password

DB_PATH = settings.DATABASE_URL.replace("./", "")


async def create_admin(username: str, password: str):
    await init_db()
    hashed = hash_password(password)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES (?,?)",
                (username, hashed)
            )
            await db.commit()
            print(f"✓ Admin '{username}' created.")
        except Exception as e:
            if "UNIQUE" in str(e):
                await db.execute(
                    "UPDATE admin_users SET password_hash=? WHERE username=?",
                    (hashed, username)
                )
                await db.commit()
                print(f"✓ Admin '{username}' password updated.")
            else:
                print(f"Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/create_admin.py <username> <password>")
        sys.exit(1)
    asyncio.run(create_admin(sys.argv[1], sys.argv[2]))
