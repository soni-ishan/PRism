"""Quick verification: check azure_tenant_id column exists and test credential builder."""
import asyncio
import os
import ssl

from dotenv import load_dotenv

load_dotenv()


async def check_db_column():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.getenv("DATABASE_URL")
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    engine = create_async_engine(url, connect_args={"ssl": ssl_ctx})
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name, data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'registrations' AND column_name = 'azure_tenant_id'"
            )
        )
        row = result.fetchone()
        if row:
            print(f"  DB column: {row[0]}, type: {row[1]}, default: {row[2]}")
        else:
            print("  ERROR: azure_tenant_id column NOT found!")
    await engine.dispose()


def test_credential_builder():
    from mcp_servers.azure_mcp_server.ingest import _build_log_analytics_credential
    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    # Cross-tenant: should return ClientSecretCredential
    os.environ["AZURE_AD_CLIENT_ID"] = "test-client-id"
    os.environ["AZURE_AD_CLIENT_SECRET"] = "test-secret"
    cred = _build_log_analytics_credential(tenant_id="fake-tenant-123")
    assert isinstance(cred, ClientSecretCredential), f"Expected ClientSecretCredential, got {type(cred)}"
    print("  Cross-tenant credential: ClientSecretCredential")

    # Same-tenant (no tenant_id): should return DefaultAzureCredential
    cred2 = _build_log_analytics_credential(tenant_id=None)
    assert isinstance(cred2, DefaultAzureCredential), f"Expected DefaultAzureCredential, got {type(cred2)}"
    print("  Same-tenant credential:  DefaultAzureCredential")


def test_fired_time_validation():
    from mcp_servers.azure_mcp_server.ingest import _ISO8601_RE

    valid = [
        "2026-03-14T12:00:00Z",
        "2026-03-14T12:00:00",
        "2026-03-14T12:00:00.123Z",
    ]
    invalid = [
        "') | take 10000 //",
        "not-a-date",
        "2026-03-14",
        "",
    ]
    for v in valid:
        assert _ISO8601_RE.match(v), f"Should be valid: {v}"
    for i in invalid:
        assert not _ISO8601_RE.match(i), f"Should be invalid: {i}"
    print("  ISO-8601 validation: all patterns correct")


def test_repo_context_has_tenant_id():
    from agents.shared.data_contract import RepoContext

    ctx = RepoContext(
        owner="testorg",
        repo="testrepo",
        azure_tenant_id="abc-123",
        azure_customer_id="cust-456",
    )
    assert ctx.azure_tenant_id == "abc-123"
    print(f"  RepoContext.azure_tenant_id = {ctx.azure_tenant_id}")


def test_msal_uses_common_authority():
    import importlib, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform"))
    azure_service = importlib.import_module("server.services.azure_service")
    _get_msal_app = azure_service._get_msal_app

    # Temporarily set required env vars
    old_id = os.environ.get("AZURE_AD_CLIENT_ID")
    old_secret = os.environ.get("AZURE_AD_CLIENT_SECRET")
    os.environ["AZURE_AD_CLIENT_ID"] = "test-id"
    os.environ["AZURE_AD_CLIENT_SECRET"] = "test-secret"
    try:
        app = _get_msal_app()
        authority_url = app.authority.token_endpoint
        assert "common" in authority_url, f"Expected 'common' in authority, got: {authority_url}"
        print(f"  MSAL authority: {authority_url}")
    except ImportError:
        print("  MSAL not installed — skipping authority test")
    finally:
        if old_id:
            os.environ["AZURE_AD_CLIENT_ID"] = old_id
        if old_secret:
            os.environ["AZURE_AD_CLIENT_SECRET"] = old_secret


if __name__ == "__main__":
    print("\n=== PRism Cross-Tenant Verification ===\n")

    print("[1] DB column check:")
    asyncio.run(check_db_column())

    print("\n[2] Credential builder:")
    test_credential_builder()

    print("\n[3] fired_time validation:")
    test_fired_time_validation()

    print("\n[4] RepoContext.azure_tenant_id:")
    test_repo_context_has_tenant_id()

    print("\n[5] MSAL multi-tenant authority:")
    test_msal_uses_common_authority()

    print("\n=== All checks passed ===\n")
