#!/usr/bin/env python3
# ruff: noqa: E501

"""Generate a standalone HTML report from the DBGI sample-compact metadata TSV."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INPUT = Path("sample_metadata_dbgi_sample_compact.tsv")
DEFAULT_OUTPUT = Path("dbgi_metadata_report.html")
DBGI_PROJECTS = ("jbc", "jbn", "jbp", "jbuf", "kew-botanical-gardens")
PALETTE = (
    "#4E79A7",
    "#F28E2B",
    "#59A14F",
    "#E15759",
    "#76B7B2",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
    "#9C755F",
    "#BAB0AC",
    "#2F6B3F",
    "#8E6C8A",
)
FAMILY_COLOR_BY_NAME: dict[str, str] = {}


@dataclass(frozen=True, slots=True)
class SpeciesRecord:
    taxon_id: str
    canonical: str
    order: str
    family: str
    genus: str


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float
    item: dict[str, object]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def truthy(value: str) -> bool:
    return value.strip().lower() == "true"


def text(value: object) -> str:
    return html.escape(str(value), quote=True)


def pct(part: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{part / total * 100:.1f}%"


def species_record(row: dict[str, str]) -> SpeciesRecord | None:
    taxon_id = row.get("resolved_taxon_id", "").strip()
    canonical = row.get("resolved_taxon_canonical", "").strip()
    if not taxon_id or not canonical:
        return None
    return SpeciesRecord(
        taxon_id=taxon_id,
        canonical=canonical,
        order=row.get("resolved_taxon_order", "").strip() or "Unassigned order",
        family=row.get("resolved_taxon_family", "").strip() or "Unassigned family",
        genus=row.get("resolved_taxon_genus", "").strip() or "Unassigned genus",
    )


def color_for(label: str, color_by_label: dict[str, str]) -> str:
    if label not in color_by_label:
        color_by_label[label] = PALETTE[len(color_by_label) % len(PALETTE)]
    return color_by_label[label]


def normalize_sizes(items: list[dict[str, object]], width: float, height: float) -> list[float]:
    total = sum(float(item["value"]) for item in items)
    if total <= 0:
        return []
    scale = width * height / total
    return [float(item["value"]) * scale for item in items]


def worst(row: list[float], side: float) -> float:
    if not row:
        return math.inf
    row_sum = sum(row)
    if row_sum == 0 or side == 0:
        return math.inf
    return max((side * side * max(row)) / (row_sum * row_sum), (row_sum * row_sum) / (side * side * min(row)))


def layout_row(
    row: list[float],
    items: list[dict[str, object]],
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[list[Rect], float, float, float, float]:
    rects: list[Rect] = []
    row_sum = sum(row)
    if width >= height:
        row_height = row_sum / width if width else 0
        cursor_x = x
        for size, item in zip(row, items, strict=True):
            rect_width = size / row_height if row_height else 0
            rects.append(Rect(cursor_x, y, rect_width, row_height, item))
            cursor_x += rect_width
        return rects, x, y + row_height, width, height - row_height

    row_width = row_sum / height if height else 0
    cursor_y = y
    for size, item in zip(row, items, strict=True):
        rect_height = size / row_width if row_width else 0
        rects.append(Rect(x, cursor_y, row_width, rect_height, item))
        cursor_y += rect_height
    return rects, x + row_width, y, width - row_width, height


def squarify(items: list[dict[str, object]], width: float, height: float) -> list[Rect]:
    ordered = sorted(items, key=lambda item: float(item["value"]), reverse=True)
    sizes = normalize_sizes(ordered, width, height)
    rects: list[Rect] = []
    row: list[float] = []
    row_items: list[dict[str, object]] = []
    x = y = 0.0
    remaining_width = width
    remaining_height = height
    side = min(remaining_width, remaining_height)

    for size, item in zip(sizes, ordered, strict=True):
        if not row or worst([*row, size], side) <= worst(row, side):
            row.append(size)
            row_items.append(item)
            continue
        new_rects, x, y, remaining_width, remaining_height = layout_row(
            row, row_items, x, y, remaining_width, remaining_height
        )
        rects.extend(new_rects)
        row = [size]
        row_items = [item]
        side = min(remaining_width, remaining_height)

    if row:
        new_rects, _, _, _, _ = layout_row(row, row_items, x, y, remaining_width, remaining_height)
        rects.extend(new_rects)
    return rects


def species_items(rows: Iterable[dict[str, str]], project: str | None = None) -> list[dict[str, object]]:
    by_taxon: dict[str, dict[str, object]] = {}
    for row in rows:
        if project is not None and row.get("qfield_project") != project:
            continue
        record = species_record(row)
        if record is None:
            continue
        by_taxon.setdefault(
            record.taxon_id,
            {
                "label": record.canonical,
                "value": 0,
                "taxon_id": record.taxon_id,
                "order": record.order,
                "family": record.family,
                "genus": record.genus,
            },
        )
        by_taxon[record.taxon_id]["value"] = int(by_taxon[record.taxon_id]["value"]) + 1
    return list(by_taxon.values())


def grouped_items(items: list[dict[str, object]], fieldname: str) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for item in items:
        label = str(item[fieldname])
        group = grouped.setdefault(label, {"label": label, "value": 0, "children": []})
        group["value"] = int(group["value"]) + int(item["value"])
        children = group["children"]
        if isinstance(children, list):
            children.append(item)
    return list(grouped.values())


def draw_species_rect(
    rect: Rect,
    *,
    fill: str,
) -> str:
    item = rect.item
    label = str(item["label"])
    family = str(item["family"])
    genus = str(item["genus"])
    taxon_id = str(item["taxon_id"])
    x = rect.x + 0.5
    y = rect.y + 0.5
    w = max(rect.width - 1, 0)
    h = max(rect.height - 1, 0)
    parts = [
        f'<g><rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'fill="{fill}"><title>{text(family)} / {text(genus)} / {text(label)} | COL {text(taxon_id)}</title></rect>'
    ]
    if w > 74 and h > 26:
        font_size = 11 if w > 120 and h > 44 else 9
        max_chars = max(int(w / (font_size * 0.58)), 8)
        display = label if len(label) <= max_chars else f"{label[: max_chars - 1]}..."
        parts.append(
            f'<text x="{x + 4:.2f}" y="{y + 14:.2f}" font-size="{font_size}">'
            f"{text(display)}</text>"
        )
    parts.append("</g>")
    return "\n".join(parts)


def treemap_svg(
    title: str,
    items: list[dict[str, object]],
    *,
    width: int = 1180,
    height: int = 620,
) -> str:
    if not items:
        return f"<section><h2>{text(title)}</h2><p>No resolved species available.</p></section>"

    color_by_family: dict[str, str] = {}
    family_items = grouped_items(items, "family")
    family_rects = squarify(family_items, width, height)
    parts = [
        "<section>",
        f"<h2>{text(title)}</h2>",
        f'<svg class="treemap" viewBox="0 0 {width} {height}" role="img" aria-label="{text(title)}">',
    ]
    for family_rect in family_rects:
        family_item = family_rect.item
        family = str(family_item["label"])
        family_species_count = int(family_item["value"])
        fill = color_for(family, color_by_family)
        fx = family_rect.x + 0.5
        fy = family_rect.y + 0.5
        fw = max(family_rect.width - 1, 0)
        fh = max(family_rect.height - 1, 0)
        parts.append(
            f'<rect class="family-frame" x="{fx:.2f}" y="{fy:.2f}" width="{fw:.2f}" '
            f'height="{fh:.2f}" fill="#ffffff"><title>{text(family)} | '
            f'{family_species_count} species</title></rect>'
        )
        label_height = 20 if fw > 90 and fh > 50 else 0
        if label_height:
            parts.append(
                f'<text class="family-label" x="{fx + 5:.2f}" y="{fy + 14:.2f}" '
                f'font-size="12">{text(family)} ({family_species_count})</text>'
            )

        children = family_item["children"]
        if not isinstance(children, list):
            continue
        genus_items = grouped_items(children, "genus")
        inner_x = family_rect.x + 3
        inner_y = family_rect.y + 3 + label_height
        inner_w = max(family_rect.width - 6, 0)
        inner_h = max(family_rect.height - 6 - label_height, 0)
        if inner_w <= 0 or inner_h <= 0:
            continue
        genus_rects = squarify(genus_items, inner_w, inner_h)
        for genus_rect in genus_rects:
            genus_item = genus_rect.item
            genus = str(genus_item["label"])
            gx = inner_x + genus_rect.x + 0.5
            gy = inner_y + genus_rect.y + 0.5
            gw = max(genus_rect.width - 1, 0)
            gh = max(genus_rect.height - 1, 0)
            parts.append(
                f'<rect class="genus-frame" x="{gx:.2f}" y="{gy:.2f}" width="{gw:.2f}" '
                f'height="{gh:.2f}" fill="none"><title>{text(family)} / {text(genus)} | '
                f'{int(genus_item["value"])} species</title></rect>'
            )
            genus_label_height = 16 if gw > 80 and gh > 44 else 0
            if genus_label_height:
                max_chars = max(int(gw / 7.0), 8)
                display = genus if len(genus) <= max_chars else f"{genus[: max_chars - 1]}..."
                parts.append(
                    f'<text class="genus-label" x="{gx + 4:.2f}" y="{gy + 12:.2f}" '
                    f'font-size="10">{text(display)}</text>'
                )
            species_children = genus_item["children"]
            if not isinstance(species_children, list):
                continue
            sx = inner_x + genus_rect.x + 2
            sy = inner_y + genus_rect.y + 2 + genus_label_height
            sw = max(genus_rect.width - 4, 0)
            sh = max(genus_rect.height - 4 - genus_label_height, 0)
            if sw <= 0 or sh <= 0:
                continue
            for species_rect in squarify(species_children, sw, sh):
                shifted = Rect(
                    sx + species_rect.x,
                    sy + species_rect.y,
                    species_rect.width,
                    species_rect.height,
                    species_rect.item,
                )
                parts.append(draw_species_rect(shifted, fill=fill))
    parts.append("</svg>")
    parts.append("<p class=\"chart-note\">Area is species presence: each COL-resolved species has weight 1. Family and genus rectangles are sized by the number of resolved species they contain.</p>")
    parts.append("</section>")
    return "\n".join(parts)


def plotly_treemap_payload(title: str, items: list[dict[str, object]]) -> dict[str, object]:
    labels = ["All species"]
    ids = ["root"]
    parents = [""]
    values = [sum(int(item["value"]) for item in items)]
    customdata = [["", "", "", len(items), values[0]]]
    marker_colors = ["#E8ECF2"]

    families = grouped_items(items, "family")
    for family in sorted(families, key=lambda item: (-int(item["value"]), str(item["label"]))):
        family_label = str(family["label"])
        family_id = f"family:{family_label}"
        family_value = int(family["value"])
        labels.append(family_label)
        ids.append(family_id)
        parents.append("root")
        values.append(family_value)
        family_species_count = len(
            {
                str(child["taxon_id"])
                for child in family["children"]
            }
            if isinstance(family["children"], list)
            else set()
        )
        customdata.append([family_label, "", "", family_species_count, family_value])
        marker_colors.append(color_for(family_label, FAMILY_COLOR_BY_NAME))

        family_children = family["children"]
        if not isinstance(family_children, list):
            continue
        genera = grouped_items(family_children, "genus")
        for genus in sorted(genera, key=lambda item: (-int(item["value"]), str(item["label"]))):
            genus_label = str(genus["label"])
            genus_id = f"{family_id}/genus:{genus_label}"
            genus_value = int(genus["value"])
            labels.append(genus_label)
            ids.append(genus_id)
            parents.append(family_id)
            values.append(genus_value)
            genus_species_count = len(
                {
                    str(child["taxon_id"])
                    for child in genus["children"]
                }
                if isinstance(genus["children"], list)
                else set()
            )
            customdata.append([family_label, genus_label, "", genus_species_count, genus_value])
            marker_colors.append(color_for(family_label, FAMILY_COLOR_BY_NAME))

            genus_children = genus["children"]
            if not isinstance(genus_children, list):
                continue
            for species in sorted(genus_children, key=lambda item: str(item["label"])):
                species_label = str(species["label"])
                taxon_id = str(species["taxon_id"])
                labels.append(species_label)
                ids.append(f"{genus_id}/species:{taxon_id}")
                parents.append(genus_id)
                sample_count = int(species["value"])
                values.append(sample_count)
                customdata.append([family_label, genus_label, taxon_id, 1, sample_count])
                marker_colors.append(color_for(family_label, FAMILY_COLOR_BY_NAME))

    return {
        "data": [
            {
                "type": "treemap",
                "labels": labels,
                "ids": ids,
                "parents": parents,
                "values": values,
                "branchvalues": "total",
                "customdata": customdata,
                "maxdepth": 3,
                "pathbar": {"visible": True},
                "hovertemplate": (
                    "<b>%{label}</b><br>"
                    "Family: %{customdata[0]}<br>"
                    "Genus: %{customdata[1]}<br>"
                    "COL taxon id: %{customdata[2]}<br>"
                    "Resolved species: %{customdata[3]}<br>"
                    "Collected samples: %{customdata[4]}<extra></extra>"
                ),
                "textinfo": "label+value",
                "marker": {"cornerradius": 2, "colors": marker_colors},
            }
        ],
        "layout": {
            "title": {"text": title, "x": 0, "xanchor": "left"},
            "height": 760,
            "margin": {"t": 48, "r": 8, "b": 8, "l": 8},
            "paper_bgcolor": "#ffffff",
            "plot_bgcolor": "#ffffff",
            "font": {"family": "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"},
            "uniformtext": {"minsize": 10, "mode": "hide"},
        },
        "config": {"responsive": True, "displaylogo": False},
    }


def plotly_treemap_section(div_id: str, title: str, items: list[dict[str, object]]) -> str:
    if not items:
        return f"<section><h2>{text(title)}</h2><p>No resolved species available.</p></section>"
    return (
        "<section>"
        f'<div id="{text(div_id)}" class="plotly-chart"></div>'
        '<p class="chart-note">Interactive Plotly treemap. Hierarchy: family / genus / species. '
        "Species leaves are sized by collected sample count. Family colors are fixed across all treemaps. Click a family or genus to zoom; use the path bar to go back.</p>"
        "</section>"
    )


def bar_svg(title: str, items: list[tuple[str, int]], *, width: int = 940) -> str:
    max_value = max((value for _, value in items), default=0)
    row_height = 28
    left = 260
    right = 95
    height = max(46, len(items) * row_height + 20)
    bar_width = width - left - right
    parts = [
        "<section>",
        f"<h2>{text(title)}</h2>",
        f'<svg class="barplot" viewBox="0 0 {width} {height}" role="img" aria-label="{text(title)}">',
    ]
    for index, (label, value) in enumerate(items):
        y = 12 + index * row_height
        width_value = 0 if max_value == 0 else bar_width * value / max_value
        parts.append(f'<text x="0" y="{y + 15}" font-size="12">{text(label)}</text>')
        parts.append(
            f'<rect x="{left}" y="{y}" width="{width_value:.2f}" height="18" rx="2" fill="#4E79A7" />'
        )
        parts.append(f'<text x="{left + width_value + 8:.2f}" y="{y + 14}" font-size="12">{value}</text>')
    parts.append("</svg>")
    parts.append("</section>")
    return "\n".join(parts)


def profile_coverage_svg(rows: list[dict[str, str]], *, width: int = 940) -> str:
    project_rows = {project: [row for row in rows if row.get("qfield_project") == project] for project in DBGI_PROJECTS}
    items = []
    for project, values in project_rows.items():
        total = len(values)
        profiled = sum(1 for row in values if truthy(row.get("profiled_any", "")))
        items.append((project, profiled, total))

    height = len(items) * 34 + 20
    left = 260
    right = 140
    bar_width = width - left - right
    parts = [
        "<section>",
        "<h2>Profiled Sample Coverage By Subproject</h2>",
        f'<svg class="barplot" viewBox="0 0 {width} {height}" role="img" aria-label="Profiled sample coverage">',
    ]
    for index, (project, profiled, total) in enumerate(items):
        y = 12 + index * 34
        coverage = 0 if total == 0 else profiled / total
        parts.append(f'<text x="0" y="{y + 15}" font-size="12">{text(project)}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_width}" height="18" rx="2" fill="#E8ECF2" />')
        parts.append(
            f'<rect x="{left}" y="{y}" width="{bar_width * coverage:.2f}" height="18" rx="2" fill="#59A14F" />'
        )
        parts.append(
            f'<text x="{left + bar_width + 10}" y="{y + 14}" font-size="12">'
            f"{profiled}/{total} ({pct(profiled, total)})</text>"
        )
    parts.append("</svg>")
    parts.append("</section>")
    return "\n".join(parts)


def summary_cards(rows: list[dict[str, str]]) -> str:
    resolved = [row for row in rows if row.get("resolved_taxon_id")]
    profiled = [row for row in rows if truthy(row.get("profiled_any", ""))]
    species_ids = {row["resolved_taxon_id"] for row in resolved}
    families = {row["resolved_taxon_family"] for row in resolved if row.get("resolved_taxon_family")}
    cards = [
        ("Collected samples", len(rows)),
        ("Resolved COL species", len(species_ids)),
        ("Resolved sample rows", len(resolved)),
        ("Profiled samples", len(profiled)),
        ("Resolved families", len(families)),
        ("Unresolved rows", len(rows) - len(resolved)),
    ]
    parts = ['<section class="kpis">']
    for label, value in cards:
        parts.append(f'<div class="kpi"><span>{text(label)}</span><strong>{value}</strong></div>')
    parts.append("</section>")
    return "\n".join(parts)


def make_report(rows: list[dict[str, str]], input_path: Path) -> str:
    project_sample_counts = Counter(row.get("qfield_project", "") for row in rows)
    project_species_counts = [
        (project, len({item["taxon_id"] for item in species_items(rows, project)}))
        for project in DBGI_PROJECTS
    ]
    unresolved_counts = [
        (
            project,
            sum(
                1
                for row in rows
                if row.get("qfield_project") == project and not row.get("resolved_taxon_id")
            ),
        )
        for project in DBGI_PROJECTS
    ]
    family_species_counts = Counter()
    for item in species_items(rows):
        family_species_counts[str(item["family"])] += 1

    treemap_specs: dict[str, dict[str, object]] = {
        "treemap_all": plotly_treemap_payload(
            "All DBGI Species By COL Taxonomy",
            species_items(rows),
        )
    }
    treemap_sections = [
        plotly_treemap_section(
            "treemap_all",
            "All DBGI Species By COL Taxonomy",
            species_items(rows),
        )
    ]
    for project in DBGI_PROJECTS:
        div_id = f"treemap_{project.replace('-', '_')}"
        project_items = species_items(rows, project)
        treemap_specs[div_id] = plotly_treemap_payload(
            f"{project} Species By COL Taxonomy",
            project_items,
        )
        treemap_sections.append(
            plotly_treemap_section(
                div_id,
                f"{project} Species By COL Taxonomy",
                project_items,
            )
        )
    treemap_json = json.dumps(treemap_specs)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DBGI Metadata Summary</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f6f7f9; }}
    header {{ padding: 28px 36px 20px; background: #ffffff; border-bottom: 1px solid #d9dee7; }}
    main {{ padding: 24px 36px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; font-weight: 700; }}
    h2 {{ margin: 0 0 14px; font-size: 19px; font-weight: 650; }}
    p {{ margin: 6px 0; color: #52606d; }}
    section {{ margin: 0 0 24px; padding: 20px; background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 24px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; background: transparent; border: 0; padding: 0; }}
    .kpi {{ padding: 16px; background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; }}
    .kpi span {{ display: block; color: #52606d; font-size: 13px; }}
    .kpi strong {{ display: block; margin-top: 6px; font-size: 27px; }}
    svg {{ width: 100%; height: auto; }}
    svg text {{ fill: #1f2933; pointer-events: none; }}
    .muted-svg {{ fill: #52606d; }}
    .plotly-chart {{ width: 100%; min-height: 760px; }}
    .note {{ max-width: 1050px; }}
    .chart-note {{ font-size: 13px; }}
  </style>
</head>
<body>
<header>
  <h1>DBGI Sample Metadata Summary</h1>
  <p class="note">Source: {text(input_path)}. Treemaps use Catalogue of Life resolved taxa, grouped as family / genus / species. Species leaves are sized by collected sample count. Unresolved names are excluded from species treemaps and species counts.</p>
</header>
<main>
  {summary_cards(rows)}
  <div class="grid">
    {bar_svg("Collected Samples By Subproject", [(project, project_sample_counts[project]) for project in DBGI_PROJECTS])}
    {bar_svg("Resolved Species By Subproject", project_species_counts)}
    {profile_coverage_svg(rows)}
    {bar_svg("Rows Without COL Resolution By Subproject", unresolved_counts)}
    {bar_svg("Top Families By Resolved Species Count", family_species_counts.most_common(20))}
  </div>
  {"".join(treemap_sections)}
</main>
<script>
const treemapSpecs = {treemap_json};
for (const [id, spec] of Object.entries(treemapSpecs)) {{
  Plotly.newPlot(id, spec.data, spec.layout, spec.config);
}}
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_rows(args.input)
    args.output.write_text(make_report(rows, args.input), encoding="utf-8")
    print(f"Wrote HTML report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
