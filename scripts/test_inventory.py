#!/usr/bin/env python3
"""Regenerate docs/testing.md from the real test suites (backend: collection only; frontend: a vitest run, read from its JSON report).

Usage: python scripts/test_inventory.py
The section between the PLANNED markers in docs/testing.md is hand-maintained and preserved.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "testing.md"
BEGIN = "<!-- PLANNED:BEGIN (hand-maintained; the script keeps this block) -->"
END = "<!-- PLANNED:END -->"

# (area, regex on file basename) - first match wins.
BACKEND_AREAS: list[tuple[str, str]] = [
    ("Mock contract", r"^test_openapi_contract"),
    ("Launch gate / verdict contract", r"launch_gate|verdict|paper_|outbound_gate"),
    ("Money / valuation", r"money|agorot|yahoo_currency|quote_fallback|performance|portfolio_expectation|horizon_table"),
    ("Importer / OCR", r"import|meitav|image_budget|ocr"),
    ("Auth / security", r"auth|security|login|ops_|compose_prod|slim_image|llm_scrub"),
    ("Exit levels", r"exit_levels"),
    ("Screener", r"screener"),
    ("Analyze / committee / LLM", r"analyze|committee|llm_|explanation"),
    ("Post-mortem", r"postmortem"),
    ("Backtest", r"backtest"),
    ("Settings / Telegram / admin", r"settings|telegram|admin|weekly_review|user_lists"),
    ("Track record", r"track_record"),
    ("X-ray rules", r"xray"),
    ("Funds / dividends", r"gemelnet|dividends"),
    ("RAG", r"^test_rag"),
    ("Migrations / database", r"migrations|postgres|append_only"),
    ("Scheduler", r"scheduler"),
    ("API scoping / portfolio API", r"api_|holding_create"),
    ("Scoring / signals", r"signals|indicators|combine_risk|provider_protocols"),
    ("Live providers", r"test_live"),
    ("CLI / seed", r"cli_seed"),
]
FRONTEND_AREAS: list[tuple[str, str]] = [
    ("Mock contract", r"mock-contract|settings-mock"),
    ("i18n / RTL", r"i18n"),
    ("Importer / OCR", r"import|ocr|meitav|screenshot"),
    ("Auth / security", r"login|turnstile|security|proxy|session"),
    ("Exit levels", r"exit-levels"),
    ("Analyze", r"analyze"),
    ("Post-mortem", r"postmortem"),
    ("Track record", r"track-record"),
    ("X-ray rules", r"xray"),
    ("Funds / dividends", r"funds|dividends"),
    ("Settings", r"settings"),
    ("Screens / components", r".*"),
]


def area_of(path: str, table: list[tuple[str, str]]) -> str:
    base = Path(path).name
    for area, rx in table:
        if re.search(rx, base):
            return area
    return "Other"


def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def collect_backend(marker: str | None) -> tuple[list[str], str | None]:
    py = ROOT / "backend" / ".venv" / "bin" / "pytest"
    cmd = [str(py) if py.exists() else "pytest", "--collect-only", "-q"]
    cmd += ["-m", marker if marker is not None else "not live"]
    code, out, err = run(cmd, ROOT / "backend")
    ids = [ln.strip() for ln in out.splitlines() if "::" in ln and not ln.startswith(" ")]
    problem = None
    if code not in (0, 5):
        problem = f"pytest exited {code}: {(err or out)[-300:]}"
    return ids, problem


def collect_frontend() -> tuple[list[tuple[str, str]], str | None]:
    # `vitest list` misses tests generated in loops, so read the JSON report of a real run instead.
    report = ROOT / "frontend" / "node_modules" / ".vitest-inventory.json"
    code, out, err = run(
        ["npx", "vitest", "run", "--reporter=json", f"--outputFile={report}"], ROOT / "frontend"
    )
    try:
        data = json.loads(report.read_text())
        res = [
            (r["name"], a["fullName"]) for r in data["testResults"] for a in r["assertionResults"]
        ]
        report.unlink(missing_ok=True)
        return [(str(Path(f).relative_to(ROOT)) if f.startswith("/") else f, n) for f, n in res], None
    except Exception as exc:  # fall back to grepping it()/test() names
        res = []
        for f in sorted((ROOT / "frontend" / "tests").glob("*.test.ts*")):
            for m in re.finditer(r"""\b(?:it|test)\(\s*(['"`])(.+?)\1""", f.read_text()):
                res.append((str(f.relative_to(ROOT)), m.group(2)))
        return res, f"vitest list --json failed ({exc}); used grep fallback"


def group(items: list[tuple[str, str]], table) -> "OrderedDict[str, dict]":
    g: dict[str, dict] = {}
    for f, name in items:
        a = area_of(f, table)
        d = g.setdefault(a, {"files": {}, "n": 0})
        d["files"].setdefault(f, []).append(name)
        d["n"] += 1
    return OrderedDict(sorted(g.items(), key=lambda kv: kv[0]))


def render_group(title: str, g: "OrderedDict[str, dict]") -> list[str]:
    lines = [f"## {title}", "", "| Area | Tests | Files |", "|---|---:|---:|"]
    for a, d in g.items():
        lines.append(f"| {a} | {d['n']} | {len(d['files'])} |")
    lines.append("")
    for a, d in g.items():
        lines.append(f"<details><summary><b>{html.escape(a)}</b> - {d['n']} tests in {len(d['files'])} files</summary>")
        lines.append("")
        for f, names in sorted(d["files"].items()):
            lines.append(f"- `{f}` ({len(names)})")
            seen: dict[str, int] = {}
            for n in names:
                seen[n] = seen.get(n, 0) + 1
            shown = [f"{k} (x{v})" if v > 1 else k for k, v in seen.items()]
            lines.append("  <details><summary>names</summary>")
            lines.append("")
            lines += [f"  - {s.replace('|', '/')}" for s in shown]
            lines.append("")
            lines.append("  </details>")
        lines += ["", "</details>", ""]
    return lines


PLANNED_DEFAULT = f"""{BEGIN}
(seed list is in the file; edit here)
{END}"""


def main() -> int:
    old = OUT.read_text() if OUT.exists() else ""
    m = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), old, re.S)
    planned = m.group(0) if m else PLANNED_DEFAULT

    problems: list[str] = []
    be, p1 = collect_backend(None)
    live, p2 = collect_backend("live")
    fe, p3 = collect_frontend()
    problems += [p for p in (p1, p2, p3) if p]

    be_items = [(i.split("::")[0], i.split("::", 1)[1]) for i in be]
    live_items = [(i.split("::")[0], i.split("::", 1)[1]) for i in live]
    bg, lg, fg = group(be_items, BACKEND_AREAS), group(live_items, BACKEND_AREAS), group(fe, FRONTEND_AREAS)

    L: list[str] = [
        "# Testing",
        "",
        "Generated by `python scripts/test_inventory.py` (backend: collection only; frontend: a vitest run, read from its JSON report). "
        "Edit only the *Planned tests* block; the rest is overwritten.",
        "",
        f"Generated: {date.today().isoformat()}",
        "",
        f"**Totals:** backend {len(be_items)} (run by default) + {len(live_items)} live (skipped by default) · "
        f"frontend {len(fe)} · **{len(be_items) + len(fe)} runnable now**",
        "",
        "## Run everything",
        "",
        "```bash",
        "cd backend && .venv/bin/pytest                 # default suite (live tests skipped)",
        "cd backend && .venv/bin/pytest -m live         # live provider tests (needs network)",
        "cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app",
        "cd frontend && npm run lint && npx tsc --noEmit && npx vitest run && npm run build",
        "python scripts/test_inventory.py               # regenerate this page",
        "```",
        "",
        "Per-phase rule: run all of the above, regenerate this page, add tests for what the phase changed, "
        "and move items from *Planned tests* into the suite as they become runnable (CLAUDE.md, Workflow rule 5).",
        "",
    ]
    if problems:
        L += ["> **Collection problems:** " + "; ".join(problems), ""]
    L += render_group("Backend (pytest, runs by default)", bg)
    L += render_group("Backend live tests (live, skipped by default)", lg) if lg else []
    L += render_group("Frontend (vitest)", fg)
    L += ["## Planned tests (not yet written or not yet runnable)", "", planned, ""]
    OUT.write_text("\n".join(L))
    print(f"backend {len(be_items)}, live {len(live_items)}, frontend {len(fe)}; wrote {OUT}")
    for t, g in (("backend", bg), ("live", lg), ("frontend", fg)):
        for a, d in g.items():
            print(f"  {t}: {a}: {d['n']}")
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
