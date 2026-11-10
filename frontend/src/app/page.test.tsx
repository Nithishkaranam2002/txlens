import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import Home from "./page";

test("home page shows the product name and synthetic-data notice", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "TxLens" })).toBeDefined();
  expect(screen.getByText(/Synthetic data only/)).toBeDefined();
});
