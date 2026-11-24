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


def test_values_as_written_are_read_once_and_stored_canonically():
    row = records.validate({"document_kind": "invoice", "party": "A", "reference": "R", "currency": "USD",
                            "document_date": "2026-10-5", "stated_total": "USD 1,234.00", "stated_tax": "$0.00",
                            "tax_basis": "exclusive",
                            "lines": [{"item": "X", "quantity": "1,000.0", "unit": "Nos", "unit_price": "$1.234",
                                       "amount": "1,234.00"}]})
    assert row["exceptions"] == [] and row["status"] == "unreviewed"
    assert row["document_date"] == "2026-10-05" and row["stated_total"] == "1234.00" and row["stated_tax"] == "0.00"
    # Ambiguous or foreign values are kept as written and stay exceptions for a person.
    assert records.stated_number("1,234", "USD") is None              # may be a decimal comma
    assert records.stated_number("$12.00", "EUR") is None              # a dollar amount on a EUR record
    assert records.stated_number("EUR 12.00", "EUR") == Decimal("12.00")


def test_an_unreadable_date_is_undated_and_never_fails_a_report():
    base = {"revision": 1, "document_kind": "invoice", "party": "A", "reference": "R", "currency": "USD",
            "stated_total": "10", "stated_tax": "0", "created_at": "2026-10-20T00:00:00+00:00"}
    rows = [dict(base, record_id="typo", document_date="05/10/2026", status="exception",
                 exceptions=["document_date_unknown"]),
            dict(base, record_id="ok", document_date="2026-10-20", status="accepted")]
    for kind in reports.KINDS:
        out = reports.build(rows, kind, start="2026-10-01", end="2026-11-01",
                            generated_at="2026-10-21T00:00:00+00:00")
        assert out["undated"] == ["typo"]
    assert [r["record_id"] for r in reports.build(rows, "exceptions", start="2026-10-01", end="2026-11-01",
                                                  generated_at="2026-10-21T00:00:00+00:00")["rows"]] == ["typo"]
    invoices = reports.build(rows, "invoices", start="2026-10-01", end="2026-11-01")
    assert invoices["totals_by_currency"]["USD"]["count"] == 1


def test_duplicate_key_ignores_case_punctuation_and_legal_suffix():
    one = {"company": "C", "document_kind": "invoice", "party": "Supplier", "reference": "INV-A"}
    assert records.duplicate_key(one) == records.duplicate_key(dict(one, party="Supplier Ltd.", reference=" inv-a "))
    assert records.duplicate_key(one) != records.duplicate_key(dict(one, reference="INV-B"))
    assert records.duplicate_key(one) != records.duplicate_key(dict(one, party="Other Supplier"))
    assert records.duplicate_key(dict(one, party=None)) is None
