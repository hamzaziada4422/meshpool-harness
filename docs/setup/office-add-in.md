# meshpool in Excel, PowerPoint and Word

The meshpool add-in puts a meshpool panel on the Home tab of Excel, PowerPoint and Word. Select some cells, text or slides, ask a question, and insert the answer back into your file.

Status: new. It is tested end to end with stand-in Office objects and the real meshpool app, but it has not yet been checked in real Excel, PowerPoint and Word on Windows and Mac. Please report what you see.

## Which Office

Microsoft 365, Office 2021 or later on Windows and Mac, and Office on the web. Office 2016 and 2019 run add-ins in an older browser engine that cannot run the panel; the panel says so instead of staying empty. Adding a slide needs PowerPoint with PowerPointApi 1.4.

## How it works

The panel is a web page that meshpool serves on this computer at `https://localhost:44317/office/taskpane.html`. Office loads it from there, so the panel only talks to the meshpool app running on your computer. meshpool then sends your request to the model you set up, the same as any other chat.

That HTTPS address serves only the panel and the few calls it makes (start a chat, send, read, stop, answer an approval once). The rest of the app is not reachable there, and the panel cannot pick a folder, an agent or a permission mode: its chats are plain Work chats with your usual settings.

## Connecting the panel

Anything on your computer can load the panel page, so the page carries no key. When you open the panel, it shows a six-digit code and asks the meshpool app to connect. The app shows the same code and asks you to allow it. Allow it only if the codes match. Until you do, the panel cannot start a chat, read your settings or answer an approval.

- The connection lasts until meshpool quits. Close and reopen the panel, or restart meshpool, and it asks again.
- The panel keeps its connection in memory, never on disk.
- If more than one request is waiting, the app shows each code. Allow only the one your panel shows, and deny the rest.
- Settings, Plugins, Office apps shows how many panels are connected, with a button that disconnects them all. Removing the add-in disconnects them too.

Office loads add-in panels only over HTTPS, from a certificate the computer trusts. meshpool makes its own certificate for localhost. The certificate authority that signs it is limited to `localhost` and `127.0.0.1`, and its private key is thrown away as soon as the certificate is signed, so it cannot vouch for any other site.

## Set it up

From the app: Settings, Plugins, Office apps, then Add meshpool to Office. Your system asks you to confirm the certificate (Windows shows a security warning, macOS asks for your password).

From a terminal:

```bash
meshpool office install
```

Then keep meshpool running, restart Excel, PowerPoint or Word, and click meshpool on the Home tab.

If you skipped trusting the certificate, run the install again (or click Trust the certificate in Settings): it asks again. The certificate lasts about two years; the install renews it when fewer than 30 days are left.

- Windows: meshpool registers the add-in under `HKEY_CURRENT_USER\Software\Microsoft\Office\16.0\WEF\Developer`, the same place Microsoft's developer tools use.
- Mac: meshpool copies the manifest into each app's `wef` folder under `~/Library/Containers`.
- Office on the web: Home, Add-ins, More add-ins, Upload My Add-in, then choose the manifest file the install printed. Your browser must trust the certificate too.

Other commands:

```bash
meshpool office status
```

```bash
meshpool office uninstall
```

`meshpool office install --apps excel,word` adds it to some apps only. `MH_OFFICE_PORT` changes the port (run the install again after changing it). `MH_OFFICE=0` turns the HTTPS listener off.

If you already used Microsoft's `npx office-addin-dev-certs install`, meshpool uses that certificate instead of making its own.

## What the panel sends

Only when "Include the selection" is ticked:

- Excel: the selected cells, up to 4,000, with formulas and their values.
- Word: the selected text, or the whole document when nothing is selected (up to 60,000 characters).
- PowerPoint: the selected text, or the text of every slide when nothing is selected.

Nothing changes in your file until you click an Insert button.

## What you can do with the answer

- Excel: Insert at selection (tables and formulas land at the active cell), Insert in a new sheet, Undo insert (puts back the values and formulas that were there; formatting is not restored). Formulas that reach outside the workbook, such as WEBSERVICE or HYPERLINK, go in as text, and if Excel refuses a formula the answer goes in as text.
- Word: Replace selection or Insert after, with headings, lists, bold and tables kept.
- PowerPoint: Insert on slide, or New slide (the first line becomes the title; needs PowerPoint with PowerPointApi 1.4).
- Copy, in every app.

When meshpool needs your approval or asks a question, the panel shows it, and you answer there.

## Tests

- `app/tests/test_office.py`: certificates, manifest, sideloading on Windows and Mac, the HTTPS listener, and the panel's parsing and rendering.
- `testkit/office_panel.js`: the panel in Excel, Word and PowerPoint with stand-in Office objects, against the real app and the mock model.
- `testkit/office_settings.js`: Settings, Office apps, add and remove.
