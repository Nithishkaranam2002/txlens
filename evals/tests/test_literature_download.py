"""PubMed download: NCBI etiquette, retries, parsing and de-duplication (mocked E-utilities)."""

import json
from pathlib import Path

import httpx
import pytest

from txlens_evals import literature_download as dl

# A made-up efetch response (not a real article): labelled sections, inline markup, a
# MedlineDate year, a DOI, and one article without an abstract (skipped).
EFETCH = """<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle>
  <MedlineCitation><PMID>101</PMID><Article>
   <Journal><Title>Test Journal</Title>
    <JournalIssue><PubDate><MedlineDate>2021 Jan-Feb</MedlineDate></PubDate>
    </JournalIssue></Journal>
   <ArticleTitle>A <i>made-up</i> study</ArticleTitle>
   <Abstract><AbstractText Label="BACKGROUND">First part.</AbstractText>
    <AbstractText Label="RESULTS">Second <sup>2</sup> part.</AbstractText></Abstract>
  </Article></MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="doi">10.0/test</ArticleId>
    <ArticleId IdType="pmc">PMC1</ArticleId></ArticleIdList></PubmedData>
 </PubmedArticle>
 <PubmedArticle>
  <MedlineCitation><PMID>102</PMID><Article>
   <Journal><Title>Other</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
   <ArticleTitle>No abstract</ArticleTitle>
  </Article></MedlineCitation>
 </PubmedArticle>
 <PubmedArticle>
  <MedlineCitation><PMID>103</PMID><Article>
   <Journal><Title>Third</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
   <ArticleTitle>Plain</ArticleTitle><Abstract><AbstractText>Only text.</AbstractText></Abstract>
  </Article></MedlineCitation>
 </PubmedArticle>
</PubmedArticleSet>"""


def test_parse_keeps_text_exactly_and_skips_articles_without_abstract() -> None:
    first, third = list(dl.parse(EFETCH))
    assert first == dl.Abstract(
        pmid=101,
        title="A made-up study",
        abstract="BACKGROUND: First part.\nRESULTS: Second 2 part.",
        journal="Test Journal",
        year=2021,
        doi="10.0/test",
        pmcid="PMC1",
    )
    assert (third.pmid, third.year, third.doi) == (103, 2024, None)


class FakeNcbi:
    """Answers esearch per query and efetch with EFETCH; the first request gets a 429."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self.requests) == 1:
            return httpx.Response(429)
        if request.url.path.endswith("esearch.fcgi"):
            ids = ["101", "102"] if "alpha" in request.url.params["term"] else ["101", "103"]
            return httpx.Response(200, json={"esearchresult": {"idlist": ids}})
        return httpx.Response(200, text=EFETCH)


@pytest.fixture
def ncbi(monkeypatch: pytest.MonkeyPatch) -> FakeNcbi:
    monkeypatch.setattr("txlens_evals.literature_download.time.sleep", lambda _: None)
    return FakeNcbi()


def test_every_request_names_the_tool_and_contact(ncbi: FakeNcbi) -> None:
    client = dl.Eutils(
        "someone@example.org", None, httpx.Client(transport=httpx.MockTransport(ncbi))
    )
    articles, manifest = dl.download(client, {"a": ("alpha", 5), "b": ("beta", 5)})
    assert all(r.url.params["tool"] == "txlens" for r in ncbi.requests)
    assert all(r.url.params["email"] == "someone@example.org" for r in ncbi.requests)
    assert "api_key" not in ncbi.requests[0].url.params
    assert ncbi.requests[0].url == ncbi.requests[1].url  # the 429 was retried
    assert {a.pmid: a.topics for a in articles} == {101: ["a", "b"], 103: ["b"]}
    assert manifest["unique_pmids"] == 3 and manifest["with_abstract"] == 2


def test_the_filters_ask_for_free_full_text_with_abstracts(ncbi: FakeNcbi) -> None:
    client = dl.Eutils("x@example.org", "key", httpx.Client(transport=httpx.MockTransport(ncbi)))
    dl.download(client, {"a": ("alpha", 5)})
    term = next(r for r in ncbi.requests if "esearch" in r.url.path).url.params["term"]
    assert '"free full text"[sb]' in term and "hasabstract" in term
    assert ncbi.requests[-1].url.params["api_key"] == "key"
    assert client.interval < 0.2  # 10 requests a second with a key


def test_requests_are_spaced_to_three_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("txlens_evals.literature_download.time.sleep", waits.append)
    client = dl.Eutils("x@example.org", None, httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"esearchresult": {"idlist": []}}))))  # fmt: skip
    client.search("a", 1)
    client.search("b", 1)
    assert waits and max(waits) <= 0.34 and max(waits) > 0.3


def test_main_refuses_to_run_without_a_contact_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("NCBI_EMAIL", raising=False)
    with pytest.raises(SystemExit, match="NCBI_EMAIL"):
        dl.main(["--out", str(tmp_path / "a.jsonl")])


def test_main_writes_corpus_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ncbi: FakeNcbi
) -> None:
    monkeypatch.setenv("NCBI_EMAIL", "x@example.org")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **_: real_client(transport=httpx.MockTransport(ncbi))
    )
    monkeypatch.setattr(dl, "TOPICS", {"a": ("alpha", 5)})
    out, manifest = tmp_path / "abstracts.jsonl", tmp_path / "manifest.json"
    dl.main(["--out", str(out), "--manifest", str(manifest)])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["pmid"] for r in rows] == [101]  # 102 has no abstract
    assert json.loads(manifest.read_text())["topics"]["a"]["found"] == 2
