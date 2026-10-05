import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Modal } from "@/components/Modal";

function Host({ onEsc }: { onEsc: () => void }) {
  const [open, setOpen] = useState(false);
  const [n, setN] = useState(0);
  return (
    <div>
      <button onClick={() => setOpen(true)}>open</button>
      <button onClick={() => setN(n + 1)}>rerender</button>
      {open && (
        <Modal title="T" onClose={() => { onEsc(); setOpen(false); }}>
          <button>one</button>
          <button>two</button>
        </Modal>
      )}
    </div>
  );
}

describe("Modal", () => {
  it("focuses once on mount, does not steal focus on re-render, traps Tab and restores focus on close", () => {
    const esc = vi.fn();
    render(<Host onEsc={esc} />);
    const opener = screen.getByText("open");
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("dialog")).toHaveFocus();
    screen.getByText("two").focus();
    fireEvent.click(screen.getByText("rerender"));
    screen.getByText("two").focus();
    expect(screen.getByText("two")).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(screen.getByText("one")).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(screen.getByText("two")).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(esc).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });
});
