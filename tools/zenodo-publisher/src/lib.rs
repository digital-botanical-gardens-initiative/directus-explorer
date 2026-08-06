use std::collections::{BTreeMap, HashSet};
use std::error::Error;
use std::fs::{File, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use chrono::{NaiveDate, Utc};
use md5::{Digest, Md5};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use tokio::time::sleep;
use zenodo_rs::{
    AccessRight, DepositMetadataUpdate, DepositionId, FileReplacePolicy, Record, RecordId,
    UploadSpec, UploadType, ZenodoClient,
};

pub type AnyError = Box<dyn Error + Send + Sync>;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct PublishManifest {
    pub previous_record_id: u64,
    pub version: String,
    pub title: String,
    pub publication_date: NaiveDate,
    pub description_html: String,
    pub creators: Vec<String>,
    #[serde(default = "default_license")]
    pub license: String,
    pub community: Option<String>,
    pub files: Vec<ManifestFile>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct ManifestFile {
    pub archive_name: String,
    pub path: PathBuf,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalFile {
    pub archive_name: String,
    pub path: PathBuf,
    pub size: u64,
    pub md5: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StageOutcome {
    AlreadyPublished(Box<Record>),
    Draft(DepositionId),
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct ReleaseConfig {
    pub record_id: u64,
    pub title: String,
    pub description_html: String,
    pub creators: Vec<String>,
    #[serde(default = "default_license")]
    pub license: String,
    pub community: Option<String>,
    pub working_dir: PathBuf,
    pub output_dir: PathBuf,
    pub readme_template: PathBuf,
    pub export_command: Vec<String>,
}

pub struct PreparedRelease {
    pub manifest_path: PathBuf,
    pub manifest: PublishManifest,
    pub files: Vec<LocalFile>,
}

pub fn load_manifest(path: &Path) -> Result<(PublishManifest, Vec<LocalFile>), AnyError> {
    let text = std::fs::read_to_string(path)?;
    let manifest: PublishManifest = toml::from_str(&text)?;
    validate_version(&manifest.version)?;
    if manifest.creators.is_empty() {
        return Err("manifest must contain at least one creator".into());
    }
    if manifest.files.is_empty() {
        return Err("manifest must contain at least one file".into());
    }

    let base = path.parent().unwrap_or_else(|| Path::new("."));
    let mut names = BTreeMap::new();
    let mut files = Vec::new();
    for item in &manifest.files {
        if item.archive_name.trim().is_empty() {
            return Err("archive filenames cannot be empty".into());
        }
        if names.insert(item.archive_name.clone(), ()).is_some() {
            return Err(format!("duplicate archive filename: {}", item.archive_name).into());
        }
        let local_path = base.join(&item.path);
        if !local_path.is_file() {
            return Err(format!("artifact does not exist: {}", local_path.display()).into());
        }
        files.push(LocalFile {
            archive_name: item.archive_name.clone(),
            size: local_path.metadata()?.len(),
            md5: md5_file(&local_path)?,
            path: local_path,
        });
    }
    Ok((manifest, files))
}

pub fn load_release_config(path: &Path) -> Result<ReleaseConfig, AnyError> {
    let text = std::fs::read_to_string(path)?;
    let config: ReleaseConfig = toml::from_str(&text)?;
    if config.export_command.is_empty() {
        return Err("release export_command cannot be empty".into());
    }
    if !config
        .export_command
        .iter()
        .any(|part| part.contains("{output}"))
    {
        return Err("release export_command must contain an {output} placeholder".into());
    }
    Ok(config)
}

pub fn next_version(current: Option<&str>) -> Result<String, AnyError> {
    let current = current.ok_or("latest Zenodo record does not have a vN version")?;
    validate_version(current)?;
    let number: u64 = current[1..].parse()?;
    Ok(format!("v{}", number + 1))
}

pub fn prepare_release(
    config_path: &Path,
    config: &ReleaseConfig,
    latest_record_id: u64,
    version: &str,
) -> Result<PreparedRelease, AnyError> {
    validate_version(version)?;
    let absolute_config_path = config_path.canonicalize()?;
    let config_dir = absolute_config_path
        .parent()
        .ok_or("release config does not have a parent directory")?;
    let output_root = config_dir.join(&config.output_dir);
    let release_dir = output_root.join(version);
    let manifest_path = release_dir.join("publish.toml");
    if manifest_path.is_file() {
        let (manifest, files) = load_manifest(&manifest_path)?;
        if manifest.version != version {
            return Err("saved release manifest has an unexpected version".into());
        }
        return Ok(PreparedRelease {
            manifest_path,
            manifest,
            files,
        });
    }

    std::fs::create_dir_all(&release_dir)?;
    let generated_at = Utc::now();
    let timestamp = generated_at.format("%Y%m%dT%H%M%SZ").to_string();
    let archive_name = format!("sample_metadata_dbgi_col_{timestamp}.tsv");
    let export_path = release_dir.join(&archive_name);
    run_export(config, config_dir, &export_path)?;
    let validation = validate_dbgi_export(&export_path)?;
    let sha256 = sha256_file(&export_path)?;

    let template_path = config_dir.join(&config.readme_template);
    let template = std::fs::read_to_string(&template_path)?;
    let readme = template
        .replace("{{version}}", version)
        .replace("{{generated_at}}", &generated_at.to_rfc3339())
        .replace("{{filename}}", &archive_name)
        .replace("{{row_count}}", &validation.row_count.to_string())
        .replace("{{sha256}}", &sha256);
    if readme.contains("{{") {
        return Err("archive README template contains unresolved placeholders".into());
    }
    write_new(&release_dir.join("README.md"), readme.as_bytes())?;

    let publication_date = generated_at.date_naive();
    let manifest = PublishManifest {
        previous_record_id: latest_record_id,
        version: version.to_owned(),
        title: config.title.clone(),
        publication_date,
        description_html: config
            .description_html
            .replace("{{version}}", version)
            .replace("{{date}}", &publication_date.to_string()),
        creators: config.creators.clone(),
        license: config.license.clone(),
        community: config.community.clone(),
        files: vec![
            ManifestFile {
                archive_name: archive_name.clone(),
                path: PathBuf::from(&archive_name),
            },
            ManifestFile {
                archive_name: "README.md".to_owned(),
                path: PathBuf::from("README.md"),
            },
        ],
    };
    let manifest_toml = toml::to_string_pretty(&manifest)?;
    write_new(&manifest_path, manifest_toml.as_bytes())?;
    let (manifest, files) = load_manifest(&manifest_path)?;
    Ok(PreparedRelease {
        manifest_path,
        manifest,
        files,
    })
}

pub async fn stage_version(
    client: &ZenodoClient,
    manifest: &PublishManifest,
    files: &[LocalFile],
) -> Result<StageOutcome, AnyError> {
    let latest = client
        .get_latest_record(RecordId(manifest.previous_record_id))
        .await?;
    if latest.metadata.version.as_deref() == Some(manifest.version.as_str()) {
        verify_published_files(&latest, files)?;
        return Ok(StageOutcome::AlreadyPublished(Box::new(latest)));
    }

    let draft = client
        .ensure_editable_draft(DepositionId(manifest.previous_record_id))
        .await?;
    if draft
        .metadata
        .get("version")
        .and_then(|value| value.as_str())
        == Some(manifest.version.as_str())
    {
        let remote = client.list_files(draft.id).await?;
        let actual = draft_file_map(&remote);
        if verify_file_map(&actual, files).is_ok() {
            return Ok(StageOutcome::Draft(draft.id));
        }
    }

    let metadata = build_metadata(manifest)?;
    let draft = client.update_metadata(draft.id, &metadata).await?;
    let uploads = files
        .iter()
        .map(|file| UploadSpec::from_path_as(&file.path, &file.archive_name))
        .collect::<Result<Vec<_>, _>>()?;
    client
        .reconcile_files(&draft, FileReplacePolicy::ReplaceAll, uploads)
        .await?;
    let remote = client.list_files(draft.id).await?;
    let actual = draft_file_map(&remote);
    verify_file_map(&actual, files)?;
    Ok(StageOutcome::Draft(draft.id))
}

pub async fn publish_version(
    client: &ZenodoClient,
    manifest: &PublishManifest,
    files: &[LocalFile],
) -> Result<Record, AnyError> {
    match stage_version(client, manifest, files).await? {
        StageOutcome::AlreadyPublished(record) => Ok(*record),
        StageOutcome::Draft(draft_id) => publish_draft(client, draft_id, manifest, files).await,
    }
}

pub async fn publish_draft(
    client: &ZenodoClient,
    draft_id: DepositionId,
    manifest: &PublishManifest,
    files: &[LocalFile],
) -> Result<Record, AnyError> {
    let draft = client.get_deposition(draft_id).await?;
    if draft
        .metadata
        .get("version")
        .and_then(|value| value.as_str())
        != Some(manifest.version.as_str())
    {
        return Err(format!("draft {draft_id} version does not match the manifest").into());
    }
    let remote = client.list_files(draft_id).await?;
    verify_file_map(&draft_file_map(&remote), files)?;

    let mut deposition = client.publish(draft_id).await?;
    for _ in 0..30 {
        if deposition.is_published() && deposition.record_id.is_some() {
            break;
        }
        sleep(Duration::from_secs(2)).await;
        deposition = client.get_deposition(draft_id).await?;
    }
    let record_id = deposition
        .record_id
        .ok_or("Zenodo did not expose a published record ID within 60 seconds")?;
    let record = client.get_record(record_id).await?;
    if record.metadata.version.as_deref() != Some(manifest.version.as_str()) {
        return Err("published record version does not match the manifest".into());
    }
    verify_published_files(&record, files)?;
    Ok(record)
}

pub fn describe_plan(manifest: &PublishManifest, files: &[LocalFile]) -> String {
    let mut output = format!(
        "Previous record: {}\nVersion: {}\nTitle: {}\nFiles:\n",
        manifest.previous_record_id, manifest.version, manifest.title
    );
    for file in files {
        output.push_str(&format!(
            "  {} <- {} ({} bytes, md5:{})\n",
            file.archive_name,
            file.path.display(),
            file.size,
            file.md5
        ));
    }
    output
}

fn build_metadata(manifest: &PublishManifest) -> Result<DepositMetadataUpdate, AnyError> {
    let mut builder = DepositMetadataUpdate::builder()
        .title(&manifest.title)
        .upload_type(UploadType::Dataset)
        .publication_date(manifest.publication_date)
        .description_html(&manifest.description_html)
        .access_right(AccessRight::Open)
        .license(&manifest.license)
        .version(&manifest.version);
    for creator in &manifest.creators {
        builder = builder.creator_named(creator);
    }
    if let Some(community) = &manifest.community {
        builder = builder.community_identifier(community);
    }
    Ok(builder.build()?)
}

struct ExportValidation {
    row_count: usize,
}

fn run_export(
    config: &ReleaseConfig,
    config_dir: &Path,
    output_path: &Path,
) -> Result<(), AnyError> {
    if output_path.exists() {
        return Err(format!("refusing to overwrite export: {}", output_path.display()).into());
    }
    let arguments = config
        .export_command
        .iter()
        .map(|part| part.replace("{output}", &output_path.to_string_lossy()))
        .collect::<Vec<_>>();
    let (program, arguments) = arguments
        .split_first()
        .ok_or("release export_command cannot be empty")?;
    let status = Command::new(program)
        .args(arguments)
        .current_dir(config_dir.join(&config.working_dir))
        .status()?;
    if !status.success() {
        return Err(format!("metadata export command failed with {status}").into());
    }
    Ok(())
}

fn validate_dbgi_export(path: &Path) -> Result<ExportValidation, AnyError> {
    let mut reader = csv::ReaderBuilder::new().delimiter(b'\t').from_path(path)?;
    let headers = reader.headers()?.clone();
    let required = [
        "original_sample_id",
        "field_taxon_col_id",
        "field_taxon_ref",
        "field_taxon_accepted_name",
    ];
    let indexes = required
        .iter()
        .map(|field| {
            headers
                .iter()
                .position(|header| header == *field)
                .ok_or_else(|| format!("export is missing required column: {field}"))
        })
        .collect::<Result<Vec<_>, _>>()?;

    let mut sample_ids = HashSet::new();
    let mut row_count = 0_usize;
    for record in reader.records() {
        let record = record?;
        row_count += 1;
        for (field, index) in required.iter().zip(&indexes) {
            if record.get(*index).unwrap_or_default().trim().is_empty() {
                return Err(format!("row {row_count} has an empty {field}").into());
            }
        }
        let sample_id = record.get(indexes[0]).unwrap_or_default().to_owned();
        if !sample_ids.insert(sample_id.clone()) {
            return Err(format!("duplicate original_sample_id: {sample_id}").into());
        }
    }
    if row_count == 0 {
        return Err("export contains no sample rows".into());
    }
    Ok(ExportValidation { row_count })
}

fn sha256_file(path: &Path) -> Result<String, AnyError> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn write_new(path: &Path, content: &[u8]) -> Result<(), AnyError> {
    use std::io::Write;

    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(content)?;
    file.sync_all()?;
    Ok(())
}

fn verify_published_files(record: &Record, files: &[LocalFile]) -> Result<(), AnyError> {
    let actual = record
        .files
        .iter()
        .map(|file| {
            (
                file.key.clone(),
                (file.size, normalize_checksum(file.checksum.as_deref())),
            )
        })
        .collect::<BTreeMap<_, _>>();
    verify_file_map(&actual, files)
}

fn draft_file_map(files: &[zenodo_rs::DepositionFile]) -> BTreeMap<String, (u64, String)> {
    files
        .iter()
        .map(|file| {
            (
                file.filename.clone(),
                (file.filesize, normalize_checksum(file.checksum.as_deref())),
            )
        })
        .collect()
}

fn verify_file_map(
    actual: &BTreeMap<String, (u64, String)>,
    files: &[LocalFile],
) -> Result<(), AnyError> {
    let expected = files
        .iter()
        .map(|file| {
            (
                file.archive_name.clone(),
                (file.size, file.md5.to_ascii_lowercase()),
            )
        })
        .collect::<BTreeMap<_, _>>();
    if actual != &expected {
        return Err(format!("remote file verification failed: {actual:?} != {expected:?}").into());
    }
    Ok(())
}

fn normalize_checksum(checksum: Option<&str>) -> String {
    let checksum = checksum.unwrap_or_default();
    checksum
        .strip_prefix("md5:")
        .unwrap_or(checksum)
        .to_ascii_lowercase()
}

fn validate_version(version: &str) -> Result<(), AnyError> {
    let number = version
        .strip_prefix('v')
        .ok_or("version must use the vN form, for example v3")?;
    if number.is_empty() || !number.chars().all(|character| character.is_ascii_digit()) {
        return Err("version must use the vN form, for example v3".into());
    }
    Ok(())
}

fn md5_file(path: &Path) -> Result<String, AnyError> {
    let mut file = File::open(path)?;
    let mut digest = Md5::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn default_license() -> String {
    "cc-by-4.0".to_owned()
}

#[cfg(test)]
mod tests {
    use super::{
        ReleaseConfig, load_manifest, next_version, normalize_checksum, prepare_release,
        validate_version,
    };
    use std::fs;
    use std::path::PathBuf;

    #[test]
    fn accepts_v_number_versions() {
        assert!(validate_version("v3").is_ok());
        assert!(validate_version("3").is_err());
        assert!(validate_version("vNext").is_err());
    }

    #[test]
    fn increments_versions() {
        assert_eq!(next_version(Some("v3")).unwrap(), "v4");
        assert!(next_version(Some("2026-08-06")).is_err());
        assert!(next_version(None).is_err());
    }

    #[test]
    fn normalizes_zenodo_md5_prefix() {
        assert_eq!(normalize_checksum(Some("md5:ABC123")), "abc123");
        assert_eq!(normalize_checksum(Some("ABC123")), "abc123");
        assert_eq!(normalize_checksum(None), "");
    }

    #[test]
    fn resolves_artifacts_relative_to_manifest() {
        let root =
            std::env::temp_dir().join(format!("dbgi-zenodo-publisher-test-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("artifact.tsv"), b"a\tb\n1\t2\n").unwrap();
        fs::write(
            root.join("publish.toml"),
            r#"
previous_record_id = 42
version = "v3"
title = "Example"
publication_date = "2026-08-06"
description_html = "<p>Example</p>"
creators = ["DBGI"]

[[files]]
archive_name = "artifact.tsv"
path = "artifact.tsv"
"#,
        )
        .unwrap();

        let (manifest, files) = load_manifest(&root.join("publish.toml")).unwrap();

        assert_eq!(manifest.version, "v3");
        assert_eq!(files[0].path, root.join("artifact.tsv"));
        assert_eq!(files[0].size, 8);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn prepares_an_immutable_release_directory() {
        let root =
            std::env::temp_dir().join(format!("dbgi-zenodo-release-test-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let source = root.join("source.tsv");
        fs::write(
            &source,
            b"original_sample_id\tfield_taxon_col_id\tfield_taxon_ref\tfield_taxon_accepted_name\n\
dbgi_001\t97H4\t1927\tAchillea millefolium L.\n",
        )
        .unwrap();
        let config_path = root.join("release.toml");
        fs::write(&config_path, "test config").unwrap();
        fs::write(
            root.join("README.template.md"),
            "{{version}} {{filename}} {{row_count}} {{sha256}} {{generated_at}}",
        )
        .unwrap();
        let config = ReleaseConfig {
            record_id: 42,
            title: "Example".to_owned(),
            description_html: "Release {{version}} on {{date}}".to_owned(),
            creators: vec!["DBGI".to_owned()],
            license: "cc-by-4.0".to_owned(),
            community: None,
            working_dir: PathBuf::from("."),
            output_dir: PathBuf::from("output"),
            readme_template: PathBuf::from("README.template.md"),
            export_command: vec![
                "cp".to_owned(),
                source.to_string_lossy().into_owned(),
                "{output}".to_owned(),
            ],
        };

        let release = prepare_release(&config_path, &config, 43, "v4").unwrap();

        assert_eq!(release.manifest.version, "v4");
        assert_eq!(release.manifest.previous_record_id, 43);
        assert!(release.manifest_path.ends_with("output/v4/publish.toml"));
        assert_eq!(release.files.len(), 2);
        fs::remove_dir_all(root).unwrap();
    }
}
