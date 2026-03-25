"""
Backfill tenant_id for all existing rows.

Phase 1-3 migration script:
1. Ensures a "default" tenant exists
2. Sets tenant_id = default tenant's id on all rows where tenant_id IS NULL
3. Prints counts of updated rows per table

Usage:
    python -m scripts.add_tenant_id
"""

import asyncio
import uuid
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.db import get_engine


DEFAULT_TENANT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "default"))
DEFAULT_TENANT_NAME = "Default"
DEFAULT_TENANT_SLUG = "default"
DEFAULT_TENANT_PLAN = "free"


async def main():
    engine = get_engine()

    async with engine.begin() as conn:
        # 1. Check if "default" tenant exists, create if not
        result = await conn.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": DEFAULT_TENANT_SLUG},
        )
        row = result.first()

        if row:
            tenant_id = row[0]
            print(f"[OK] Default tenant already exists: id={tenant_id}")
        else:
            tenant_id = DEFAULT_TENANT_ID
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, plan, status, primary_color, max_documents, max_monthly_messages) "
                    "VALUES (:id, :name, :slug, :plan, 'active', '#4a6cf7', 100, 1000)"
                ),
                {
                    "id": tenant_id,
                    "name": DEFAULT_TENANT_NAME,
                    "slug": DEFAULT_TENANT_SLUG,
                    "plan": DEFAULT_TENANT_PLAN,
                },
            )
            print(f"[CREATED] Default tenant: id={tenant_id}")

        # 2. Backfill tenant_id on all tables where tenant_id IS NULL
        tables = [
            "documents",
            "chunks",
            "chat_sessions",
            "faq_templates",
            "document_groups",
            "system_settings",
        ]

        total_updated = 0
        for table in tables:
            result = await conn.execute(
                text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
                {"tid": tenant_id},
            )
            count = result.rowcount
            total_updated += count
            print(f"  {table}: {count} rows updated")

        # admin_users: only update non-superadmin users (superadmin keeps NULL)
        result = await conn.execute(
            text(
                "UPDATE admin_users SET tenant_id = :tid "
                "WHERE tenant_id IS NULL AND role != 'superadmin'"
            ),
            {"tid": tenant_id},
        )
        admin_count = result.rowcount
        total_updated += admin_count
        print(f"  admin_users (non-superadmin): {admin_count} rows updated")

        print(f"\n[DONE] Total rows backfilled: {total_updated}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
