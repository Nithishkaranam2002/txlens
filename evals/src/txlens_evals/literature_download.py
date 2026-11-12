"""Download open-access transplant abstracts from PubMed (NCBI E-utilities) for the copilot.

`make literature-download` -> data/literature/abstracts.jsonl (git-ignored: the abstracts
belong to their publishers and are stored exactly as published, for faithful citations)
and evals/literature/manifest.json (committed: queries, date and PMID counts, so the corpus
can be rebuilt).

NCBI etiquette: every request names the tool ("txlens") and a contact email (NCBI_EMAIL in
.env); at most 3 requests per second, 10 with NCBI_API_KEY. Only articles with free full
text and an abstract are kept, in English, published 2010 or later.
"""

import argparse
import json
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "txlens"
FILTERS = 'AND hasabstract AND "free full text"[sb] AND english[la] AND 2010:2026[dp]'
# Topic -> (PubMed query, how many of its newest matching articles to fetch).
TOPICS: dict[str, tuple[str, int]] = {
    "dd_cfdna": ('("donor-derived cell-free DNA" OR dd-cfDNA OR "donor-derived cfDNA" OR '
                 '"donor fraction cell-free DNA") AND transplant*', 450),
    "kidney_rejection": ('"Kidney Transplantation"[MeSH] AND "Graft Rejection"[MeSH]', 520),
    "heart_rejection": ('"Heart Transplantation"[MeSH] AND "Graft Rejection"[MeSH]', 360),
    "dsa": ('("donor-specific antibodies" OR "donor-specific antibody" OR "donor specific '
            'anti-HLA") AND transplant*', 450),
    "banff": ("Banff AND (rejection OR classification OR lesion*) AND transplant*", 360),
    "bk_virus": ('("BK virus" OR "BK polyomavirus" OR BKPyV OR "polyomavirus nephropathy") '
                 "AND (kidney OR renal) AND transplant*", 360),
}  # fmt: skip
BATCH = 200


class NcbiSettings(BaseSettings):
    """NCBI_EMAIL (required by NCBI) and NCBI_API_KEY (optional), from the env or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ncbi_email: str = ""
    ncbi_api_key: SecretStr = SecretStr("")


@dataclass
class Abstract:
    """One article as stored: text exactly as published, plus where it came from."""

    pmid: int
    title: str
    abstract: str
    journal: str
    year: int | None
    doi: str | None
    pmcid: str | None
    topics: list[str] = field(default_factory=list)


class Eutils:
    """A polite E-utilities client: tool, email, optional key, rate limit, retries."""

    def __init__(self, email: str, api_key: str | None, client: httpx.Client | None = None):
        self.params: dict[str, str | int] = {"tool": TOOL, "email": email}
        if api_key:
            self.params["api_key"] = api_key
        self.interval = 0.11 if api_key else 0.34
        self.client = client or httpx.Client(timeout=60)
        self.last = 0.0

    def get(self, endpoint: str, **params: str | int) -> httpx.Response:
        """One request, spaced out to the rate limit, retried on 429/5xx."""
        for attempt in range(4):
            wait = self.interval - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            response = self.client.get(f"{EUTILS}/{endpoint}", params=self.params | params)
            if response.status_code < 429 or attempt == 3:
                response.raise_for_status()
                return response
            time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def search(self, query: str, limit: int) -> list[int]:
        """The newest `limit` PMIDs matching the query (with the corpus filters)."""
        found = self.get("esearch.fcgi", db="pubmed", term=f"({query}) {FILTERS}",
                         retmax=limit, sort="pub_date", retmode="json").json()  # fmt: skip
        return [int(p) for p in found["esearchresult"]["idlist"]]

    def fetch(self, pmids: list[int]) -> Iterator[Abstract]:
        """Full records, in batches."""
        for start in range(0, len(pmids), BATCH):
            ids = ",".join(str(p) for p in pmids[start : start + BATCH])
            xml = self.get("efetch.fcgi", db="pubmed", id=ids, retmode="xml").text
            yield from parse(xml)


def text_of(node: ET.Element | None) -> str:
    """All text inside an element (titles and abstracts contain markup such as <i>)."""
    return "".join(node.itertext()).strip() if node is not None else ""


def year_of(article: ET.Element) -> int | None:
    """Publication year from PubDate/Year, or the first year in a MedlineDate."""
    date = article.find("Journal/JournalIssue/PubDate")
    if date is None:
        return None
    raw = text_of(date.find("Year")) or text_of(date.find("MedlineDate"))
    return int(raw[:4]) if raw[:4].isdigit() else None


def parse(xml: str) -> Iterator[Abstract]:
    """Articles of an efetch response; labelled abstract sections keep their labels."""
    for record in ET.fromstring(xml).iter("PubmedArticle"):
        article = record.find("MedlineCitation/Article")
        if article is None:
            continue
        parts = []
        for section in article.findall("Abstract/AbstractText"):
            label = section.get("Label")
            parts.append(f"{label}: {text_of(section)}" if label else text_of(section))
        ids = {i.get("IdType"): (i.text or "").strip()
               for i in record.findall("PubmedData/ArticleIdList/ArticleId")}  # fmt: skip
        abstract = "\n".join(p for p in parts if p)
        if abstract:
            yield Abstract(
                pmid=int(text_of(record.find("MedlineCitation/PMID"))),
                title=text_of(article.find("ArticleTitle")),
                abstract=abstract,
                journal=text_of(article.find("Journal/Title")),
                year=year_of(article),
                doi=ids.get("doi") or None,
                pmcid=ids.get("pmc") or None,
            )


def download(
    eutils: Eutils, topics: dict[str, tuple[str, int]]
) -> tuple[list[Abstract], dict[str, object]]:
    """All topics' articles, de-duplicated by PMID (an article keeps every topic it matched)."""
    by_topic = {topic: eutils.search(query, limit) for topic, (query, limit) in topics.items()}
    topics_of: dict[int, list[str]] = {}
    for topic, pmids in by_topic.items():
        for pmid in pmids:
            topics_of.setdefault(pmid, []).append(topic)
    articles = [a for a in eutils.fetch(sorted(topics_of)) if a.pmid in topics_of]
    for article in articles:
        article.topics = topics_of[article.pmid]
    manifest = {
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "PubMed E-utilities (esearch + efetch)",
        "filters": FILTERS,
        "topics": {
            t: {"query": q, "limit": n, "found": len(by_topic[t])} for t, (q, n) in topics.items()
        },
        "unique_pmids": len(topics_of),
        "with_abstract": len(articles),
    }
    return articles, manifest


def main(argv: list[str] | None = None) -> None:
    """Download and write the corpus and its manifest."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/literature/abstracts.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path("evals/literature/manifest.json"))
    args = parser.parse_args(argv)
    settings = NcbiSettings()
    if "@" not in settings.ncbi_email:
        raise SystemExit("Set NCBI_EMAIL in .env: NCBI asks every E-utilities client for one.")
    key = settings.ncbi_api_key.get_secret_value() or None
    articles, manifest = download(Eutils(settings.ncbi_email.strip(), key), TOPICS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as out:
        for article in articles:
            out.write(json.dumps(asdict(article), ensure_ascii=False) + "\n")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(articles)} abstracts -> {args.out}")


if __name__ == "__main__":
    main()
