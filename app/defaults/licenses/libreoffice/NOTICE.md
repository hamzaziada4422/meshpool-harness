Meshpool ships LibreOffice, unmodified, inside the app (folder `vendor/libreoffice`) and uses it only for converting Office files, recalculating spreadsheets and rendering slides. Meshpool never uses a LibreOffice installed on your computer.

- Release: LibreOffice 26.2.7, the official build from The Document Foundation (https://www.libreoffice.org). The exact file and its SHA-256 are recorded in `vendor/libreoffice/MESHPOOL-LIBREOFFICE.json`.
- Licence: Mozilla Public License 2.0 (text in `MPL-2.0.txt` beside this file). LibreOffice also contains components under other open-source licences; each is listed in the LibreOffice folder itself (macOS: `LibreOffice.app/Contents/Resources/` license files; Windows: `program/` and `share/` and the `LICENSE` and `NOTICE` files).
- Source code of this release: https://download.documentfoundation.org/libreoffice/src/26.2.7/ (and https://git.libreoffice.org/core). Meshpool made no changes to it.
- "LibreOffice" and "The Document Foundation" are trademarks of their owners; Meshpool is not affiliated with or endorsed by them.
