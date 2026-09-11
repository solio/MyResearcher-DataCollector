"""Shared content provenance and detail-enrichment rules for Eastmoney Guba."""

from __future__ import annotations

from typing import Any, Mapping


TRUNCATED_LIST_TITLE_LENGTH = 40
LIST_TITLE_CONTENT_SOURCE = "list_title"
DETAIL_BODY_CONTENT_SOURCE = "detail_body"
TRUNCATION_TRIGGER = "list_title_length_eq_40"
TRUNCATION_OVERFLOW_TRIGGER = "list_title_length_gt_40"
EXPLICIT_SHORT_TITLE_TRIGGER = "explicit_short_title"


def normalized_title_length(title: str | None) -> int:
    """Return the source title length used by both persistence paths."""
    return len((title or "").strip())


def detail_enrichment_trigger(
    title: str | None,
    *,
    include_short_titles: bool = False,
) -> str | None:
    """Return why a list title is eligible for detail enrichment, if at all.

    The default eligibility rule is "title length is at least 40" (>=40), i.e.
    any list title at or beyond the 40-character truncation length is treated as
    suspected truncated. ``length == 40`` keeps its historical trigger identity;
    ``length > 40`` reports the additive overflow trigger. Titles shorter than
    40 are only requested when ``include_short_titles`` is explicitly set.
    """
    length = normalized_title_length(title)
    if length > TRUNCATED_LIST_TITLE_LENGTH:
        return TRUNCATION_OVERFLOW_TRIGGER
    if length == TRUNCATED_LIST_TITLE_LENGTH:
        return TRUNCATION_TRIGGER
    if include_short_titles and length < TRUNCATED_LIST_TITLE_LENGTH:
        return EXPLICIT_SHORT_TITLE_TRIGGER
    return None


def list_title_metadata(
    metadata: Mapping[str, Any],
    title: str | None,
) -> dict[str, Any]:
    """Label a list-only observation without representing its title as a body."""
    result = dict(metadata)
    length = normalized_title_length(title)
    result["content_source"] = LIST_TITLE_CONTENT_SOURCE
    result["list_title_length"] = length
    result["list_title_suspected_truncated"] = (
        length == TRUNCATED_LIST_TITLE_LENGTH
    )
    return result


def detail_body_metadata(
    metadata: Mapping[str, Any],
    *,
    title: str | None,
    trigger: str | None = None,
    from_observation_version: int | None = None,
) -> dict[str, Any]:
    """Label a detail-backed item and retain its list-title provenance."""
    result = dict(metadata)
    result["content_source"] = DETAIL_BODY_CONTENT_SOURCE
    result.setdefault("list_title_length", normalized_title_length(title))
    result.setdefault(
        "list_title_suspected_truncated",
        normalized_title_length(title) == TRUNCATED_LIST_TITLE_LENGTH,
    )
    if trigger is not None:
        result["detail_enrichment_trigger"] = trigger
    if from_observation_version is not None:
        result["detail_enrichment_from_observation_version"] = from_observation_version
    return result
