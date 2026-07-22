# MS File Handover Lists

This folder contains Directus exports that map each mass spectrometry file name
to the corresponding upper-level sample id.

Use the project-specific files in `by_project/` when preparing uploads for one
research project:

```text
by_project/artemisia_absinthium__ms_filename_sample_id.tsv
by_project/bioblitz_vinesch__ms_filename_sample_id.tsv
by_project/champex_gradient__ms_filename_sample_id.tsv
by_project/droguier_jbn__ms_filename_sample_id.tsv
by_project/heloise_bachelor_FiBL__ms_filename_sample_id.tsv
by_project/jbc__ms_filename_sample_id.tsv
by_project/jbn__ms_filename_sample_id.tsv
by_project/jbp__ms_filename_sample_id.tsv
by_project/jbuf__ms_filename_sample_id.tsv
by_project/sbl_20004__ms_filename_sample_id.tsv
```

`ms_filename_sample_id_by_project.tsv` is the combined file for all projects.
`ms_filename_sample_id_unresolved.tsv` contains MS rows that could not be linked
back to a sample id and should not be used for routine handover.

## Important Columns

- `sample_id`: upper-level sample id, for example `grad_000001` or
  `drog_000469`.
- `ms_filename`: file name stored in Directus. It usually has no extension.
- `qfield_project`: project name in Directus.
- `injection_method`: acquisition method associated with the Directus MS row.

## Finding All Files From One TSV

The actual raw files can have either `.mzML` or `.mzXML` extension. The TSV
stores the filename stem in the `ms_filename` column, usually without the file
extension.

The easiest way to find all files for one project is to choose the project TSV
and the directory where the MS files should be searched. Avoid searching the
whole computer unless needed, because it can take a long time.

Example for Champex gradient:

```bash
PROJECT_NAME="champex_gradient"
PROJECT_TSV="/path/to/champex_gradient__ms_filename_sample_id.tsv"
SEARCH_DIR="/path/to/folder/containing/ms/files"
UPLOAD_DIR="${PROJECT_NAME}_files_for_upload"
```

Find all matching `.mzML` and `.mzXML` files once, and save the result:

```bash
awk -F '\t' 'NR > 1 {print $3}' "$PROJECT_TSV" |
while read -r stem; do
  find "$SEARCH_DIR" \( -iname "${stem}.mzML" -o -iname "${stem}.mzXML" \)
done > found_ms_files.txt
```

Copy all found files to the project upload folder:

```bash
mkdir -p "$UPLOAD_DIR"

while read -r file; do
  cp "$file" "$UPLOAD_DIR/"
done < found_ms_files.txt
```

Check how many files were expected and how many were copied:

```bash
expected=$(awk -F '\t' 'NR > 1 {print $3}' "$PROJECT_TSV" | sort -u | wc -l)
found=$(find "$UPLOAD_DIR" -type f \( -iname '*.mzML' -o -iname '*.mzXML' \) | wc -l)
echo "Expected: $expected"
echo "Copied:   $found"
```

Optional: make a missing-file report from the copied files:

```bash
awk -F '\t' 'NR > 1 {print $3}' "$PROJECT_TSV" | sort -u > expected_stems.txt

find "$UPLOAD_DIR" -type f \( -iname '*.mzML' -o -iname '*.mzXML' \) \
  -exec basename {} \; |
sed -E 's/\.(mzML|mzXML)$//' |
sort -u > found_stems.txt

comm -23 expected_stems.txt found_stems.txt > missing_stems.txt
```

If you really need to search from the current folder, set:

```bash
SEARCH_DIR="."
```

`missing_stems.txt` will contain filename stems from the TSV that were not found
in the project upload folder.

## Finding One File Manually

For occasional manual checks, search for the `ms_filename` value with either
extension. If the TSV contains:

```text
20260903_Mazz_Sample_grad_000001_01
```

then search for:

```text
20260903_Mazz_Sample_grad_000001_01.mzML
20260903_Mazz_Sample_grad_000001_01.mzXML
```

## Future Improvement

The current handover lists store the Directus filename stem, while files on disk
may be `.mzML` or `.mzXML`. We should add a future workflow that scans a file
directory, matches both extensions automatically, and reports which expected
files are present, missing, duplicated, or extension-mismatched.
