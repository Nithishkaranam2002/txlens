"""Reading the Athena-format vocabulary subset."""

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from txlens_pipelines.runtime import LocalRuntime
from txlens_pipelines.vocab import load_vocabulary

pytestmark = pytest.mark.spark


def test_vocabulary_includes_local_concepts_and_reviewed_mappings(runtime: LocalRuntime) -> None:
    vocab = load_vocabulary(runtime)
    ids = {r.concept_id for r in vocab.concept.select("concept_id").collect()}
    assert 950637 in ids and 2_000_000_001 in ids
    brand = vocab.concept_relationship.where("relationship_id = 'Brand name of'").first()
    assert brand is not None and brand.concept_id_2 == 950637
    tacro = vocab.source_to_concept_map.where("source_code = 'tacro'").first()
    assert tacro is not None and tacro.source_vocabulary_id == "TXLENS_DRUG"
    empty_reason = vocab.concept.where("concept_id = 3016723").first()
    assert empty_reason is not None and empty_reason.invalid_reason is None
