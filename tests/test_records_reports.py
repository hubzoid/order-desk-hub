from __future__ import annotations

import copy
import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from desk import records, reports

ORACLE = json.loads((Path(__file__).parent / "fixtures/report_oracles.json").read_text())


def report(kind):
    f = ORACLE["filters"]
    return reports.build(ORACLE["records"], kind, start=f["start"], end=f["end_exclusive"],
                         tz=f["timezone"], generated_at=f["generated_at"])


def assert_numbers(actual, expected):
    assert actual.keys() == expected.keys()
    for k, value in expected.items():
        if isinstance(value, dict):
            assert_numbers(actual[k], value)
        elif isinstance(value, str):
            assert Decimal(actual[k]) == Decimal(value)
        else:
            assert actual[k] == value


def test_financial_reports_against_independently_committed_oracle():
    expected = ORACLE["expected"]
    for kind, prefix in (("orders", "order"), ("invoices", "invoice")):
        out = report(kind)
        assert_numbers(out["totals_by_currency"], expected[f"{prefix}_totals_by_currency"])
        assert sorted(out["revisions"]) == sorted(expected[f"accepted_{prefix}_revisions"])
        assert set(out["excluded"]) == set(expected["excluded_from_accepted_totals"])
    assert report("orders")["converted_quantities"] == expected["converted_quantities"]


def test_register_and_exception_aging_against_independent_oracle():
    expected = ORACLE["expected"]
    register = reports.build(ORACLE["records"], "register")
    assert [r["record_id"] for r in register["rows"]] == expected["unfiltered_register_latest_ids"]
    csv_rows = list(csv.DictReader(io.StringIO(reports.csv_text(register))))
    for row in csv_rows:
        if row["record_id"] in expected["spreadsheet_safe_party"]:
            assert row["party"] == expected["spreadsheet_safe_party"][row["record_id"]]
    exceptions = report("exceptions")
    assert {r["record_id"]: r["age_seconds"] for r in exceptions["rows"]} == expected["exception_ages_seconds"]
    assert exceptions["undated"] == expected["undated_exception_ids"]


def test_timestamp_filter_does_not_reinterpret_source_calendar_dates():
    e = ORACLE["expected"]["timestamp_boundary"]
    assert reports.in_period(e["timestamp"], "2026-10-01", "2026-11-01", "Asia/Kolkata") == e["in_october_Asia/Kolkata"]
    assert reports.in_period(e["timestamp"], "2026-10-01", "2026-11-01", "UTC") == e["in_october_UTC"]
    assert not reports.in_period(e["source_date_only"], "2026-10-01", "2026-11-01", "Asia/Kolkata")


def test_deterministic_validation_rejects_bad_totals_unknowns_credits_and_rounding():
    for fixture in ORACLE["records"]:
        row = records.validate(dict(fixture, requires_reference=False))
        assert row["status"] == fixture["status"], fixture["record_id"]
    rounding = next(r for r in ORACLE["records"] if r["record_id"] == "ROUND1")
    reviewed = records.validate(dict(rounding, requires_reference=False), records.RecordRules(rounding="half_up"))
    assert not reviewed["exceptions"]
    assert records.number(float("nan")) is None
    assert records.number("Infinity") is None


def test_zero_lines_do_not_become_an_accepted_zero_total():
    row = records.validate({"document_kind": "order", "party": "A", "reference": "R", "currency": "USD",
                            "document_date": "2026-10-20", "lines": [], "stated_total": "0", "stated_tax": "0",
                            "tax_basis": "exclusive", "status": "accepted"})
    assert row["status"] == "exception"
    assert "lines_unknown" in row["exceptions"]


def test_rules_refuse_conflicting_aliases_and_untrusted_source_paths(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge/source.md").write_text("Owner rule")
    rules = records.RecordRules(aliases=[records.Alias(source="x", target="A", source_ref="knowledge/source.md"),
                                        records.Alias(source="X", target="B", source_ref="knowledge/source.md")])
    import yaml
    (tmp_path / records.RULE_FILE).write_text(yaml.safe_dump(rules.model_dump()))
    with pytest.raises(ValueError, match="conflicting alias"):
        records.load_rules(tmp_path)
