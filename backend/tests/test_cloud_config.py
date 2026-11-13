"""The cloud demo's compose files: Caddy is the only public entry, every other port stays on
the VM's loopback, no OpenAI key reaches a container, and no Langfuse database is created."""

from pathlib import Path
from typing import Any

import yaml

INFRA = Path(__file__).parents[2] / "infra"
PUBLIC_PORTS = {"80:80", "443:443"}


def load(path: Path) -> dict[str, Any]:
    """A compose file as a dict."""
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data


def test_only_caddy_publishes_ports_and_only_80_and_443() -> None:
    cloud = load(INFRA / "azure" / "compose.cloud.yml")["services"]
    published = {name: svc["ports"] for name, svc in cloud.items() if svc.get("ports")}
    assert set(published) == {"caddy"}
    assert set(published["caddy"]) == PUBLIC_PORTS


def test_every_base_port_is_bound_to_loopback() -> None:
    base = load(INFRA / "docker-compose.yml")["services"]
    ports = [port for svc in base.values() for port in svc.get("ports", [])]
    assert ports and all(port.startswith("127.0.0.1:") for port in ports)


def test_backend_containers_get_an_empty_openai_key_and_https_settings() -> None:
    cloud = load(INFRA / "azure" / "compose.cloud.yml")["services"]
    for name in ("api", "worker", "tools"):
        env = cloud[name]["environment"]
        assert env["OPENAI_API_KEY"] == ""
        assert env["COOKIE_SECURE"] == "true"
        assert env["FRONTEND_ORIGIN"].startswith("https://")
    assert cloud["web"]["environment"]["TXLENS_HSTS"] == "1"


def test_langfuse_init_script_is_replaced_by_a_no_op() -> None:
    cloud = load(INFRA / "azure" / "compose.cloud.yml")["services"]
    target = "/docker-entrypoint-initdb.d/init-langfuse-db.sh"
    mounts = [m for m in cloud["postgres"]["volumes"] if f":{target}:" in m]
    assert len(mounts) == 1
    script = INFRA / mounts[0].split(":")[0]
    assert "CREATE DATABASE" not in script.read_text()


def test_caddy_sends_hsts_and_noindex_and_hides_the_site_from_robots() -> None:
    caddyfile = (INFRA / "azure" / "Caddyfile").read_text()
    assert 'Strict-Transport-Security "max-age=63072000' in caddyfile
    assert 'X-Robots-Tag "noindex' in caddyfile
    assert "Disallow: /" in caddyfile
    assert "reverse_proxy web:3000" in caddyfile
