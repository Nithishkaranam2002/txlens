"""The copilot's suggested questions (shown above its input) and the public demo's replies.

Their answers are kept in the LLM cache (`txlens-demo copilot-cache`), so they also work in the
public demo, which has no API key: one data question answered with checked SQL, one cohort
count, one survival question, two literature questions with PubMed citations, and one refusal.

The cache key is the exact prompt, so a typed question is first matched to a suggested one
(`match`): the same words after normalising case, spaces and punctuation, or the same words
with at most two typos (numbers must be exact) and one left-out or extra filler word.
"""

import re
import unicodedata
from difflib import SequenceMatcher

SUGGESTED = (
    "Median dd-cfDNA in the first year after transplant, by organ",
    "How many kidney recipients had a dd-cfDNA result of 1% or more in the first year after "
    "transplant?",
    "Graft survival by donor type, with a 1-year landmark",
    "What does the literature say about dd-cfDNA thresholds for antibody-mediated rejection?",
    "What does the literature say about dd-cfDNA after heart transplantation?",
    "List the MRNs of the patients with antibody-mediated rejection.",
)

DEMO_NO_LIVE_AI = ("This public demo runs without live AI to keep it free and safe. "
                   "Try one of these:")  # fmt: skip
HELP = (
    "I'm a research copilot for transplant programs. I answer questions with data tools "
    "(cohort counts, survival analysis and checked, read-only SQL) over your center's "
    "de-identified data, and I search open-access transplant abstracts and cite them by PMID. "
    "I only ever see de-identified aggregates: no names, MRNs or exact dates, and counts from "
    "1 to 10 are hidden to protect privacy. I don't give clinical advice. "
    "This public demo runs without live AI, so try one of these:"
)
HELP_QUESTION = re.compile(
    r"(help|hi|hello|hey|what is this( about)?|what does (this|it) do|what can (you|i) do"
    r"|what can (you|i) ask|how does (this|it) work|how do i use (this|it)|who are you"
    r"|what are you)( (copilot|app|tool|page|demo))?( please)?"
)
FILLER = frozenset({"the", "a", "an", "please"})
MAX_TYPOS = 2
TYPO_RATIO = 0.8


def normalize(text: str) -> str:
    """Lower case, hyphens and apostrophes dropped, other punctuation as spaces, single spaces."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[-'\u2019]", "", text)
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def close(typed: str, saved: str) -> bool:
    """The same word, or a typo of it (words with digits must be exact)."""
    if typed == saved:
        return True
    if any(c.isdigit() for c in typed + saved):
        return False
    return SequenceMatcher(None, typed, saved).ratio() >= TYPO_RATIO


def near(typed: list[str], saved: list[str]) -> bool:
    """At most MAX_TYPOS mistyped words and one left-out or extra filler word."""
    typos = fillers = 0
    for op, i1, i2, j1, j2 in SequenceMatcher(None, typed, saved, autojunk=False).get_opcodes():
        a, b = typed[i1:i2], saved[j1:j2]
        if op == "equal":
            continue
        if op == "replace" and len(a) == len(b) and all(map(close, a, b)):
            typos += len(a)
        elif op in ("insert", "delete") and len(a + b) == 1 and (a + b)[0] in FILLER:
            fillers += 1
        else:
            return False
    return typos <= MAX_TYPOS and fillers <= 1


def match(question: str) -> str | None:
    """The suggested question this one is (after normalising, or with a few typos), if any."""
    typed = normalize(question)
    for saved in SUGGESTED:
        if normalize(saved) == typed:
            return saved
    words = typed.split()
    found = [s for s in SUGGESTED if near(words, normalize(s).split())]
    return found[0] if len(found) == 1 else None


def asks_for_help(question: str) -> bool:
    """Questions like "what is this about?" or "help"."""
    return HELP_QUESTION.fullmatch(normalize(question)) is not None
