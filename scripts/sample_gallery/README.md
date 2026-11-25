# Sample photo gallery

## Open it on a laptop

Use a terminal with OpenSSH and the account or SSH key normally used for the Commons server. Connect to the network or VPN used for SSH access to that server. Run this command on the laptop and leave the terminal open.

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:/run/user/1001/sample-photo-gallery/http.sock allardpm@134.21.20.118
```

Open <http://127.0.0.1:8765/> in the laptop browser. Search for a full or partial sample code, select a result and scroll to its Directus field metadata. Select **Open full size** beneath a preview to inspect the stored Nextcloud image in a new tab. `Ctrl+C` in the terminal closes the tunnel. If port `8765` is already in use, change it in both the command and the URL. If the SSH connection normally uses a jump host or a configured host alias, use that same route.

## Data and access

This is a read-only snapshot. It joins `Field_Data` records from Directus with JPEGs in the EMI Nextcloud `output/pictures` and `output/pictures_raw` folders. QField's `picture_map.json` supplies the names of renamed files. The catalog preserves missing references and distinguishes photos without a matching field record. Full-size links serve the archived file bytes. The private service makes smaller previews on demand. The page shows the snapshot date in UTC.

Access requires the server account. The server listens on a Unix socket in that account’s private runtime directory. The web process reads the curated export and does not need a Directus password or access to Nextcloud. Field records may include collector names and precise coordinates. Keep the export private unless permission to redistribute those data and images has been established.

## Build or refresh on the server

Run from the repository root. The exporter uses the repository's Python environment, a readable Directus `.env`, and the Commons server's QField map at `/media/data/qfieldcloud_data/data/picture_map.json`. It reads the two photo folders as `www-data` through the existing `nextcloud_app` container. Only the export operator needs Docker access. Allow space for a separate copy of the selected JPEGs for each snapshot.

```sh
set -e
umask 077
base="$HOME/.local/share/sample-photo-gallery"
snapshot="$base/snapshots/$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python scripts/sample_gallery/export.py "$snapshot"
ln -s "$snapshot" "$base/site-next"
mv -Tf "$base/site-next" "$base/site"
```

The export must finish before switching the `site` link. Reload the browser after switching. Retain a previous snapshot if rollback is needed.

The host uses NGINX with its HTTP image filter module and a `systemd` user service. To install the service on this host or apply changes to its configuration, run the following commands from the repository root after building the snapshot.

```sh
base="$HOME/.local/share/sample-photo-gallery"
install -d -m 700 "$base" "$HOME/.config/systemd/user"
install -m 600 scripts/sample_gallery/nginx.conf "$base/nginx.conf"
install -m 600 scripts/sample_gallery/sample-photo-gallery.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable sample-photo-gallery.service
systemctl --user restart sample-photo-gallery.service
```

To diagnose a connection failure on the server, run `systemctl --user status sample-photo-gallery.service` and `curl --unix-socket "$XDG_RUNTIME_DIR/sample-photo-gallery/http.sock" -I http://localhost/`. No public server port is required.
