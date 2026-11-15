# ADR 0022: Patient search by role, audited by a keyed fingerprint of the search text

**Status:** accepted (2026-11-15)

## Context
Clinicians want to jump to a patient by name or MRN from any page, and to narrow the
worklist the same way. Researchers and data stewards must never be able to look a person up
by name: even "no match" versus "one match" for a name would tell them whether that person
is a patient of the center. Every PHI view is audited, but the search text is often a
patient's name, so writing it into `audit_log` puts names into a table that many reviews and
exports touch.

## Decision
- **One endpoint**, `GET /patients/search?q=`, used by the top bar. The worklist takes the
  same `q` and filters with the same SQL (`services/patients.search_query`), so the two can
  never disagree.
- **Role rules in SQL:** clinicians match part of a name or the start of an MRN.
  Researchers and data stewards match the start of a pseudonym only; their query never joins
  the identity table, so a name cannot match even by accident. Admins get 403.
- **Limits:** own center only (RLS), 2 to 100 characters, at most 10 results, LIKE wildcards
  escaped, and a per-user rate limit (`rate_limit_search_per_minute`, default 120), so the
  search cannot be used to page through the patient list.
- **Audit:** each search writes `phi_view` / `deid_view` with object type `patient_search`,
  the ids returned, `q_length` and `q_fingerprint`: an HMAC-SHA256 (key = the
  de-identification key, which is never stored in the database) of the lower-cased text,
  cut to 16 hex characters. The worklist and the patient list store the same two fields
  instead of the raw text.

## Alternatives
- **Raw text in the audit log:** easiest to investigate, but copies names into the log.
- **Plain SHA-256 of the text:** looks safe but is not. There are few names, so anyone with
  the log can hash a list of names and find which were searched.
- **Length only:** safest, but two searches for the same text cannot be linked, which an
  investigation ("did this user look up the same person every night?") needs.

## Consequences
- The audit log never holds what was typed. To confirm that a user searched for a given
  name, someone holding the key computes its fingerprint and compares; the returned patient
  ids already show who was looked at.
- Rotating the de-identification key changes fingerprints (and pseudonyms) from then on.
- Fingerprints are case- and space-blind, so "SMITH" and " smith" link together.
