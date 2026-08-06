# DBGI Zenodo publisher

This crate publishes a new version of an existing Zenodo record using
`zenodo-rs`. Publication is driven by a TOML manifest, and all artifact paths are
resolved relative to that manifest.

The `draft` command creates or reuses the latest editable version draft, replaces
its files, and verifies remote sizes and MD5 checksums without publishing. The
`publish` command performs the same verification and then publishes. It first
checks the latest public record: if the requested `vN` is already published with
the same files, it exits successfully instead of creating another version. This
makes rerunning the command safe after a timeout.

Authentication comes only from the `ZENODO_TOKEN` environment variable. The
token needs Zenodo `deposit:write` and `deposit:actions` scopes.

## Preparing the next version

The normal workflow is a single command:

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  release zenodo/dbgi-samples-metadata-release.toml
```

The command resolves the latest record family member, calculates the next `vN`,
runs the configured exporter into a new version-specific directory, validates
the TSV, generates its README and frozen `publish.toml`, stages and verifies the
files, and publishes. Generated release state is stored under
`data/output/zenodo/dbgi-samples-metadata/vN/` so a pending version can be
resumed without changing its timestamp or bytes.

To review the remote draft before publication, use:

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  release zenodo/dbgi-samples-metadata-release.toml --draft-only
```

The lower-level manifest commands remain available for inspection, recovery,
and nonstandard releases.

## Low-level publishing

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  plan zenodo/dbgi-samples-metadata-v4.toml

cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  draft zenodo/dbgi-samples-metadata-v4.toml

cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  publish-draft zenodo/dbgi-samples-metadata-v4.toml --draft-id ID_FROM_DRAFT
```

`publish` remains available as a one-command stage-and-publish operation. The
two-phase `draft` / `publish-draft` workflow is preferred when a draft will be
reviewed or when Zenodo has an open file-modification state: `publish-draft`
targets the exact verified draft and does not invoke `newversion` or reconcile
its files again.

If Zenodo returns a gateway timeout, run the same command again. The crate checks
for the requested public version before obtaining a draft, and `zenodo-rs`
reuses an existing editable draft when one is present.
