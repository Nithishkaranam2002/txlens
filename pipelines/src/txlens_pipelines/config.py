"""Pipeline settings: where data lives, which tenants exist, and quality limits.

Plain dataclasses (no extra dependencies), so the same code runs on Databricks, where only
PySpark, Delta Lake and pandera are available.
"""

from dataclasses import dataclass, field
from uuid import uuid4

# Each tenant owns the id range [ordinal * ID_RANGE, (ordinal + 1) * ID_RANGE). OMOP ids are
# 32-bit integers, so ordinals 1-20 fit below 2,147,483,647.
ID_RANGE = 100_000_000


@dataclass(frozen=True)
class Tenant:
    """One transplant center: its id range and the dialect of its lab feed."""

    tenant_id: str
    ordinal: int
    name: str
    lab_file: str
    lab_dialect: str


TENANTS = (
    Tenant("center_a", 1, "Aldermoor Transplant Center", "center_a_labs.csv", "a"),
    Tenant("center_b", 2, "Quillfield Medical Center", "center_b_labs.csv", "b"),
    Tenant("center_c", 3, "Vesperhill Kidney and Heart Institute", "center_c_labs.csv", "c"),
)
LAYERS = ("bronze", "silver", "gold", "load", "report")


def new_batch_id() -> str:
    """A unique id for one pipeline run, stored on every bronze row it ingests."""
    return uuid4().hex


@dataclass(frozen=True)
class PipelineConfig:
    """Everything one run needs to know."""

    raw_root: str
    vocab_root: str
    tenants: tuple[Tenant, ...] = TENANTS
    # A table whose share of quarantined rows is above this fails the run (contract violation).
    max_quarantine_rate: float = 0.05
    batch_id: str = field(default_factory=new_batch_id)

    def tenant(self, tenant_id: str) -> Tenant:
        """The tenant with this id."""
        for tenant in self.tenants:
            if tenant.tenant_id == tenant_id:
                return tenant
        raise KeyError(f"unknown tenant: {tenant_id}")

    def only(self, tenant_ids: list[str] | None) -> "PipelineConfig":
        """A copy limited to some tenants (all when None)."""
        if not tenant_ids:
            return self
        return PipelineConfig(
            self.raw_root,
            self.vocab_root,
            tuple(self.tenant(t) for t in tenant_ids),
            self.max_quarantine_rate,
            self.batch_id,
        )
