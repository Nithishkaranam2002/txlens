"""Vocabulary codes the API queries by. Concept ids are always looked up from these codes.

The same codes appear in txlens_pipelines.codes and txlens_omop.codes (checked by
`make vocab-check`); they are repeated here so the API does not depend on the pipeline package.
"""

# Transplant procedures by organ; every donor-specific transplant concept descends from these.
TRANSPLANT = {"kidney": ("SNOMED", "70536003"), "heart": ("SNOMED", "32413006")}
DONOR_TYPE = {"175901007": "living", "175902000": "deceased"}  # SNOMED code -> donor type
BIOPSY = {"kidney": ("SNOMED", "1293269004"), "heart": ("SNOMED", "387829002")}
GRAFT_FAILURE = (("SNOMED", "236583003"), ("SNOMED", "233934000"))
# Lab key -> (vocabulary, code). DSA has no standard concept: it is matched by source concept.
LABS = {
    "creatinine": ("LOINC", "2160-0"),
    "egfr": ("LOINC", "98979-8"),
    "tacrolimus_trough": ("LOINC", "77348-1"),
    "dd_cfdna": ("LOINC", "103142-6"),
}
DSA_LOCAL = ("TxLens", "DSA_MAX_MFI")
# Graft rejection: every transplanted-organ rejection concept descends from the SNOMED one;
# borderline rejection has no standard concept, only our local one.
REJECTION = (("SNOMED", "213148006"), ("TxLens", "BORDERLINE_REJECTION"))
