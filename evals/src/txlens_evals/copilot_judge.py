"""The judge for literature answers: RAGAS metrics and a citation check, on another model.

The judge is a different model from the copilot's (EVAL_JUDGE_MODEL, default
openai/gpt-5.6-luna), so the copilot does not grade its own answers; it is still a model of
the same family, and the report says so. Every judge call goes through LiteLLM with the
copilot's machinery (cache, budget), so a re-run is free and the cap holds.

- Faithfulness (RAGAS): the answer is split into claims; the share of claims the retrieved
  abstracts support.
- Answer relevancy (RAGAS): questions generated back from the answer, compared with the real
  question by embedding similarity (strictness 1: the cached judge would repeat itself).
- Citation support: for each (sentence, cited abstract) pair, does the abstract support the
  sentence? Asked as a structured yes / partly / no question.
- Declining: for questions the corpus cannot answer, did the answer decline?
"""

import asyncio
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.outputs import Generation, LLMResult
from pydantic import BaseModel
from ragas import SingleTurnSample
from ragas.embeddings.base import BaseRagasEmbeddings
from ragas.llms.base import BaseRagasLLM
from ragas.metrics import Faithfulness, ResponseRelevancy

from txlens_backend.copilot.answer_filter import BOUNDARY, CITATION
from txlens_backend.copilot.embeddings import Embedder
from txlens_backend.copilot.llm import CopilotLlm
from txlens_backend.documents.budget import Budget
from txlens_backend.documents.llm import price_per_token


class RagasJudge(BaseRagasLLM):
    """RAGAS's LLM interface over the judge model (plain text, cached and budgeted)."""

    def __init__(self, llm: CopilotLlm) -> None:
        super().__init__()
        self.llm = llm

    def generate_text(
        self,
        prompt: Any,
        n: int = 1,
        temperature: float = 0.01,
        stop: list[str] | None = None,
        callbacks: Any = None,
    ) -> LLMResult:
        text = "".join(self.llm.stream([{"role": "user", "content": prompt.to_string()}], "ragas"))
        return LLMResult(generations=[[Generation(text=text) for _ in range(n)]])

    async def agenerate_text(
        self,
        prompt: Any,
        n: int = 1,
        temperature: float | None = 0.01,
        stop: list[str] | None = None,
        callbacks: Any = None,
    ) -> LLMResult:
        return await asyncio.to_thread(self.generate_text, prompt, n, 0.01, stop, callbacks)

    def is_finished(self, response: LLMResult) -> bool:
        return True


class RagasEmbeddings(BaseRagasEmbeddings):
    """RAGAS's embedding interface over the copilot's embedder, paid from the eval budget."""

    def __init__(self, embedder: Embedder, budget: Budget) -> None:
        super().__init__()
        self.embedder, self.budget = embedder, budget

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        reserved = price_per_token(self.embedder.model)[0] * sum(len(t) for t in texts) + 1e-6
        self.budget.reserve(reserved)
        vectors: list[list[float]] = []
        cost = 0.0
        try:
            vectors, cost = self.embedder.embed(texts)
        finally:
            self.budget.settle(reserved, cost)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)


class Support(BaseModel):
    """Whether one abstract supports one sentence."""

    verdict: Literal["yes", "partly", "no"]


class Declines(BaseModel):
    """Whether an answer declines (says the sources do not answer the question)."""

    declines: bool


SUPPORT = """You check citations. Given a SENTENCE from an answer and the ABSTRACT it cites,
say whether the abstract supports the sentence: "yes" (fully), "partly" (some of it), or
"no". Judge only by the abstract's text."""
DECLINE = """Given a QUESTION and an ANSWER, say whether the answer declines: it says the
available sources do not answer the question (or answers nothing substantive)."""


class Judge:
    """RAGAS metrics, citation support and declining, with one judge model."""

    def __init__(self, llm: CopilotLlm, embedder: Embedder, budget: Budget) -> None:
        self.llm = llm
        ragas_llm = RagasJudge(llm)
        self.faithfulness = Faithfulness(llm=ragas_llm)
        embeddings = RagasEmbeddings(embedder, budget)
        self.relevancy = ResponseRelevancy(llm=ragas_llm, embeddings=embeddings, strictness=1)

    def ragas(self, question: str, answer: str, contexts: Sequence[str]) -> tuple[float, float]:
        """(faithfulness, answer relevancy) of one answer, 0-1."""
        sample = SingleTurnSample(user_input=question, response=CITATION.sub("", answer),
                                  retrieved_contexts=list(contexts))  # fmt: skip

        async def score() -> tuple[float, float]:
            return (await self.faithfulness.single_turn_ascore(sample),
                    await self.relevancy.single_turn_ascore(sample))  # fmt: skip

        return asyncio.run(score())

    def supports(self, sentence: str, abstract: str) -> str:
        """'yes', 'partly' or 'no'."""
        messages = [{"role": "system", "content": SUPPORT},
                    {"role": "user", "content": f"SENTENCE: {CITATION.sub('', sentence)}\n\n"
                                                f"ABSTRACT: {abstract}"}]  # fmt: skip
        return self.llm.structured(Support, messages, "citation-support").verdict

    def declines(self, question: str, answer: str) -> bool:
        asked = f"QUESTION: {question}\n\nANSWER: {answer}"
        messages = [{"role": "system", "content": DECLINE}, {"role": "user", "content": asked}]
        return self.llm.structured(Declines, messages, "declines").declines


def cited_sentences(answer: str) -> list[tuple[str, list[int]]]:
    """(sentence, cited PMIDs) for each sentence of an answer that cites something."""
    pairs = []
    for sentence in BOUNDARY.split(answer):
        pmids = [int(p) for p in CITATION.findall(sentence)]
        if pmids:
            pairs.append((sentence.strip(), pmids))
    return pairs
