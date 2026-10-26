"""Live taxon worklist classification; fixtures require no Directus connection."""

import pytest

from directus_explorer.taxon_curation_worklist import build_worklist

VERSION = "2026-07-17 XR"
PROJECT = "bg-unifr"


def _row(record_id, name="Drosera capensis", **updates):
    row = {
        "id": record_id,
        "sample_id": f"sample-{record_id}",
        "qfield_project": PROJECT,
        "taxon_name_unified": name,
        "taxon_name": "original specimen name",
        "sample_name": "field label",
        "observation_notes": {"habitat": "wet rock", "photos": ["A.jpg"]},
        "taxon_col_id": None,
        "taxon_ref": None,
        "taxon_resolution_status": None,
        "taxon_backbone_version": None,
    }
    row.update(updates)
    return row


def _audit(row, **updates):
    audit = {
        "field_data_id": str(row["id"]),
        "sample_id": row["sample_id"],
        "project": row["qfield_project"],
        "stored_taxon_name_unified": row["taxon_name_unified"] or "",
        "source_taxon_field_conflict": "",
    }
    audit.update(updates)
    return audit


def test_project_with_26_names_and_nine_source_conflicts_needs_links():
    rows = [_row(index, name=f"Species {index:02}") for index in range(26)]
    audits = [_audit(row, source_taxon_field_conflict="true" if index < 9 else "")
              for index, row in enumerate(rows)]
    audits.append({"project": "another-project", "field_data_id": "elsewhere"})
    result = build_worklist(audits, rows, [], PROJECT, VERSION)
    assert result["projects"] == ["another-project", PROJECT]
    assert (
        result["pending_count"], result["complete_count"], result["project_count"]
    ) == (26, 0, 26)
    assert {record["state"] for record in result["records"]} == {"pending_link"}
    assert result["records"][0]["raw"] == rows[0]
    assert result["records"][0]["raw"]["observation_notes"]["photos"] == ["A.jpg"]

    taxa = []
    for index, row in enumerate(rows):
        col_id = f"COL-{index}"
        row.update(taxon_col_id=col_id, taxon_ref={"id": 100 + index},
                   taxon_backbone_version=VERSION, taxon_resolution_status="accepted")
        taxa.append({"id": 100 + index, "col_id": col_id})
    result = build_worklist(audits, rows, taxa, PROJECT, VERSION)
    assert (
        result["pending_count"], result["complete_count"], result["project_count"]
    ) == (0, 26, 26)
    assert {record["state"] for record in result["records"]} == {"complete"}


def test_missing_name_and_reviewed_unresolved_are_distinct():
    missing = _row(1, name=None, taxon_resolution_status="reviewed_unresolved",
                   taxon_backbone_version=VERSION)
    resolved = _row(2, name="Unknown seedling", taxon_resolution_status="reviewed_unresolved",
                    taxon_backbone_version=VERSION)
    no_version = _row(3, name="Mystery moss", taxon_resolution_status="reviewed_unresolved")
    has_col_id = _row(4, name="Unknown tree", taxon_col_id="COL-X",
                      taxon_resolution_status="reviewed_unresolved", taxon_backbone_version=VERSION)
    result = build_worklist([_audit(row) for row in (missing, resolved, no_version, has_col_id)],
                            [missing, resolved, no_version, has_col_id], [], PROJECT, VERSION)
    assert {record["raw"]["id"]: record["state"] for record in result["records"]} == {
        1: "missing_name", 2: "complete", 3: "pending_link", 4: "pending_link",
    }
    assert result["pending_count"] == 3


@pytest.mark.parametrize("updates,taxa", [
    ({"taxon_ref": 101}, [{"id": 101, "col_id": "DIFFERENT"}]),
    ({"taxon_ref": 999}, [{"id": 101, "col_id": "COL-X"}]),
    ({"taxon_ref": None}, [{"id": 101, "col_id": "COL-X"}]),
    ({"taxon_ref": {"id": 101}, "taxon_backbone_version": "older"},
     [{"id": 101, "col_id": "COL-X"}]),
])
def test_col_id_without_matching_taxa_reference_and_version_remains_pending(updates, taxa):
    row = _row(1, **{"taxon_col_id": "COL-X", "taxon_backbone_version": VERSION, **updates})
    result = build_worklist([_audit(row)], [row], taxa, PROJECT, VERSION)
    assert result["records"][0]["state"] == "pending_link"
    assert result["pending_count"] == 1


def test_grouping_sort_uses_normalized_name_then_sample_id():
    rows = [_row(3, name="  R osa ", sample_id="b"),
            _row(2, name="r osa", sample_id="a"),
            _row(1, name="Acer", sample_id="z")]
    result = build_worklist([_audit(row) for row in rows], rows, [], PROJECT, VERSION)
    assert [record["raw"]["id"] for record in result["records"]] == [1, 2, 3]


@pytest.mark.parametrize("audit_rows,field_rows", [
    (lambda row: [_audit(row)], lambda row: [_row(1, "Changed name")]),
    (lambda row: [_audit(row, sample_id="different")], lambda row: [row]),
    (lambda row: [_audit(row)], lambda row: []),
    (lambda row: [], lambda row: [row]),
    (lambda row: [_audit(row), _audit(row)], lambda row: [row]),
    (lambda row: [_audit(row)], lambda row: [row, row]),
    (lambda row: [_audit(row, project="other")], lambda row: [row]),
])
def test_changed_or_unmatched_project_rows_require_refresh(audit_rows, field_rows):
    row = _row(1)
    with pytest.raises(ValueError, match="Refresh again"):
        build_worklist(audit_rows(row), field_rows(row), [], PROJECT, VERSION)


def test_no_rows_in_selected_project_preserves_project_options():
    other = _row(10, qfield_project="another-project")
    result = build_worklist([_audit(other)], [other], [], PROJECT, VERSION)
    assert result == {"projects": ["another-project"], "records": [], "pending_count": 0,
                      "complete_count": 0, "project_count": 0}
