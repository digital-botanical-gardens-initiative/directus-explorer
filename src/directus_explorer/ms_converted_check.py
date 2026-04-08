"""Compare Directus MS metadata exports against watcher TSV inventories."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from .ms_metadata import sanitize_column_name


PROFILED_SAMPLE_CODE_COLUMNS = (
    "ms_parent_sample_container_container_id",
    "aliquot_sample_container_container_id",
)
ORIGINAL_SAMPLE_ID_COLUMNS = (
    "original_sample_id",
    "original_sample_container_id",
    "field_sample_id",
    "extraction_parent_sample_container_container_id",
)


@dataclass(frozen=True, slots=True)
class ConvertedMatchRow:
    """Comparison result for one metadata row."""

    ms_data_id: str
    ms_filename: str
    exact_filename_match: bool
    exact_matched_file_name: str | None
    exact_matched_file_path: str | None
    exact_matched_polarity: str | None
    dbgi_sample_code: str | None
    dbgi_id: str | None


@dataclass(frozen=True, slots=True)
class ConvertedMatchSummary:
    """Aggregate counts for one comparison run."""

    metadata_row_count: int
    exact_match_count: int
    no_match_count: int


@dataclass(frozen=True, slots=True)
class ConvertedMatchReport:
    """Full comparison payload."""

    summary: ConvertedMatchSummary
    rows: tuple[ConvertedMatchRow, ...]


@dataclass(frozen=True, slots=True)
class WatcherEntry:
    """Relevant watcher TSV fields for exact matching."""

    file_name: str
    file_path: str
    polarity: str | None
    metadata: dict[str, str | None]


def strip_mzml_suffix(file_name: str) -> str:
    """Return a watcher file name without the trailing mzML extension."""

    if file_name.endswith(".mzML"):
        return file_name[:-5]
    if file_name.endswith(".mzml"):
        return file_name[:-5]
    return file_name


def load_watcher_entries(watcher_tsv_paths: tuple[Path, ...]) -> tuple[WatcherEntry, ...]:
    """Load watcher inventory rows from one or more TSV exports."""

    rows: list[WatcherEntry] = []
    for watcher_tsv_path in watcher_tsv_paths:
        try:
            with watcher_tsv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                _require_columns(
                    path=watcher_tsv_path,
                    fieldnames=reader.fieldnames,
                    required_columns=("file_name", "file_path", "polarity"),
                )
                rows.extend(
                    WatcherEntry(
                        file_name=_require_cell(row, "file_name", watcher_tsv_path),
                        file_path=_require_cell(row, "file_path", watcher_tsv_path),
                        polarity=_optional_cell(row, "polarity"),
                        metadata={key: value for key, value in row.items()},
                    )
                    for row in reader
                )
        except OSError as exc:
            raise ValueError(f"Unable to read watcher TSV {watcher_tsv_path}: {exc}") from exc

    return tuple(rows)


def flatten_watcher_metadata(entry: WatcherEntry | None) -> dict[str, str | None]:
    """Return watcher metadata columns flattened for tabular export."""

    if entry is None:
        return {}
    return {
        sanitize_column_name(f"watcher_{key}"): value
        for key, value in entry.metadata.items()
    }


def compare_metadata_to_watcher(
    metadata_csv_path: Path,
    watcher_tsv_paths: tuple[Path, ...],
) -> ConvertedMatchReport:
    """Compare a Directus metadata export against one or more watcher TSV inventories."""

    watcher_entries = load_watcher_entries(watcher_tsv_paths)
    watcher_by_stem = {strip_mzml_suffix(entry.file_name): entry for entry in watcher_entries}

    try:
        with metadata_csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            _require_columns(
                path=metadata_csv_path,
                fieldnames=reader.fieldnames,
                required_columns=("ms_data_id", "ms_filename"),
            )
            _require_any_column(
                path=metadata_csv_path,
                fieldnames=reader.fieldnames,
                alternative_columns=PROFILED_SAMPLE_CODE_COLUMNS,
                label="profiled sample code",
            )
            _require_any_column(
                path=metadata_csv_path,
                fieldnames=reader.fieldnames,
                alternative_columns=ORIGINAL_SAMPLE_ID_COLUMNS,
                label="original sample id",
            )
            rows = tuple(
                _build_match_row(
                    row,
                    metadata_csv_path,
                    watcher_by_stem,
                )
                for row in reader
            )
    except OSError as exc:
        raise ValueError(f"Unable to read metadata CSV {metadata_csv_path}: {exc}") from exc

    summary = ConvertedMatchSummary(
        metadata_row_count=len(rows),
        exact_match_count=sum(row.exact_filename_match for row in rows),
        no_match_count=sum(not row.exact_filename_match for row in rows),
    )
    return ConvertedMatchReport(summary=summary, rows=rows)


def filter_report(
    report: ConvertedMatchReport,
    *,
    matches_only: bool,
    missing_only: bool,
) -> ConvertedMatchReport:
    """Return a filtered report while keeping summary counts consistent with output rows."""

    if matches_only and missing_only:
        raise ValueError("--matches-only and --missing-only cannot be used together")

    if matches_only:
        rows = tuple(row for row in report.rows if row.exact_filename_match)
    elif missing_only:
        rows = tuple(row for row in report.rows if not row.exact_filename_match)
    else:
        rows = report.rows

    return ConvertedMatchReport(
        summary=ConvertedMatchSummary(
            metadata_row_count=len(rows),
            exact_match_count=sum(row.exact_filename_match for row in rows),
            no_match_count=sum(not row.exact_filename_match for row in rows),
        ),
        rows=rows,
    )


def report_to_json_payload(report: ConvertedMatchReport) -> dict[str, object]:
    """Convert a report into a JSON-serializable payload."""

    return {
        "summary": asdict(report.summary),
        "rows": [asdict(row) for row in report.rows],
    }


def write_report_csv(report: ConvertedMatchReport, output_path: Path) -> None:
    """Write per-row results to a CSV file."""

    fieldnames = (
        "ms_data_id",
        "ms_filename",
        "exact_filename_match",
        "exact_matched_file_name",
        "exact_matched_file_path",
        "exact_matched_polarity",
        "dbgi_sample_code",
        "dbgi_id",
    )
    try:
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in report.rows:
                writer.writerow(asdict(row))
    except OSError as exc:
        raise ValueError(f"Unable to write output CSV {output_path}: {exc}") from exc


def _build_match_row(
    metadata_row: dict[str, str | None],
    metadata_csv_path: Path,
    watcher_by_stem: dict[str, WatcherEntry],
) -> ConvertedMatchRow:
    ms_data_id = _require_cell(metadata_row, "ms_data_id", metadata_csv_path)
    ms_filename = _require_cell(metadata_row, "ms_filename", metadata_csv_path)
    exact_entry = watcher_by_stem.get(ms_filename)

    return ConvertedMatchRow(
        ms_data_id=ms_data_id,
        ms_filename=ms_filename,
        exact_filename_match=exact_entry is not None,
        exact_matched_file_name=exact_entry.file_name if exact_entry is not None else None,
        exact_matched_file_path=exact_entry.file_path if exact_entry is not None else None,
        exact_matched_polarity=exact_entry.polarity if exact_entry is not None else None,
        dbgi_sample_code=_first_non_empty_cell(metadata_row, PROFILED_SAMPLE_CODE_COLUMNS),
        dbgi_id=_first_non_empty_cell(metadata_row, ORIGINAL_SAMPLE_ID_COLUMNS),
    )


def _require_columns(
    *,
    path: Path,
    fieldnames: list[str] | None,
    required_columns: tuple[str, ...],
) -> None:
    if fieldnames is None:
        raise ValueError(f"{path} is missing a header row")

    missing_columns = [column for column in required_columns if column not in fieldnames]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"{path} is missing required columns: {missing}")


def _require_any_column(
    *,
    path: Path,
    fieldnames: list[str] | None,
    alternative_columns: tuple[str, ...],
    label: str,
) -> None:
    assert fieldnames is not None
    if any(column in fieldnames for column in alternative_columns):
        return
    columns = ", ".join(alternative_columns)
    raise ValueError(f"{path} is missing any column for {label}: {columns}")


def _require_cell(row: dict[str, str | None], column: str, path: Path) -> str:
    value = row.get(column)
    if value is None:
        raise ValueError(f"{path} contains a malformed row without {column}")
    return value


def _optional_cell(row: dict[str, str | None], column: str) -> str | None:
    return row.get(column)


def _first_non_empty_cell(
    row: dict[str, str | None],
    columns: tuple[str, ...],
) -> str | None:
    for column in columns:
        value = row.get(column)
        if value:
            return value
    return None
