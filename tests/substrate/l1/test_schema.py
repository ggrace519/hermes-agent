"""L1 schema dataclasses + entity-type normalisation."""

from __future__ import annotations

from substrate.l1.schema import (
    ENTITY_TYPES,
    ParsedEntity,
    ParserResult,
    normalise_entity_type,
)


def test_normalise_known_types_pass_through():
    for t in ENTITY_TYPES:
        assert normalise_entity_type(t) == t


def test_normalise_lowercases_and_strips():
    assert normalise_entity_type("Person") == "person"
    assert normalise_entity_type("  PROJECT ") == "project"


def test_normalise_unknown_becomes_other():
    assert normalise_entity_type("spaceship") == "other"
    assert normalise_entity_type("") == "other"
    assert normalise_entity_type(None) == "other"


def test_parser_result_is_empty():
    assert ParserResult().is_empty is True
    assert ParserResult(entities=[ParsedEntity(name="x", entity_type="concept")]).is_empty is False
