"""One-time migration: add azure_tenant_id to registrations table."""
import asyncio
import os
import ssl

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()


async def migrate():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    engine = create_async_engine(url, connect_args={"ssl": ssl_ctx})
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'registrations' AND column_name = 'azure_tenant_id'"
            )
        )
        if result.scalar():
            print("Column azure_tenant_id already exists — nothing to do.")
        else:
            await conn.execute(
                text("ALTER TABLE registrations ADD COLUMN azure_tenant_id TEXT DEFAULT ''")
            )
            print("Added azure_tenant_id column to registrations table.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
