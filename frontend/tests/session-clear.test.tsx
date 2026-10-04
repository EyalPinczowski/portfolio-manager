import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import useSWR, { mutate } from "swr";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
import { api } from "@/lib/api";
import { PORTFOLIO_CHOICE_KEY } from "@/lib/session";
import { loadPortfolioChoice, savePortfolioChoice } from "@/lib/hooks";

function Probe() {
  const { data } = useSWR<{ owner: string }>("secret-key", null);
  return <p data-testid="probe">{data?.owner ?? "empty"}</p>;
}

describe("client state is cleared on logout and login", () => {
  afterEach(() => window.localStorage.clear());

  it.each(["logout", "login"] as const)("%s drops the SWR cache and the stored portfolio", async (which) => {
    savePortfolioChoice(7);
    render(<Probe />);
    await act(async () => { await mutate("secret-key", { owner: "user A" }, { revalidate: false }); });
    expect(screen.getByTestId("probe")).toHaveTextContent("user A");
    expect(loadPortfolioChoice()).toBe(7);

    await act(async () => { await (which === "logout" ? api.logout() : api.login({ email: "b@example.com", password: "x" })); });

    expect(screen.getByTestId("probe")).toHaveTextContent("empty");
    expect(window.localStorage.getItem(PORTFOLIO_CHOICE_KEY)).toBeNull();
    expect(loadPortfolioChoice()).toBeNull();
  });
});
