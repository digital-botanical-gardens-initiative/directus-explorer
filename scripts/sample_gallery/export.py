"""Export a private static gallery from Directus and the Nextcloud sample photo folders."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from catalog import build_catalog

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient

ARCHIVE = "/var/www/html/data/emi/files/output"
ROOTS = ("pictures", "pictures_raw")
CONTAINER = ["docker", "exec", "-i", "--user", "www-data", "--workdir", ARCHIVE, "nextcloud_app"]
MAPPING = Path("/media/data/qfieldcloud_data/data/picture_map.json")


def photo_paths() -> list[str]:
    output = subprocess.check_output(
        [*CONTAINER, "find", *ROOTS, "-type", "f", "-iname", "*.jpg", "-print0"], timeout=120
    )
    return [os.fsdecode(name) for name in output.split(b"\0") if name]


def copy_selected(sources: set[str], destination: Path) -> None:
    """Read only catalogued JPEGs from Nextcloud into the static export."""
    with tempfile.TemporaryFile() as names:
        for source in sorted(sources):
            path = PurePosixPath(source)
            if path.parts[0] not in ROOTS or ".." in path.parts or path.suffix.lower() != ".jpg":
                raise ValueError(f"Unexpected archive path {source!r}")
            names.write(os.fsencode(source) + b"\0")
        names.seek(0)
        command = [*CONTAINER, "tar", "--create", "--file=-", "--null", "--no-recursion",
                   "--hard-dereference", "--files-from=-"]
        with subprocess.Popen(command, stdin=names, stdout=subprocess.PIPE) as process:
            assert process.stdout is not None
            pending = sources.copy()
            try:
                with tarfile.open(fileobj=process.stdout, mode="r|", bufsize=256 * 1024) as stream:
                    for member in stream:
                        if member.name not in pending or not member.isfile():
                            raise ValueError(f"Unexpected archive member {member.name!r}")
                        pending.remove(member.name)
                        target = destination / member.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        image = stream.extractfile(member)
                        assert image is not None
                        with image, target.open("xb") as output:
                            shutil.copyfileobj(image, output, length=256 * 1024)
                process.stdout.close()
                if process.wait(timeout=30) or pending:
                    raise RuntimeError(f"Incomplete export, {len(pending)} pictures outstanding")
            finally:
                process.stdout.close()
                if process.poll() is None:
                    process.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New private website directory")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    os.umask(0o077)
    destination = args.destination.resolve()
    if destination.exists():
        parser.error("Destination already exists. Choose a new snapshot directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    client = DirectusClient(load_settings(args.env_file))
    client._authenticate()
    records = client._get_items("Field_Data", "*")
    paths = photo_paths()
    catalog = build_catalog(records, paths, json.loads(MAPPING.read_bytes()))
    catalog["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    samples = catalog["samples"]
    sources = {photo["source"] for sample in samples for photo in sample["photos"]}
    if not records or not sources or not sources.issubset(paths):
        raise ValueError("Missing field records or invalid image selection")
    print(
        f"{len(records)} field records, {len(samples)} sample entries, "
        f"{len(sources)} distinct pictures to copy", flush=True
    )
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        site = Path(staging) / "site"
        site.mkdir()
        copy_selected(sources, site / "media")
        (site / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        shutil.copyfile(Path(__file__).with_name("index.html"), site / "index.html")
        site.rename(destination)
    print(f"Published private snapshot at {destination}")


if __name__ == "__main__":
    main()
