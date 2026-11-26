# office3 runaways (office direct, D arms, aaabcc33): causes

Where mp-work spends more than Codex on office (per 2 reps, mp-work vs Codex):

| Task | mp-work | Codex | Cause |
|---|---|---|---|
| pptx_chart | 1/2, $0.099, 69 calls | 2/2, $0.034 | Slides engine run (about 30 internal calls a build). Rep 1 left the deck at `Meshpool outputs/<job>/deck.pptx`; `./deck.pptx` never existed, so it failed. |
| xlsx_budget | 2/2, $0.053, 67 calls | 2/2, $0.017 | Contradictory notes. Office direct turns the hand-build guard off, but the system note still says "never write a .xlsx by hand with openpyxl" and the message note says "make it with make_spreadsheet". Rep 1 spent 27 calls reading Meshpool's own source for a way, built with engines.sheets.builder, then 17 calls of self-written value checks (59 calls in all). Rep 2 used openpyxl directly: 7 calls. |
| sb-56921 | 2/2, $0.046, 54 calls | 2/2, $0.020 | Real formula debugging (LibreOffice #VALUE! on a ragged SUMPRODUCT range). Not structural. |
| ob-1-15-2 | 2/2, $0.046, 57 calls | 2/2, $0.019 | "Append reference.txt to the docx" became XML forensics on content controls. Model over-verification, not structural. |

Every other office task is at or below Codex's cost.

Patch: docs/patches/0003-office-direct-notes-save-as.patch
- In office direct, the system note and the per-message note name the libraries (openpyxl, python-docx, python-pptx, reportlab), not engine tools, and no longer forbid writing by hand. MH_OFFICE_DIRECT_NOTE=0 restores the old notes.
- make_deck and make_spreadsheet take save_as. The built file is copied to that name in the project folder; an existing file is never replaced (it is saved as "name (2).ext").
- The engine-path note asks for save_as.

Expected effect: xlsx_budget and pptx_chart move toward Codex's direct path, about 8 to 16 calls a rep. It has not been measured yet: the gateway budget is at 0.
