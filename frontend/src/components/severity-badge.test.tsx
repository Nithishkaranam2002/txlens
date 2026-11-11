import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SeverityBadge } from "./severity-badge";

describe("SeverityBadge", () => {
  it.each(["high", "medium", "low"] as const)("names the %s severity in text, not colour alone", (severity) => {
    render(<SeverityBadge severity={severity} />);
    expect(screen.getByText(new RegExp(severity, "i"))).toHaveTextContent(new RegExp(`${severity} severity`, "i"));
  });
});
