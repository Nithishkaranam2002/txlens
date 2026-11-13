"""The copilot's suggested questions (shown above its input).

Their answers are kept in the LLM cache (`txlens-demo copilot-cache`), so they also work in the
public demo, which has no API key: one count, one cohort, one survival question, two
literature questions with PubMed citations, and one refusal.
"""

SUGGESTED = (
    "How many kidney recipients had a deceased donor?",
    "How many kidney recipients had a dd-cfDNA result of 1% or more in the first year after "
    "transplant?",
    "Graft survival by donor type, with a 1-year landmark",
    "What does the literature say about dd-cfDNA thresholds for antibody-mediated rejection?",
    "What does the literature say about dd-cfDNA after heart transplantation?",
    "List the MRNs of the patients with antibody-mediated rejection.",
)
