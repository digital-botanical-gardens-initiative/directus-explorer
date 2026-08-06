use std::path::{Path, PathBuf};

use clap::{Parser, Subcommand};
use dbgi_zenodo_publisher::{
    StageOutcome, describe_plan, load_manifest, load_release_config, next_version, prepare_release,
    publish_draft, publish_version, stage_version,
};
use zenodo_rs::{DepositionId, RecordId, ZenodoClient};

#[derive(Debug, Parser)]
#[command(about = "Safely publish versioned DBGI artifacts to Zenodo")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Export, validate, version, stage, and publish the next DBGI release.
    Release {
        config: PathBuf,
        /// Stop after preparing and verifying the Zenodo draft.
        #[arg(long)]
        draft_only: bool,
    },
    /// Validate the manifest and local artifacts without contacting Zenodo.
    Plan { manifest: PathBuf },
    /// Create or reuse a version draft, replace its files, and verify it.
    Draft { manifest: PathBuf },
    /// Stage, verify, and publish the requested version.
    Publish { manifest: PathBuf },
    /// Verify and publish one exact draft ID without restaging it.
    PublishDraft {
        manifest: PathBuf,
        #[arg(long)]
        draft_id: u64,
    },
    /// Display the latest public version in the record family.
    Status { manifest: PathBuf },
}

#[tokio::main]
async fn main() -> Result<(), dbgi_zenodo_publisher::AnyError> {
    let cli = Cli::parse();
    if let Command::Release { config, draft_only } = &cli.command {
        return run_release(config, *draft_only).await;
    }
    let manifest_path = match &cli.command {
        Command::Release { .. } => unreachable!(),
        Command::Plan { manifest }
        | Command::Draft { manifest }
        | Command::Publish { manifest }
        | Command::PublishDraft { manifest, .. }
        | Command::Status { manifest } => manifest,
    };
    let (manifest, files) = load_manifest(manifest_path)?;

    if matches!(cli.command, Command::Plan { .. }) {
        print!("{}", describe_plan(&manifest, &files));
        return Ok(());
    }

    let client = ZenodoClient::from_env()?;
    match cli.command {
        Command::Release { .. } => unreachable!(),
        Command::Plan { .. } => unreachable!(),
        Command::Draft { .. } => match stage_version(&client, &manifest, &files).await? {
            StageOutcome::AlreadyPublished(record) => {
                println!(
                    "{} is already published as record {}",
                    manifest.version, record.id
                );
            }
            StageOutcome::Draft(id) => println!("Verified draft {id} for {}", manifest.version),
        },
        Command::Publish { .. } => {
            let record = publish_version(&client, &manifest, &files).await?;
            println!(
                "Published {} as https://zenodo.org/records/{}",
                manifest.version, record.id
            );
            if let Some(doi) = record.doi {
                println!("DOI: {}", doi.0);
            }
        }
        Command::PublishDraft { draft_id, .. } => {
            let record = publish_draft(&client, DepositionId(draft_id), &manifest, &files).await?;
            println!(
                "Published {} as https://zenodo.org/records/{}",
                manifest.version, record.id
            );
            if let Some(doi) = record.doi {
                println!("DOI: {}", doi.0);
            }
        }
        Command::Status { .. } => {
            let record = client
                .get_latest_record(RecordId(manifest.previous_record_id))
                .await?;
            println!("Latest record: https://zenodo.org/records/{}", record.id);
            println!(
                "Version: {}",
                record.metadata.version.as_deref().unwrap_or("<unset>")
            );
        }
    }
    Ok(())
}

async fn run_release(
    config_path: &Path,
    draft_only: bool,
) -> Result<(), dbgi_zenodo_publisher::AnyError> {
    let config = load_release_config(config_path)?;
    let client = ZenodoClient::from_env()?;
    let latest = client.get_latest_record(RecordId(config.record_id)).await?;
    let version = next_version(latest.metadata.version.as_deref())?;
    let prepared = prepare_release(config_path, &config, latest.id.0, &version)?;
    println!("Prepared {}", prepared.manifest_path.display());
    print!("{}", describe_plan(&prepared.manifest, &prepared.files));

    if draft_only {
        match stage_version(&client, &prepared.manifest, &prepared.files).await? {
            StageOutcome::AlreadyPublished(record) => {
                println!("{version} is already published as record {}", record.id);
            }
            StageOutcome::Draft(id) => println!("Verified draft {id} for {version}"),
        }
        return Ok(());
    }

    let record = publish_version(&client, &prepared.manifest, &prepared.files).await?;
    println!(
        "Published {version} as https://zenodo.org/records/{}",
        record.id
    );
    if let Some(doi) = record.doi {
        println!("DOI: {}", doi.0);
    }
    Ok(())
}
