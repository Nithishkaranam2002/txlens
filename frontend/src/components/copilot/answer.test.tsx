import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { newTurn, type Turn } from "@/lib/copilot";
import { renderWithQuery } from "@/test/render";

import { AnswerBody } from "./answer";

const tool = (step: number, label: string, extra: Partial<Turn["tools"][number]> = {}) => ({
  type: "tool" as const, step, tool: label, label, args: {}, ok: true, error: null, sql: null, display: null, notices: [], latency_ms: 1, ...extra,
});

describe("AnswerBody", () => {
  it("shows the tool trace in order, and SQL on request", async () => {
    const turn: Turn = {
      ...newTurn("q"),
      tools: [
        tool(1, "searched concepts"),
        tool(2, "ran SQL", { sql: "SELECT count(DISTINCT person_id) FROM copilot.patient LIMIT 200", display: { kind: "table", columns: ["organ", "n"], rows: [["kidney", 427], ["heart", "<11"]], notices: [] } }),
      ],
      text: "There are 427 kidney recipients.",
    };
    renderWithQuery(<AnswerBody turn={turn} />);
    const steps = within(screen.getByRole("list", { name: "Steps the copilot took" })).getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual(["searched concepts", "→ran SQL"]);
    await userEvent.click(screen.getByText("Show SQL"));
    expect(screen.getByText(/FROM copilot.patient LIMIT 200/)).toBeVisible();
    expect(screen.getByRole("cell", { name: "427" })).toBeInTheDocument();
    expect(screen.getByTitle("Counts from 1 to 10 are hidden to protect privacy")).toHaveTextContent("<11");
  });

  it("links citations to their sources with the quoted sentences", () => {
    const turn: Turn = {
      ...newTurn("q"),
      text: "- Levels above 1% suggested ABMR [PMID:111].\n- Others disagreed [PMID:222].",
      citations: [
        { pmid: 111, title: "A study", journal: "J One", year: 2022, snippet: "Levels above 1% suggested ABMR.", url: "https://pubmed.ncbi.nlm.nih.gov/111/" },
        { pmid: 222, title: "Another", journal: "J Two", year: null, snippet: "We disagree.", url: "https://pubmed.ncbi.nlm.nih.gov/222/", quotes: ["Others found no association with rejection"] },
      ],
    };
    renderWithQuery(<AnswerBody turn={turn} />);
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toContain("Levels above 1% suggested ABMR [1].");
    expect(screen.getByRole("link", { name: "Source PMID 222" })).toHaveAttribute("href", "#cite-222");
    const sources = screen.getByRole("region", { name: "Sources" });
    const pubmed = within(sources).getByRole("link", { name: /PMID 111/ });
    expect(pubmed).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/111/");
    expect(pubmed).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(sources).getByText("“Levels above 1% suggested ABMR.”")).toBeInTheDocument(); // no quotes: snippet
    expect(within(sources).getByText("Supporting quote, verified in the abstract")).toBeInTheDocument();
    expect(within(sources).getByText("“Others found no association with rejection”")).toBeInTheDocument();
    expect(within(sources).queryByText(/We disagree/)).toBeNull();
  });

  it("marks failed steps and shows the reason", () => {
    const turn: Turn = { ...newTurn("q"), tools: [tool(1, "ran SQL", { ok: false, error: "SQL rejected by the guard: max" })] };
    renderWithQuery(<AnswerBody turn={turn} />);
    expect(screen.getByText("(failed)")).toBeInTheDocument();
    expect(screen.getByText(/SQL rejected by the guard/)).toBeInTheDocument();
  });

  it("shows a refusal instead of an answer", () => {
    renderWithQuery(<AnswerBody turn={{ ...newTurn("q"), refusal: "I can only read data.", text: "I can only read data." }} />);
    expect(screen.getAllByText("I can only read data.")).toHaveLength(1);
  });
});
