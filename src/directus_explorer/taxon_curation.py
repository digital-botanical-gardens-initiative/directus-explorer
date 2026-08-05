"""Pinned Catalogue of Life helpers for human-reviewed taxon curation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

COL_API_BASE_URL = "https://api.checklistbank.org"
COL_DATASET_KEY = 315834
COL_DATASET_VERSION = "2026-07-17 XR"
COL_DATASET_DOI = "10.48580/dgykv"
UNUSABLE_MATCH_TYPES = {"none", "higherrank"}


class ColCurationError(RuntimeError):
    """Raised when the pinned CoL service cannot provide a valid response."""


@dataclass(frozen=True, slots=True)
class ColNameResolution:
    """A review-only resolution of one verbatim name against a pinned CoL release."""

    verbatim_name: str
    parsed_name: str
    parsed_authorship: str
    parsed_rank: str
    parsed_cultivar: str
    parse_status: str
    match: bool
    match_type: str
    matched_usage_id: str
    matched_name: str
    matched_authorship: str
    matched_rank: str
    matched_status: str
    accepted_usage_id: str
    accepted_name: str
    accepted_authorship: str
    accepted_rank: str
    issues: str
    fuzzy_candidates: str
    lookup_name: str = ""

    @property
    def is_usable_match(self) -> bool:
        """Return whether this is a taxon-level match suitable for human review."""

        return (
            self.match
            and self.match_type.lower() not in UNUSABLE_MATCH_TYPES
            and bool(self.accepted_usage_id)
            and bool(self.accepted_name)
        )


class ChecklistBankColClient:
    """Resolve names against one explicitly pinned Catalogue of Life release."""

    def __init__(
        self,
        *,
        dataset_key: int = COL_DATASET_KEY,
        base_url: str = COL_API_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 30,
    ) -> None:
        self.dataset_key = dataset_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", "directus-explorer taxon-curation/0.1"
        )
        self.timeout = timeout

    def validate_pinned_dataset(self) -> dict[str, Any]:
        """Return metadata after verifying that the configured key is the expected release."""

        metadata = self._get_json(f"/dataset/{self.dataset_key}")
        actual = (
            metadata.get("key"),
            metadata.get("version"),
            metadata.get("doi"),
        )
        expected = (COL_DATASET_KEY, COL_DATASET_VERSION, COL_DATASET_DOI)
        if actual != expected:
            raise ColCurationError(
                "Configured ChecklistBank dataset does not match the pinned CoL release: "
                f"expected {expected!r}, received {actual!r}"
            )
        return metadata

    def resolve_name(
        self,
        verbatim_name: str,
        *,
        fuzzy_limit: int = 5,
        kingdom: str | None = "Plantae",
    ) -> ColNameResolution:
        """Parse and match a botanical name, returning review-only fuzzy candidates if needed."""

        parsed = self._get_json(
            "/parser/name",
            params={"q": verbatim_name, "code": "BOTANICAL"},
        )
        match_params: dict[str, str | int] = {
            "scientificName": verbatim_name,
            "verbose": "true",
        }
        if kingdom:
            match_params["kingdom"] = kingdom
        match_result = self._get_json(
            f"/dataset/{self.dataset_key}/match/nameusage",
            params=match_params,
        )

        match_type = _text(match_result.get("type"))
        matched_id = _text(match_result.get("id"))
        usable = bool(match_result.get("match")) and match_type.lower() not in UNUSABLE_MATCH_TYPES
        usage_record: dict[str, Any] = {}
        if usable and matched_id:
            usage_record = self._get_json(
                f"/dataset/{self.dataset_key}/nameusage/{matched_id}"
            )

        matched_usage = usage_record if usage_record else _mapping(match_result.get("usage"))
        accepted_usage = _mapping(usage_record.get("accepted")) or matched_usage
        matched_name = _mapping(matched_usage.get("name"))
        accepted_name = _mapping(accepted_usage.get("name"))

        fuzzy_candidates = ""
        if not usable:
            fuzzy_candidates = self._fuzzy_candidates(
                verbatim_name, limit=fuzzy_limit, kingdom=kingdom
            )

        return ColNameResolution(
            verbatim_name=verbatim_name,
            parsed_name=_text(parsed.get("scientificName")),
            parsed_authorship=_text(parsed.get("authorship")),
            parsed_rank=_text(parsed.get("rank")),
            parsed_cultivar=_text(parsed.get("cultivarEpithet")),
            parse_status="parsed" if parsed.get("parsed") else "unparsed",
            match=usable,
            match_type=match_type,
            matched_usage_id=_text(matched_usage.get("id")),
            matched_name=_text(matched_name.get("scientificName"))
            or _text(matched_usage.get("name")),
            matched_authorship=_text(matched_name.get("authorship"))
            or _text(matched_usage.get("authorship")),
            matched_rank=_text(matched_name.get("rank")) or _text(matched_usage.get("rank")),
            matched_status=_text(matched_usage.get("status")),
            accepted_usage_id=_text(accepted_usage.get("id")) if usable else "",
            accepted_name=(
                _text(accepted_name.get("scientificName"))
                or _text(accepted_usage.get("name"))
                if usable
                else ""
            ),
            accepted_authorship=(
                _text(accepted_name.get("authorship"))
                or _text(accepted_usage.get("authorship"))
                if usable
                else ""
            ),
            accepted_rank=(
                _text(accepted_name.get("rank"))
                or _text(accepted_usage.get("rank"))
                if usable
                else ""
            ),
            issues=json.dumps(match_result.get("issues") or {}, sort_keys=True),
            fuzzy_candidates=fuzzy_candidates,
            lookup_name=verbatim_name,
        )

    def _fuzzy_candidates(
        self, name: str, *, limit: int, kingdom: str | None = "Plantae"
    ) -> str:
        result = self._get_json(
            f"/dataset/{self.dataset_key}/nameusage/search",
            params={
                "q": name,
                "type": "FUZZY",
                "limit": max(limit * 4, 20),
            },
        )
        candidates: list[dict[str, str]] = []
        for wrapper in result.get("result") or []:
            if not isinstance(wrapper, dict) or (kingdom and not _is_kingdom(wrapper, kingdom)):
                continue
            usage = _mapping(wrapper.get("usage"))
            scientific_name = _mapping(usage.get("name"))
            candidates.append(
                {
                    "id": _text(usage.get("id")) or _text(wrapper.get("id")),
                    "name": _text(scientific_name.get("scientificName")),
                    "authorship": _text(scientific_name.get("authorship")),
                    "rank": _text(scientific_name.get("rank")),
                    "status": _text(usage.get("status")),
                }
            )
            if len(candidates) == limit:
                break
        return json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))

    def _get_json(
        self, path: str, *, params: dict[str, str | int] | None = None
    ) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ColCurationError(f"ChecklistBank request failed for {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ColCurationError(f"ChecklistBank returned a non-object response for {path}")
        return payload


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _is_kingdom(wrapper: dict[str, Any], kingdom: str) -> bool:
    classification = wrapper.get("classification")
    if not isinstance(classification, list):
        return False
    return any(
        isinstance(item, dict)
        and _text(item.get("rank")).lower() == "kingdom"
        and _text(item.get("name")).casefold() == kingdom.casefold()
        for item in classification
    )
