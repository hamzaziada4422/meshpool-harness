# Photo renamer

Write `rename.sh`, a bash script run as `bash rename.sh <folder>`. It renames the files directly inside
`<folder>` (not in subfolders) according to these rules:

1. Camera files named `IMG_YYYYMMDD_HHMMSS.<ext>` (example: `IMG_20240315_142233.jpg`) become
   `YYYY-MM-DD_HH-MM-SS.<ext>` (example: `2024-03-15_14-22-33.jpg`).
2. Phone screenshots named `Screenshot YYYY-MM-DD at HH.MM.SS.<ext>` (note the spaces) become
   `screenshot_YYYY-MM-DD_HH-MM-SS.<ext>`.
3. In every renamed file the extension is lower-cased (`.JPG` -> `.jpg`, `.PNG` -> `.png`), and `.jpeg` becomes
   `.jpg`.
4. Files that match neither pattern are left exactly as they are (name and extension untouched).
5. If the target name already exists, append `_1` before the extension; if that exists too, `_2`, and so on.
   Files are processed in byte-wise sorted order of their original names (`LC_ALL=C sort`).
6. The script prints nothing on success and exits 0. It must work with names that contain spaces.
