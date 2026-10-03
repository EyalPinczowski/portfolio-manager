"""Securities table: seeding from the CSV and search (Hebrew / English / symbol)."""

from __future__ import annotations

import csv
from pathlib import Path

from rapidfuzz import fuzz, process
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.models import Security

DEFAULT_SEED = Path(__file__).parent / "data" / "securities_seed.csv"


def seed_securities(db: Session, path: Path | None = None, settings: Settings | None = None) -> int:
    """Insert securities missing from the DB. Idempotent. Returns the number inserted."""
    s = settings or get_settings()
    csv_path = path or (Path(s.seed_csv_path) if s.seed_csv_path else DEFAULT_SEED)
    existing = {sym for sym in db.exec(select(Security.symbol)).all()}
    inserted = 0
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["symbol"] in existing:
                continue
            db.add(
                Security(
                    symbol=row["symbol"],
                    name_en=row["name_en"],
                    name_he=row["name_he"],
                    tase_number=row["tase_number"] or None,
                    asset_type=row["asset_type"],
                    market=row["market"],
                    currency=row["currency"],
                    sector=row["sector"] or "Unknown",
                    country=row["country"] or "Unknown",
                    dual_listing_group=row["dual_listing_group"] or None,
                )
            )
            inserted += 1
    db.commit()
    return inserted


def infer_security(symbol: str) -> Security:
    """Minimal Security for a symbol we do not know (manual add of an unseeded ticker)."""
    sym = symbol.upper()
    if sym.endswith(".TA"):
        return Security(
            symbol=sym,
            name_en=sym,
            asset_type="stock",
            market="TASE",
            currency="ILS",
            country="Israel",
            verified=False,
        )
    if sym.endswith("-USD"):
        return Security(
            symbol=sym,
            name_en=sym,
            asset_type="crypto",
            market="CRYPTO",
            currency="USD",
            sector="Crypto",
            country="Global",
            verified=False,
        )
    return Security(
        symbol=sym, name_en=sym, asset_type="stock", market="US", currency="USD", verified=False
    )


def get_or_create_security(db: Session, symbol: str) -> Security:
    sec = db.get(Security, symbol.upper())
    if sec is None:
        sec = infer_security(symbol)
        db.add(sec)
        db.flush()
    return sec


def search_securities(db: Session, query: str, limit: int = 15) -> list[Security]:
    q = query.strip()
    if not q:
        return []
    all_secs = list(db.exec(select(Security).where(col(Security.verified).is_(True))).all())
    ql = q.lower()
    exact_prefix = [s for s in all_secs if s.symbol.lower().startswith(ql)]
    exact_prefix.sort(key=lambda s: (len(s.symbol), s.symbol))
    out: list[Security] = list(exact_prefix)
    seen = {s.symbol for s in out}
    sub = [
        s
        for s in all_secs
        if s.symbol not in seen and (ql in s.name_en.lower() or (s.name_he and q in s.name_he))
    ]
    out.extend(sub)
    seen.update(s.symbol for s in sub)
    if len(out) < limit:
        choices = {}
        for s in all_secs:
            if s.symbol in seen:
                continue
            choices[(s.symbol, "en")] = s.name_en.lower()
            if s.name_he:
                choices[(s.symbol, "he")] = s.name_he
        by_symbol = {s.symbol: s for s in all_secs}
        for _text, _score, key in process.extract(
            ql, choices, scorer=fuzz.WRatio, limit=limit, score_cutoff=70
        ):
            sym = key[0]
            if sym not in seen:
                out.append(by_symbol[sym])
                seen.add(sym)
    return out[:limit]


def tase_numbers(db: Session) -> dict[str, Security]:
    rows = db.exec(select(Security).where(col(Security.tase_number).is_not(None))).all()
    return {r.tase_number: r for r in rows if r.tase_number}
