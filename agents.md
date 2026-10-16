# Build Ledger: project guide for AI coding agents

Build Ledger is a mobile-friendly, end-to-end encrypted cost tracker for one-off art/woodwork builds. It tracks per-project build cost
(stock lots, tools, labour, travel), vendors and purchases (with GST), customers, invoices, reports and documents.
It runs as a single HTML file, optionally with a small Python server that stores only encrypted data.

## Layout
- `index.html`: the whole app (vanilla JS, inline CSS, no framework, no build step). About 100 KB.
- `server.py`: Python 3 standard-library server. Serves `index.html`, stores encrypted vaults and optional document files.
- `config.yaml`, `Dockerfile`, `README.txt`: Home Assistant add-on packaging (add-on folder is `/addons/build_ledger/`).
- `../build_ledger_phone_app/`: Capacitor wrapper project so documents can be saved as real files in phone storage.
- `../artcost/index.html`: duplicate of `index.html` for plain static hosting in `/config/www/artcost/`. Keep identical to `index.html`
  (edit one, copy to the other).

## Run and check
- Syntax check: extract the `<script>` body and run it through `new Function(...)` in Node.
- Run the server: `DATA_DIR=/tmp/bl DOCS_DIR=/tmp/bl-docs WEB_DIR=. python3 server.py`, then open `http://localhost:8099`.
  `localhost` counts as a secure context; any other address must be `https://` or WebCrypto is unavailable.
- Static mode (no server): open the page over https; data stays in the browser. The app detects the server by calling `api/ping`.
- There is no test suite in the repo yet. Past testing used jsdom + `fake-indexeddb` + Node's `webcrypto`, driving the real HTML.
  Adding such tests is welcome.

## Architecture of the app
- State is one in-memory object `db`: `projects, items (each with lots), uses, costs, bills, tools, vendors, customers, purchases, docs (metadata only), set, company`.
  `norm()` fills defaults and migrates old data; call it after any load or import.
- Persistence: `save()` (debounced) calls `flush()`, which encrypts `{db, auth: AUTH}` and writes a v3 vault:
  localStorage (local mode) or `PUT api/vault` (server mode, with revision check).
- Rendering: `render()` rebuilds the current view from `S = {v, id, sub}` using template strings. Navigate with `go(view, id, sub)`.
- UI forms use `sheet(title, fields, onOk, label, extra)` (bottom sheet). The sheet closes first, then runs `onOk`; return `false` to keep it open.
- Stock uses lots: each purchase creates its own lot with its own unit cost. Usage takes oldest lot first (`take`) and records `al`
  allocations so deletes restore exact lots. `mergeInto`/`mergeItems` average unit cost and re-point references.
- Invoices store `net, gst, price (total), gstMode, gstRate`. Numbers look like `PREFIX-YYYYMMDD-001`.
- Reports come from `repData()`; CSV via `csvS()`; printing uses the sheet and print CSS.

## Security model (do not weaken)
- A random data key (DEK, AES-256-GCM) encrypts everything. The DEK is wrapped twice: by a key from the password
  (PBKDF2-SHA-256, 600,000 rounds) and by a key from a one-time recovery key.
- `kekAuth()` derives 64 bytes from one PBKDF2 run: first 32 bytes wrap the DEK, last 32 bytes are the server login credential.
- The server stores only: salts, a hash of each login credential, an opaque encrypted vault, a revision counter, and files.
  It must never receive passwords, recovery keys, the DEK or readable data.
- Encrypted exports use the `BLX1` format: magic + 16-byte salt + 4-byte iteration count + 12-byte IV + AES-GCM ciphertext (AAD `BLX1`).
- Two-factor (TOTP) and recovery codes live inside the encrypted vault and are checked in the app after unlock.
- Rules: never log secrets, never persist plaintext, keep AAD strings (`vault`, document id, `BLX1`) unchanged, bump the vault `v` field
  and keep a migration path if the format changes. Existing vault versions: v2 (password key used directly), v3 (wrapped DEK).

## Server API (`server.py`)
`GET api/ping`, `GET api/kdf?user=`, `POST api/register`, `GET|PUT api/vault`, `GET|PUT|DELETE api/doc/<id>`, `GET|PUT|DELETE api/blob/<id>`,
`DELETE api/account`. Auth headers: `X-User`, `X-Auth`. Per-user attempt limiting with backoff, invite code and user cap from add-on options.
`PUT api/vault` uses `baseRev`; a mismatch returns 409 and the app shows a keep-or-load dialog.

## Document storage (`docStore()` in the app)
`native` (Capacitor Filesystem, Documents/BuildLedger/...), `folder` (File System Access API, desktop only), `server` (real files under
`DOCS_DIR/<user>/<kind>/<ref>/`), `browser` (encrypted IndexedDB). Each `db.docs` entry records `store`, `path`, `enc`.
Files are encrypted (`.enc`, 12-byte IV prefix) unless the user chooses readable files.

## Conventions and gotchas
- Preview panes may sandbox pages: do not use `<form>` submit; use button handlers. Use `copyText()` and `download()` helpers, which have fallbacks.
- Third-party scripts (Tesseract for scanning, qrcode-generator for 2FA QR) load lazily from CDNs; the app must work without them.
- Keep the app a single file unless there is a strong reason; keep all user-facing text plain and friendly.
- Currency, GST and date formats are Australian by default (dd/mm/yyyy parsing in scan, ABN, 10% GST default).

## Known limits
- 2FA is enforced in the app, not by the server. PBKDF2 is used because Argon2 is not built into browsers.
- If the server is unreachable, changes are retried every 15 seconds but are lost if the page is closed first.
- Home Assistant add-on packaging and the phone wrapper were written but not run on real devices. Android's Documents-folder write rules may
  need the `NDIR` constant changed (see the phone app README).
- Home Assistant ingress URLs contain a temporary token, so the phone app needs a stable https address to the add-on's port.

## Roadmap (not built)
1. Local SQLite storage (sql.js) with an encrypted change log and a sync queue: local write, queue, push/pull through the server,
   newest-edit-wins merge. CR-SQLite and ElectricSQL were rejected because they merge by reading rows on the server, which breaks end-to-end encryption.
   Documents and images never sync.
2. Over-the-air app updates on the phone that keep stored data.
3. Backup of code and data to Google Drive or OneDrive, with restore on another device.
4. One-command uninstall of everything installed or stored (server data, docs, phone app data).
5. Per-section choice (synced or local-only) on the company details page; pulling linked documents from the phone into the Home Assistant view without storing them on the server.
