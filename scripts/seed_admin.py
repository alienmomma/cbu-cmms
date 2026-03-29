"""Seed default admin and technician users into the database.

Run once after initial setup:
    python scripts/seed_admin.py

Skips creation if employee_number already exists.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from backend.database import AsyncSessionLocal, init_db
from backend.models import User, UserRole
from backend.auth import hash_password


_DEFAULT_USERS = [
    {
        "employee_number": "ADMIN001",
        "full_name": "System Administrator",
        "email": "admin@cbu.ac.zm",
        "role": UserRole.admin,
        "password": "admin123",
    },
    {
        "employee_number": "TECH001",
        "full_name": "Field Technician",
        "email": "tech@cbu.ac.zm",
        "role": UserRole.technician,
        "password": "tech123",
    },
]


async def seed():
    await init_db()
    async with AsyncSessionLocal() as db:
        created = 0
        skipped = 0
        for u in _DEFAULT_USERS:
            result = await db.execute(select(User).where(User.employee_number == u["employee_number"]))
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  SKIP  {u['employee_number']} ({u['role'].value}) — already exists")
                skipped += 1
                continue
            user = User(
                employee_number=u["employee_number"],
                full_name=u["full_name"],
                email=u["email"],
                role=u["role"],
                hashed_password=hash_password(u["password"]),
            )
            db.add(user)
            await db.commit()
            print(f"  CREATE {u['employee_number']} ({u['role'].value}) password={u['password']}")
            created += 1

    print(f"\nDone. Created: {created}  Skipped (already exist): {skipped}")
    print("\nLogin credentials:")
    print("  Admin     — Employee#: ADMIN001  Password: admin123")
    print("  Technician — Employee#: TECH001   Password: tech123")


if __name__ == "__main__":
    asyncio.run(seed())
