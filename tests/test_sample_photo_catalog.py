"""Sample search results retain Directus ownership across archive renames."""

from pathlib import Path
from runpy import run_path

build_catalog = run_path(str(Path(__file__).parents[1] / "scripts/sample_gallery/catalog.py"))[
    "build_catalog"
]


def test_explicit_reference_owns_all_renditions_even_when_basename_has_another_code() -> None:
    original = "fung/trip/Forest floor_fung_000802_02.jpg"
    renamed = "fung/trip/Forest_floor_fung_000802_02.jpg"
    reference = "DCIM/trip/Forest floor_fung_000802_02.jpg"
    owner = {
        "id": 12,
        "qfield_project": "fung",
        "sample_id": "fung_000801",
        "picture_detail": reference,
        "picture_free": reference,
    }
    other = {"id": 13, "qfield_project": "fung", "sample_id": "fung_000802"}
    raw = f"pictures_raw/{original}"
    samples = build_catalog([other, owner], [f"pictures/{renamed}", raw], {
        original: {"renamed_rel": renamed},
    })["samples"]
    first, second = sorted(samples, key=lambda sample: sample["code"])
    assert first["metadata"] == owner
    assert [photo["source"] for photo in first["photos"]] == [raw]
    assert first["photos"][0]["label"]
    assert first["missing"] == []
    assert second["photos"] == []


def test_renamed_layers_and_names_require_a_unique_project_scoped_match() -> None:
    clear = "DCIM/Audrey_layer/Lonicera xylosteum L._dbgi_001187_Panel+Label.jpg"
    ambiguous = "DCIM/Audrey_layer/Free shot.jpg"
    foreign = "DCIM/Audrey_layer/Absent.jpg"
    actual = "pictures/jbuf/audrey_le_cabec/Lonicera_xylosteum_L_dbgi_001187_PanelLabel.jpg"
    row = {
        "sample_id": "dbgi_001187",
        "qfield_project": "jbuf",
        "picture_panel_label": clear,
        "picture_detail": ambiguous,
        "picture_free": foreign,
    }
    paths = [
        actual,
        "pictures/jbuf/alice/Free_shot.jpg",
        "pictures/jbuf/bob/Free_shot.jpg",
        "pictures/jbn/Audrey_layer/Absent.jpg",
    ]
    sample, = build_catalog([row], paths, {})["samples"]
    assert [photo["source"] for photo in sample["photos"]] == [actual]
    assert set(sample["missing"]) == {ambiguous, foreign}


def test_full_code_tokens_add_unreferenced_photos_and_archive_only_samples() -> None:
    row = {"sample_id": "fung_000801", "qfield_project": "fung"}
    unrelated = {"sample_id": "fung_000801", "qfield_project": "elsewhere"}
    original = "pictures_raw/fung/trip/Agaricus_fung_000801_04.jpg"
    orphan = "pictures/fung/trip/fung_000803_05.jpg"
    paths = [
        orphan, original, original.replace("pictures_raw", "pictures"),
        "pictures/fung/trip/Agaricus_xfung_000801_05.jpg",
        "pictures/fung/trip/Agaricus_fung_0008018_05.jpg",
        "pictures/fung/trip/Agaricus_fung_000801e_05.jpg",
        "pictures/fung/trip/fung_000801_fung_000803.jpg",
        "pictures/fung/trip/IMG_012345.jpg",
    ]
    samples = build_catalog([row, unrelated], paths, {})["samples"]
    assert len(samples) == 3
    by_key = {(sample["project"], sample["code"]): sample for sample in samples}
    assert [photo["source"] for photo in by_key["fung", "fung_000801"]["photos"]] == [original]
    assert [photo["source"] for photo in by_key["fung", "fung_000803"]["photos"]] == [orphan]
    assert by_key["fung", "fung_000803"]["metadata"] is None
    assert by_key["elsewhere", "fung_000801"]["photos"] == []


def test_duplicate_rows_survive_and_ambiguous_code_only_photos_are_not_assigned() -> None:
    first = {"id": 12, "sample_id": "fung_000801", "qfield_project": "fung"}
    second = {**first, "id": 13, "picture_detail": "DCIM/trip/detail.jpg"}
    detail = "pictures/fung/trip/detail.jpg"
    samples = build_catalog([first, second], [detail, "pictures/fung/trip/fung_000801.jpg"], {})[
        "samples"
    ]
    by_id = {sample["metadata"]["id"]: sample for sample in samples}
    assert set(by_id) == {12, 13}
    assert by_id[12]["photos"] == []
    assert [photo["source"] for photo in by_id[13]["photos"]] == [detail]


def test_two_raw_originals_with_one_renamed_path_cannot_substitute_for_each_other() -> None:
    first = "jbc/trip/Leaf 1.jpg"
    second = "jbc/trip/Leaf_1!.jpg"
    target = "jbc/trip/Leaf_1.jpg"
    names = ["Leaf 1.jpg", "Leaf_1!.jpg", "Leaf_1.jpg"]
    rows = [
        {"qfield_project": "jbc", "sample_id": code, "picture_cut": f"DCIM/trip/{name}"}
        for code, name in zip(["dbgi_008711", "dbgi_008712", "dbgi_008713"], names, strict=True)
    ]
    paths = ["pictures_raw/" + first, "pictures_raw/" + second]
    aliases = {original: {"renamed_rel": target} for original in [first, second]}
    first_sample, second_sample, ambiguous = build_catalog(rows, paths, aliases)["samples"]
    assert [photo["source"] for photo in first_sample["photos"]] == ["pictures_raw/" + first]
    assert [photo["source"] for photo in second_sample["photos"]] == ["pictures_raw/" + second]
    assert ambiguous["photos"] == []
    assert ambiguous["missing"] == ["DCIM/trip/Leaf_1.jpg"]
