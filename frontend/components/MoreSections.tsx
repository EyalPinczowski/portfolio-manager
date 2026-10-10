"use client";
import { useId, useState } from "react";

export interface MoreItem { id: string; title: string; body: React.ReactNode; defaultOpen?: boolean }

/** One collapsible section. A real button (Enter/Space work), aria-expanded/aria-controls; the body is not rendered while closed. */
export function MoreSection({ title, children, defaultOpen = false, id }: { title: string; children: React.ReactNode; defaultOpen?: boolean; id?: string }) {
  const auto = useId();
  const uid = id ?? auto;
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-line first:border-t-0" data-testid={`more-${uid}`}>
      <h3>
        <button
          type="button"
          className="flex min-h-11 w-full items-center justify-between gap-2 py-2 text-start font-semibold"
          aria-expanded={open}
          aria-controls={`more-body-${uid}`}
          onClick={() => setOpen((v) => !v)}
        >
          <span>{title}</span>
          <span aria-hidden="true" className="text-muted">{open ? "−" : "+"}</span>
        </button>
      </h3>
      {open && <div id={`more-body-${uid}`} className="space-y-3 pb-3">{children}</div>}
    </div>
  );
}

/** A stack of collapsed sections (each toggles on its own). */
export function MoreSections({ items, label }: { items: MoreItem[]; label?: string }) {
  return (
    <section className="card !py-1" aria-label={label}>
      {items.map((it) => <MoreSection key={it.id} id={it.id} title={it.title} defaultOpen={it.defaultOpen}>{it.body}</MoreSection>)}
    </section>
  );
}
