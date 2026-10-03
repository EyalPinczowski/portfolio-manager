"""The personal-data scrubber: its fixture corpus, the User-table names and the no-bypass guarantee."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.config import Settings
from app.llm import BaseLLMProvider, GeminiProvider, GroqProvider, LLMRequest
from app.llm.fakes import FakeLLMProvider
from app.llm.scrub import PersonalDataScrubber, names_from_emails, user_names_from_db
from app.models import User
from tests.fixtures.scrub_corpus import CORPUS, USER_EMAILS, Case

S = Settings(_env_file=None)


def scrubber() -> PersonalDataScrubber:
    return PersonalDataScrubber(S, names=lambda: names_from_emails(USER_EMAILS))


def test_the_corpus_is_big_and_covers_every_rule() -> None:
    assert len(CORPUS) >= 40
    assert len({c.id for c in CORPUS}) == len(CORPUS)
    kinds = {c.kind for c in CORPUS}
    assert {"email", "digits", "phone", "account", "token", "name", "keep", "mixed"} <= kinds
    keeps = [c for c in CORPUS if c.kind == "keep"]
    assert any("1,234.56" in c.text for c in keeps)  # prices are not masked
    assert any(any("֐" <= ch <= "׿" for ch in c.text) for c in keeps)  # Hebrew survives


@pytest.mark.parametrize("case", CORPUS, ids=[c.id for c in CORPUS])
def test_corpus(case: Case) -> None:
    result = scrubber().scrub(case.text)
    assert result.text == case.expected
    assert result.changed is (case.text != case.expected)


def test_scrubbing_is_idempotent_on_the_whole_corpus() -> None:
    sc = scrubber()
    for case in CORPUS:
        once = sc.scrub(case.text).text
        assert sc.scrub(once).text == once, case.id


def test_counts_name_the_rules_not_the_data() -> None:
    res = scrubber().scrub("dana.levi@example.com 050-1234567 12-345678 1234567")
    assert res.counts == {"email": 1, "phone": 1, "account": 1, "number": 1}
    assert "dana" not in repr(res.counts)


def test_digit_run_length_is_config() -> None:
    strict = PersonalDataScrubber(
        Settings(_env_file=None, llm_scrub_min_digit_run=4), names=lambda: []
    )
    assert strict.scrub("code 1234").text == "code [number]"
    assert scrubber().scrub("code 1234").text == "code 1234"


def test_names_from_emails() -> None:
    assert names_from_emails(["dana.levi99@x.com", "ab@x.com"]) == {"dana.levi99", "dana", "levi"}
    assert names_from_emails(["eyalpin2002@gmail.com"]) == {"eyalpin2002", "eyalpin"}


def test_names_come_from_the_user_table(db: Session) -> None:
    db.add(User(email="shira.mizrahi@example.com", password_hash="x"))
    db.commit()
    assert {"shira", "mizrahi"} <= user_names_from_db()
    sc = PersonalDataScrubber(S)  # default source: the User table
    assert sc.scrub("Shira Mizrahi wants to sell").text == "[user] [user] wants to sell"


def test_an_unreadable_user_table_still_scrubs_the_other_rules(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.db as appdb

    def boom() -> Session:
        raise RuntimeError("db down")

    monkeypatch.setattr(appdb, "new_session", boom)
    assert PersonalDataScrubber(S).scrub("a@b.co 1234567").text == "[email] [number]"


# ---------------------------------------------------------------- inside the provider
def test_the_provider_base_class_scrubs_before_sending() -> None:
    fake = FakeLLMProvider(["{}"], scrubber=scrubber())
    fake.complete(
        LLMRequest(
            role="t",
            system="user dana.levi@example.com",
            prompt="call 050-1234567 re AAPL 1,234.56",
        )
    )
    sent = fake.seen[0]
    assert sent.system == "user [email]"
    assert sent.prompt == "call [phone] re AAPL 1,234.56"


def _all_subclasses(cls: type) -> set[type]:
    out: set[type] = set()
    for sub in cls.__subclasses__():
        out |= {sub} | _all_subclasses(sub)
    return out


def test_no_provider_overrides_complete() -> None:
    subs = _all_subclasses(BaseLLMProvider)
    assert {FakeLLMProvider, GeminiProvider, GroqProvider} <= subs
    for cls in subs:
        assert "complete" not in cls.__dict__, f"{cls.__name__} must implement _send, not complete"
    assert getattr(BaseLLMProvider.complete, "__final__", False) is True


def test_a_role_cannot_skip_the_scrubber_through_structured_call(env: None) -> None:
    from pydantic import BaseModel

    from app.llm.structured import structured_call

    class Out(BaseModel):
        ok: bool

    fake = FakeLLMProvider(['{"ok": true}'], scrubber=scrubber())
    res = structured_call(
        role="news",
        model_cls=Out,
        system="s",
        prompt="owner dana.levi@example.com id 99887766",
        template=lambda: Out(ok=False),
        providers=[fake],
        settings=S,
    )
    assert res.source == "llm"
    assert "@" not in fake.seen[0].prompt and "99887766" not in fake.seen[0].prompt
