"""Unit tests for the shared Eastmoney list-title enrichment rule.

The default eligibility rule is "trimmed list title length >= 40".  Lengths of
exactly 40 keep the historical truncation trigger; lengths above 40 report the
additive overflow trigger.  Short titles stay opt-in only.
"""

from myresearcher_collector.sources.eastmoney_guba.content_rules import (
    EXPLICIT_SHORT_TITLE_TRIGGER,
    TRUNCATION_OVERFLOW_TRIGGER,
    TRUNCATION_TRIGGER,
    detail_enrichment_trigger,
    normalized_title_length,
)


def test_length_eq_40_keeps_historical_trigger():
    assert detail_enrichment_trigger("x" * 40) == TRUNCATION_TRIGGER


def test_length_gt_40_reports_additive_overflow_trigger():
    for length in (41, 47, 56, 63, 64):
        assert detail_enrichment_trigger("x" * length) == TRUNCATION_OVERFLOW_TRIGGER


def test_length_below_40_is_ineligible_by_default():
    for length in (0, 1, 39):
        assert detail_enrichment_trigger("x" * length) is None


def test_short_titles_are_opt_in_only():
    assert detail_enrichment_trigger("x" * 39) is None
    assert (
        detail_enrichment_trigger("x" * 39, include_short_titles=True)
        == EXPLICIT_SHORT_TITLE_TRIGGER
    )
    # include_short_titles must not change the >=40 behavior.
    assert detail_enrichment_trigger("x" * 40, include_short_titles=True) == TRUNCATION_TRIGGER
    assert (
        detail_enrichment_trigger("x" * 41, include_short_titles=True)
        == TRUNCATION_OVERFLOW_TRIGGER
    )


def test_none_and_whitespace_only_titles_are_ineligible():
    assert detail_enrichment_trigger(None) is None
    assert detail_enrichment_trigger("   ") is None


def test_title_length_is_measured_after_stripping():
    assert normalized_title_length("  " + "x" * 40 + "  ") == 40
    assert detail_enrichment_trigger("  " + "x" * 40) == TRUNCATION_TRIGGER
