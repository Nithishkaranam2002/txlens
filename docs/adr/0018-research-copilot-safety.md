# ADR 0018: The research copilot: tools before SQL, guards in code, quote-grounded literature

**Status:** accepted (2026-11-13)

## Context
Step 3.3 adds a copilot for clinicians and researchers. It answers questions about the
center's transplant data and the transplant literature. The risks are specific:
- A text-to-SQL model can read anything its database role can read: identities, other
  centers, free text, real dates.
- Counts can be subtracted: "aged ≥ 18" minus "aged ≥ 19" reveals a small group even when
  every single count is ≥ 11.
- Abstracts and tool results are text a model reads, so they can carry instructions
  (prompt injection).
- A model can write fluent medical claims that its sources do not support, cite papers it
  did not read, or name commercial products.
- Every call costs money.

## Decision
- **The model only chooses; code acts.**
  - A LangGraph state machine: screen, plan ⇄ act (at most 5 tools), answer or refuse.
  - The planner returns ONE action from a closed, schema-validated union (Instructor JSON
    schema; no free-form tool calls). Text cannot add a tool or widen what a tool does.
  - Structured tools come first: concept search, the cohort service, the survival service.
    They carry the platform's own rules (whole-year date filters, the landmark guard,
    suppression). SQL is a last resort for aggregates they cannot express.
- **The copilot sees aggregates and de-identified data only, for every role** (clinicians
  included). Tool results are reduced to counts, estimates and concept ids before a model
  sees them; patient ids never enter the model's context, Langfuse or the stored trace.
- **SQL: allow-list guard plus a database role that could not do harm anyway.**
  - sqlglot guard: one SELECT; an allow-list of syntax-tree node kinds (functions, window
    functions, FILTER, locks, INTO, LATERAL and parameters are rejected by default); only the
    `copilot.*` views and two vocabulary tables, columns resolved against them; aggregates
    only, and only in the outermost SELECT, over bare columns (no `sum(CASE ...)`); no
    min/max; GROUP BY only bare, non-identifying, non-continuous columns; every result must
    include `count(DISTINCT person_id)` (the cell size); LIMIT ≤ 200.
  - The query runs in its own transaction: READ ONLY, the caller's tenant (RLS), a 5 s
    statement timeout, as `txlens_copilot`. That role may read only the `copilot` views
    (no free text, no source values, dates turned into days after transplant and year) and
    two vocabulary tables. The views are owned by a NOLOGIN, non-bypass role, so tenant RLS
    still applies through them.
  - `set_config` is revoked from PUBLIC (granted back to the app role only), so even SQL
    that slipped past the guard could not switch tenants. Tests run SQL that skips the
    guard and check that the database refuses it.
  - Rows backed by 1-10 patients show `<11` for every aggregate.
- **A differencing guard at the service layer, per user, persisted.**
  - Every count released to a user is remembered (`app.released_count`) for 30 days. It
    covers the cohort builder (for de-identified roles), survival analyses (every role) and
    the copilot (every role).
  - Each count is described by its filters as constraints (equality, ranges, any-of sets,
    lab rules with day windows, opaque conditions). A new count is withheld when an earlier
    count of the same kind was over nested filters (one set refines the other) and the two
    differ by 1-10. Within one response, attrition steps are checked against each other.
  - The user sees "withheld" and the reason. No rounding: other numbers stay exact.
- **Prompt injection: data is data, and nothing depends on the model obeying.**
  - Questions are screened by deterministic patterns first (identifiers, other centers,
    writes, advice about a specific patient, injection phrasing), then by a classifier.
  - Tool results, abstracts and earlier answers reach the model only inside `<data>` blocks,
    JSON-encoded with `<` escaped, so no content can close the block.
  - Even an obeyed injection hits the closed action set, the SQL guard, the database role
    and the answer checks.
- **Literature answers are quote-grounded and checked in code.**
  - Hybrid retrieval (pgvector cosine + Postgres full-text, Reciprocal Rank Fusion, k = 60)
    over 2,063 open-access PubMed abstracts, behind a `VectorStore` interface. Abstracts are
    stored verbatim and kept out of git.
  - The model returns claims, each with 1-3 quotes copied from the abstracts it cites. A
    claim is shown only if a quote is found in the cited abstract by string matching
    (case, whitespace and typography normalized), is at least 6 words, and every number in
    the claim appears in its quotes. Other claims are dropped and counted; with none left,
    the copilot says the abstracts do not support an answer.
  - The citation popover shows the verified quotes.
  - Temperature 0, a fixed seed and PMID tie-breaks make answers as repeatable as the
    provider allows.
- **No commercial names in our own words.** A salted-hash list of names (the names
  themselves are never stored) rewrites them in the copilot's text to generic wording. A
  repository-wide test checks code, docs and reports. Quoted source text stays verbatim.
- **Cost, cache, trace, audit.** Every LLM and embedding call is cached and paid from a
  budget (per center and month, `LLM_BUDGET_USD`; evaluations use their own capped batch).
  Each question is a Langfuse trace with a generation per LLM call and a span per tool.
  Every question and every tool call (arguments and SQL, never values) is audited.

## Alternatives considered
- **Text-to-SQL over `cdm` with a prompt asking for aggregates.** It relies on the model
  obeying, and `cdm` has real dates and free text. Rejected.
- **LLM-as-judge or an embedding similarity check for citations.** Not deterministic, and
  it can be talked into agreeing. Quote matching is a yes/no fact. The judge is kept only to
  measure support in the evaluation.
- **Rounding every count (to 5).** It blunts differencing but also every real answer, and
  the user asked for exact numbers with a differencing guard instead.
- **Native tool calling.** LiteLLM 1.106 pulls in its MCP proxy for any call with tools
  (ADR 0013). Instructor's JSON-schema mode gives the same discriminated union.
- **Per-conversation differencing memory.** Defeated by opening a new conversation, so the
  memory is per user, in the database, across the copilot and the cohort and survival APIs.

## Consequences
- **Residual differencing risk.**
  - The guard compares counts of the same kind over filters it can describe. Filters
    written so they cannot be compared (an opaque expression, an arithmetic-rewritten bound,
    a restructured CTE) are not compared.
  - Several accounts working together can still combine their results.
  - In production this would be paired with data access agreements, per-user rate limits and
    review of exported outputs. The audit log records every question, so collusion can be
    found after the fact.
- **Row-level views exist elsewhere.** Researchers can list de-identified cohort members in
  the cohort builder (pseudonyms, shifted dates, age bands, ADR 0008). The differencing
  guard protects aggregates; it does not make those lists unnecessary to govern.
- **Grounded answers stream claim by claim, not token by token.** The claims are checked as
  a whole after one structured call, so the first sentence appears a little later. Data
  answers still stream sentence by sentence.
- **Strictness costs coverage.** A true statement whose wording the model cannot back with a
  six-word quote is dropped. On the evaluation questions no claim had to be dropped.
- **Revoking `set_config` needs a superuser.** On a managed Postgres without one, the
  migration only warns. The guard and the role's grants still apply.
- **New dependencies are held to releases at least two weeks old** (LangGraph, sqlglot, the
  RAGAS chain), pinned through `constraint-dependencies` in the root `pyproject.toml`.
