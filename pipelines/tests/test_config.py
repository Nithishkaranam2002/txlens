"""The pipeline's tenant registry matches the generator's centers (no Spark needed)."""

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.writers.lab_feeds import DIALECTS
from txlens_pipelines.config import ID_RANGE, TENANTS, PipelineConfig


def test_tenants_match_generator_centers() -> None:
    centers = {c.tenant_id: c for c in GeneratorConfig().centers}
    assert {t.tenant_id for t in TENANTS} == set(centers)
    for tenant in TENANTS:
        center = centers[tenant.tenant_id]
        assert tenant.name == center.name
        assert tenant.lab_dialect == center.code
        assert tenant.lab_file == DIALECTS[center.code].file_name


def test_tenant_id_ranges_are_distinct_and_fit_in_int32() -> None:
    ordinals = [t.ordinal for t in TENANTS]
    assert len(set(ordinals)) == len(ordinals)
    assert max(ordinals) + 1 <= (2**31 - 1) // ID_RANGE


def test_only_limits_tenants_and_keeps_batch() -> None:
    config = PipelineConfig("raw", "vocab")
    limited = config.only(["center_b"])
    assert [t.tenant_id for t in limited.tenants] == ["center_b"]
    assert limited.batch_id == config.batch_id
    assert config.only(None) is config
