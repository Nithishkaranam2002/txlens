"""infra/azure/remote/gen_env.py: the VM's .env gets strong new secrets once, keeps them on a
re-run, is readable by its owner only, never prints a value and never holds an OpenAI key."""

import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest

from txlens_backend.seed_users import MIN_PASSWORD_LENGTH

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "infra" / "azure" / "remote" / "gen_env.py"
DOMAIN = "txlens-abc123.eastus.cloudapp.azure.com"


def load_script() -> ModuleType:
    """The script runs with the VM's python3, outside any package."""
    spec = importlib.util.spec_from_file_location("gen_env", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A folder with the real .env.example and a local-looking OpenAI key in it."""
    example = (ROOT / ".env.example").read_text()
    local_key = example.replace("OPENAI_API_KEY=placeholder", "OPENAI_API_KEY=sk-local-only")
    (tmp_path / ".env.example").write_text(local_key)
    return tmp_path


def test_first_run_writes_new_strong_secrets_owner_only(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gen = load_script()
    assert gen.main([str(checkout), DOMAIN]) == 0
    env = gen.parse((checkout / ".env").read_text())
    assert stat.S_IMODE((checkout / ".env").stat().st_mode) == 0o600
    assert env["OPENAI_API_KEY"] == "" and env["LANGFUSE_SECRET_KEY"] == ""
    assert env["FRONTEND_ORIGIN"] == f"https://{DOMAIN}" and env["COOKIE_SECURE"] == "true"
    assert env["TXLENS_DOMAIN"] == DOMAIN
    for role in gen.ROLES:
        assert len(env[f"DEMO_{role}_PASSWORD"]) >= MIN_PASSWORD_LENGTH
    assert len(env["JWT_SECRET"]) >= 32 and len(env["DEID_HMAC_KEY"]) >= 32
    assert f":{env['POSTGRES_PASSWORD']}@localhost:5432/" in env["DATABASE_URL"]
    assert env["APP_DATABASE_URL"].startswith(
        f"postgresql+psycopg://txlens_api:{env['APP_DB_PASSWORD']}@"
    )
    assert "placeholder" not in "".join(
        v for k, v in env.items() if "PASSWORD" in k or "SECRET" in k
    )
    printed = capsys.readouterr().out
    assert not any(env[key] in printed for key in gen.new_secrets())


def test_second_run_keeps_secrets_and_moves_the_domain(checkout: Path) -> None:
    gen = load_script()
    gen.main([str(checkout), DOMAIN])
    first = gen.parse((checkout / ".env").read_text())
    gen.main([str(checkout), "other.westus2.cloudapp.azure.com"])
    second = gen.parse((checkout / ".env").read_text())
    for key in ("POSTGRES_PASSWORD", "JWT_SECRET", "DEID_HMAC_KEY", "DEMO_ADMIN_PASSWORD",
                "DATABASE_URL", "APP_DATABASE_URL"):  # fmt: skip
        assert second[key] == first[key]
    assert second["FRONTEND_ORIGIN"] == "https://other.westus2.cloudapp.azure.com"


def test_two_servers_never_share_a_secret(tmp_path: Path, checkout: Path) -> None:
    gen = load_script()
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env.example").write_text((checkout / ".env.example").read_text())
    gen.main([str(checkout), DOMAIN])
    gen.main([str(other), DOMAIN])
    a, b = (gen.parse((d / ".env").read_text()) for d in (checkout, other))
    assert all(a[k] != b[k] for k in gen.new_secrets())
