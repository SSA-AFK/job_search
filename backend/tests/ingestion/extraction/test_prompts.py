from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.prompts import MAX_PROMPT_CHARS, build_prompt


def raw_document(external_id: str, text: str) -> RawDocument:
    return RawDocument(
        provider="test",
        external_id=external_id,
        url="https://example.test/source",
        title="Source",
        text=text,
        published_at=None,
    )


def test_prompt_never_exceeds_total_character_budget() -> None:
    _, prompt = build_prompt(
        "discover",
        [raw_document(f"doc-{index}", "x" * 7_930) for index in range(3)],
    )

    assert len(prompt) <= MAX_PROMPT_CHARS


def test_duplicate_evidence_ids_are_disambiguated_until_unique() -> None:
    evidence_ids, prompt = build_prompt(
        "discover",
        [
            raw_document("foo", "first"),
            raw_document("foo-3", "second"),
            raw_document("foo", "third"),
        ],
    )

    assert evidence_ids == {"foo", "foo-3", "foo-3-3"}
    assert prompt.count("[evidence:foo-3]") == 1
