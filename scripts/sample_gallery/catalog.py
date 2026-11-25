"""Join Directus field observations to the project-scoped Nextcloud photo archive."""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import NotRequired, TypedDict


class Photo(TypedDict):
    source: str
    label: str


class Sample(TypedDict):
    code: str
    project: str
    metadata: Mapping[str, object] | None
    photos: list[Photo]
    missing: list[str]


class Catalog(TypedDict):
    samples: list[Sample]
    generated_at: NotRequired[str]


_CODE_TOKEN = re.compile(r"(?<![^\W_])[a-z][a-z0-9]*_[0-9]+[a-z]*(?![^\W_])")
_ARCHIVE_CODE = re.compile(
    r"(?:absi|chec|dbgi|drog|emi|fibl|fung|grad|mcdn|meda|mnsl|tp24|vine)_[0-9]{6}"
)


def _normalized_name(name: str) -> str:
    stem, separator, extension = name.rpartition(".")
    # QField's pictures_renamer drops punctuation after replacing spaces with underscores.
    stem = re.sub(r"[^\w\s]", "", stem.replace(" ", "_"))
    stem = re.sub(r"_+", "_", stem).strip("_")
    return (stem + separator + extension).casefold()


class _Archive:
    def __init__(self, paths: Iterable[str], aliases: Mapping[str, Mapping[str, object]]) -> None:
        targets = {}
        for original, alias in aliases.items():
            renamed = alias.get("renamed_rel")
            if isinstance(renamed, str) and original.split("/")[:-1] == renamed.split("/")[:-1]:
                targets[original] = renamed

        renditions: dict[str, set[str]] = defaultdict(set)
        for path in paths:
            parts = path.split("/")
            if len(parts) < 3 or parts[0] not in {"pictures", "pictures_raw"}:
                continue
            if any(part in {"", ".", ".."} for part in parts) or "\\" in path:
                continue
            if not parts[-1].lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            relative = path.partition("/")[2]
            target = targets.get(relative, relative)
            renditions[target].add(path)

        self.sources: dict[str, str] = {}
        self.exact: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        self.normalized: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for target, copies in renditions.items():
            raw = [path for path in copies if path.startswith("pictures_raw/")]
            if len(raw) <= 1:
                final = f"pictures/{target}"
                self.sources[target] = raw[0] if raw else final if final in copies else min(copies)
                self._index(target, target)
            else:
                # A rename collision does not identify which original produced a final file.
                for path in sorted(copies):
                    relative = path.partition("/")[2]
                    self.sources[relative] = path
                    self._index(relative, relative)
                if target not in self.sources:
                    for path in raw:
                        self._index(target, path.partition("/")[2])
        for original, target in targets.items():
            if original not in self.sources and target in self.sources:
                self._index(original, target)

    def _index(self, relative: str, target: str) -> None:
        project, *layers, name = relative.split("/")
        layer = "/".join(layers).casefold()
        normalized = _normalized_name(name)
        for scope in {layer, ""}:
            self.exact[project, scope, name].add(target)
            self.normalized[project, scope, normalized].add(target)

    def candidates(self, project: str, reference: str) -> set[str]:
        parts = reference.split("/")
        name = parts[-1]
        layer = ""
        if "DCIM" in parts:
            layer = "/".join(parts[parts.index("DCIM") + 1 : -1]).casefold()
        for scope in (layer, "") if layer else ("",):
            matches = self.exact.get((project, scope, name))
            if matches:
                return matches
            matches = self.normalized.get((project, scope, _normalized_name(name)))
            if matches:
                return matches
        return set()


def _sample(project: str, code: str, metadata: Mapping[str, object] | None) -> Sample:
    return {"project": project, "code": code, "metadata": metadata, "photos": [], "missing": []}


def build_catalog(
    rows: Iterable[Mapping[str, object]],
    paths: Iterable[str],
    aliases: Mapping[str, Mapping[str, object]],
) -> Catalog:
    """Preserve every field observation and resolve its attachments against actual files."""
    archive = _Archive(paths, aliases)
    samples: list[Sample] = []
    by_code: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    explicitly_referenced: set[str] = set()
    for row in rows:
        project = str(row.get("qfield_project") or "")
        code = str(row.get("sample_id") or "").strip()
        sample = _sample(project, code, row)
        samples.append(sample)
        by_code[project, code.casefold()].append(sample)
        linked: set[str] = set()
        for field, value in row.items():
            if not field.startswith("picture_"):
                continue
            for reference in value if isinstance(value, list) else [value]:
                if not isinstance(reference, str) or not reference.strip():
                    continue
                reference = reference.strip()
                candidates = archive.candidates(project, reference)
                explicitly_referenced.update(candidates)
                if len(candidates) != 1:
                    if reference not in sample["missing"]:
                        sample["missing"].append(reference)
                    continue
                target = next(iter(candidates))
                if target not in linked:
                    label = field.removeprefix("picture_").replace("_", " ").capitalize()
                    sample["photos"].append({"source": archive.sources[target], "label": label})
                    linked.add(target)

    for target in sorted(archive.sources.keys() - explicitly_referenced):
        project = target.partition("/")[0]
        source = archive.sources[target]
        names = {target.rsplit("/", 1)[-1], source.rsplit("/", 1)[-1]}
        codes = {
            token
            for name in names
            for token in _CODE_TOKEN.findall(name.casefold())
            if (project, token) in by_code or _ARCHIVE_CODE.fullmatch(token)
        }
        if len(codes) != 1:
            continue
        code = codes.pop()
        owners = by_code[project, code]
        if not owners:
            sample = _sample(project, code, None)
            owners.append(sample)
            samples.append(sample)
        if len(owners) == 1:
            owners[0]["photos"].append({"source": source, "label": source.rsplit("/", 1)[-1]})

    return {"samples": samples}
