"""Taxonomic name resolution helpers backed by gnverifier."""

from __future__ import annotations

import csv
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CATALOGUE_OF_LIFE_SOURCE_ID = "1"


class TaxonResolutionError(RuntimeError):
    """Raised when taxonomic name resolution fails."""


@dataclass(frozen=True, slots=True)
class TaxonResolution:
    """One resolved taxon-name result from gnverifier."""

    input_name: str
    canonical_name: str
    taxon_id: str
    scientific_name: str
    matched_name: str
    current_name: str
    synonym: str
    data_source_id: str
    data_source_title: str
    kind: str
    sort_score: str
    match_type: str
    edit_distance: str
    classification_path: str
    error: str

    @property
    def is_catalogue_of_life_match(self) -> bool:
        """Return whether this result is a usable Catalogue of Life match."""

        return (
            self.data_source_id == CATALOGUE_OF_LIFE_SOURCE_ID
            and bool(self.taxon_id)
            and bool(self.canonical_name)
            and not self.error
        )

    @property
    def aggregation_key(self) -> str | None:
        """Return the stable species-level key to use for aggregation."""

        if not self.is_catalogue_of_life_match:
            return None
        return self.taxon_id


class TaxonResolver(Protocol):
    """Protocol for resolving raw taxon names to taxonomic backbone records."""

    def resolve_names(self, names: Iterable[str]) -> Mapping[str, TaxonResolution]:
        """Resolve taxon names and return results keyed by original input name."""


class CatalogueOfLifeTaxonResolver:
    """Resolve names with gnverifier, restricted to Catalogue of Life."""

    def resolve_names(self, names: Iterable[str]) -> Mapping[str, TaxonResolution]:
        """Resolve distinct non-empty names through gnverifier."""

        normalized_by_input = {
            name: normalized
            for name in names
            if (normalized := self._normalize_name_whitespace(name))
        }
        query_names = sorted(set(normalized_by_input.values()))
        if not query_names:
            return {}

        with tempfile.TemporaryDirectory(prefix="directus-explorer-taxonomy-") as tmpdir:
            names_path = Path(tmpdir) / "names.txt"
            names_path.write_text("\n".join(query_names) + "\n", encoding="utf-8")
            result_rows = self._run_gnverifier(names_path)

        return self._align_results(normalized_by_input, result_rows)

    @staticmethod
    def _normalize_name_whitespace(name: str) -> str:
        """Collapse internal whitespace so one taxon name stays one input line."""

        return " ".join(name.split())

    @staticmethod
    def _run_gnverifier(names_path: Path) -> list[dict[str, str]]:
        try:
            completed = subprocess.run(
                [
                    "gnverifier",
                    "-s",
                    CATALOGUE_OF_LIFE_SOURCE_ID,
                    "-f",
                    "csv",
                    "--quiet",
                    str(names_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise TaxonResolutionError(
                "gnverifier is not available on PATH. Install gnverifier to run species summaries."
            ) from exc
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or exc.stdout or str(exc)).strip()
            raise TaxonResolutionError(f"gnverifier failed: {message}") from exc

        reader = csv.DictReader(completed.stdout.splitlines())
        if not reader.fieldnames:
            raise TaxonResolutionError("gnverifier returned no CSV header")
        return [{key: value or "" for key, value in row.items()} for row in reader]

    @staticmethod
    def _align_results(
        normalized_by_input: Mapping[str, str],
        result_rows: list[dict[str, str]],
    ) -> Mapping[str, TaxonResolution]:
        results_by_name: dict[str, dict[str, str]] = {}
        for row in result_rows:
            scientific_name = CatalogueOfLifeTaxonResolver._normalize_name_whitespace(
                row.get("ScientificName") or ""
            )
            if scientific_name:
                results_by_name[scientific_name] = row

        missing = [
            input_name
            for input_name, normalized_name in normalized_by_input.items()
            if normalized_name not in results_by_name
        ]
        if missing:
            examples = ", ".join(repr(name) for name in missing[:5])
            raise TaxonResolutionError(
                "gnverifier results could not be matched back to input names. "
                f"Examples: {examples}"
            )

        return {
            input_name: TaxonResolution(
                input_name=input_name,
                canonical_name=(row.get("MatchedCanonical") or "").strip(),
                taxon_id=(row.get("TaxonId") or "").strip(),
                scientific_name=(row.get("ScientificName") or "").strip(),
                matched_name=(row.get("MatchedName") or "").strip(),
                current_name=(row.get("CurrentName") or "").strip(),
                synonym=(row.get("Synonym") or "").strip(),
                data_source_id=(row.get("DataSourceId") or "").strip(),
                data_source_title=(row.get("DataSourceTitle") or "").strip(),
                kind=(row.get("Kind") or "").strip(),
                sort_score=(row.get("SortScore") or "").strip(),
                match_type=(row.get("MatchType") or "").strip(),
                edit_distance=(row.get("EditDistance") or "").strip(),
                classification_path=(row.get("ClassificationPath") or "").strip(),
                error=(row.get("Error") or "").strip(),
            )
            for input_name, normalized_name in normalized_by_input.items()
            for row in [results_by_name[normalized_name]]
        }
