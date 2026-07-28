# desktop-support-tools

Webapps plus matching CLI scripts that help Thunderbird support staff and
volunteers troubleshoot Thunderbird **Desktop**. Modelled on
[thunderbird/dns-scripts](https://github.com/thunderbird/dns-scripts).

The first tool checks whether the Thundermail settings in a user's
Troubleshooting Information are correct. Gmail, Microsoft and other providers
come after.

## What these tools are for

A support person pastes a user's Troubleshooting Information and gets one
verdict that answers four questions at once:

1. **Which settings to change** — actual vs. correct, with exact values.
2. **Thunderbird or the server?** — settings correct means escalate server-side
   or file a bug; settings wrong means the user can self-serve.
3. **Is this a known bad config?** — matched against a catalogue.
4. **Fewer round trips** — one paste, full verdict.

Output is aimed at a volunteer who does *not* already know the correct values.
Follow `dns-scripts`' remediation style: name the exact field in Thunderbird's
Account Settings UI and the exact value to put in it.

## Commands

Managed with [`uv`](https://docs.astral.sh/uv/), like `dns-scripts`.

```sh
uv sync                                                  # set up
uv run --with pytest pytest -q                            # tests
uv run parse_troubleshooting.py fixtures/thundermail-correct.txt
pbpaste | uv run parse_troubleshooting.py                 # parse a real paste
python3 -m http.server 8000                               # serve the webapp
```

## Architecture rules

**Static and client-side only.** The webapp is plain HTML/CSS/JS on GitHub
Pages with no build step. Nothing is uploaded, nothing is stored, no backend.
This is not a preference — it is what makes the privacy rule below true by
construction rather than by policy.

**One source of truth per fact.** Expected settings live in a JSON file read by
*both* the CLI and the webapp, the way `dns-scripts` uses `records.json`. Add a
provider or a field once and both front-ends pick it up. Never hard-code an
expected value in either front-end.

**The parser exists twice, so fixtures are the contract.** Mirroring
`dns-scripts` means Python for the CLI and JS for the webapp. `dns-scripts` only
shared *data* that way, which was fine because its logic was trivial DNS
lookups; here the parser is the complex part. Every fixture in `fixtures/` has a
`.expected.json` companion, and **both** implementations must be asserted
against it. That harness is the only thing stopping the two from drifting.

**Never store or emit PII.** Email addresses and login names must not appear in
output, logs, or committed fixtures beyond what a test needs. See below for why
this costs nothing.

**Flag unverified inferences.** `dns-scripts` marks providers as *unverified*
when their behaviour was inferred from docs or a screenshot rather than confirmed
live, and says so in the output itself. Keep that habit. A confidently-worded
wrong answer sends a volunteer down a dead end.

## Troubleshooting Information format

Verified against comm-central; don't re-derive it from guesses.

**There is no JSON export.** `mail/components/about-support/content/aboutSupport.xhtml`
has "Copy text to clipboard" only — the "Copy raw data to clipboard" button
Firefox has is commented out, marked *"Not used on TB"*. Text is the only input
format we can rely on.

**But "Copy text to clipboard" also puts a `text/html` flavour on the clipboard**,
and it is the rendered DOM, not the text serialisation. Verified on Windows 11 /
TB 153 with `Get-Clipboard -TextFormatType Html`: a `CF_HTML` payload whose
accounts section is a real `<table id="accounts-table">`, one `<td>` per field:

```html
<tr><td rowspan="1">account1</td><td rowspan="1" class="data-private"></td>
<td rowspan="1">(imap) imap.gmail.com:993</td><td rowspan="1">3</td><td rowspan="1">10</td>
<td rowspan="1" class="data-private"></td><td rowspan="1">smtp.gmail.com:465</td>
<td rowspan="1">3</td><td rowspan="1">10</td><td rowspan="1">true</td></tr>
```

The string `INCOMING` appears nowhere in it — those prefixes exist only in
`getAccountsText()`. Four things this format has that the text does not:

- **Fields are cells**, so both comma traps below (commas inside `name`, trailing
  empty fields) simply do not arise.
- **`class="data-private"` marks the private cells explicitly**, so PII is
  identified by markup rather than by position.
- **`rowspan` on the account-level cells** encodes accounts with several outgoing
  servers; an account with no outgoing server (Local Folders) just ends its row
  early, so cell counts vary per row.
- **`data-l10n-id` survives the copy** (`app-basics-version`, `accounts-title`),
  which would make App Basics parsing locale-neutral and lift the English-only
  limitation on version detection noted below.

It does *not* solve everything: `hostDetails` is still one cell
(`(imap) imap.gmail.com:993`), needing the same protocol/host/port regex.

Both flavours are registered deliberately — `getClipboardTransferable()` calls
`addDataFlavor("text/html")` and `addDataFlavor("text/plain")` — so which one a
dump arrives in is decided by where the user pasted it, not by chance. A plain
`<textarea>`, including the webapp's paste box, takes `text/plain`. A rich-text
target takes the table, and the "Send via email" button sends HTML. **So a dump
forwarded through rich-text mail can reach support in a structurally different
shape, one that never passed through `getAccountsText()` at all.** Do not assume
a paste is the text format; a table flattened back to text does not carry the
`INCOMING:` prefixes the parser keys on.

Text remains the contract for the CLI and for `fixtures/`, because `pbpaste` has
no equivalent path and a parser only one front-end can exercise is precisely the
drift `fixtures/` exists to prevent. But the HTML shape is a real input for
Bucket 3, not merely a curiosity: the webapp gets it for free from a `paste`
event via `clipboardData.getData("text/html")`. When that is built, parse it with
`DOMParser` and never assign it into the live document.
`fixtures/tb153-windows-accounts.html` is a captured sample to build against; it
has no `.expected.json` yet because nothing parses it.

The numbers `3` and `10` in that markup are worth noting separately: the DOM
carries raw integers too, so the `gSocketTypes` fallback described below is not
an artefact of the text serialiser but of the shared lookup both paths use.

The private-data toggle is labelled **"Include account names"** in the UI (the
underlying class is `CLASS_DATA_PRIVATE`). Unchecked is the default.

**Accounts serialisation**, from `getAccountsText()` in
`mail/components/about-support/content/accounts.js`:

```
account1:
  INCOMING: account1, <name>, (imap) mail.thundermail.com:993, 3, 10
  OUTGOING: <identityName>, mail.thundermail.com:465, 3, 10, true
```

- INCOMING fields: `key`, `name`, `hostDetails`, `socketType`, `authMethod`.
- OUTGOING fields: `identityName`, `name`, `socketType`, `authMethod`, `isDefault`.
- App Basics labels (`Version:`, `OS:`) are **localised**, so version detection
  is best-effort and English-only. Anything version-dependent must treat an
  unknown version as unknown, not assume one.

**`socketType` and `authMethod` may be numbers or names — accept both.** Reading
`accounts.js` suggests it writes language-neutral names, and it tries to: it
looks them up in a table built from `Object.entries(Ci.nsMsgSocketType)`. But
when that enumeration yields nothing the code falls back to the raw integer
(`aIndex in gSocketTypes ? gSocketTypes[aIndex] : aIndex`), and **Thunderbird 153
takes the fallback**. A current dump reads `, 3, 10`. Confirmed against real
153.0 dumps on **both** macOS and Windows 11, so the build decides this and not
the platform; older dumps may still carry names. Normalise to the name.

Values from `nsMsgSocketType` / `nsMsgAuthMethod` in
`mailnews/base/public/MailNewsTypes2.idl`:

| `socketType` | | `authMethod` | |
|---|---|---|---|
| 0 | `plain` | 1 `none` | 6 `NTLM` |
| 1 | `trySTARTTLS` (removed from the IDL; old profiles still hold it) | 2 `old` | 7 `External` |
| 2 | `alwaysSTARTTLS` | 3 `passwordCleartext` | 8 `secure` |
| 3 | `SSL` | 4 `passwordEncrypted` | 9 `anything` |
| | | 5 `GSSAPI` | 10 `OAuth2` |

This is the clearest lesson so far: reading comm-central tells you what the code
*means* to emit, not what it emits. Validate format assumptions against a real
dump before building on them.

Three parsing traps, all covered by fixtures:

- **Commas inside `name`.** Field counts are fixed and only the private field is
  free-form, so parse positionally from the fixed end and let it absorb the
  slack. Never split-and-count from the left.
- **Trailing empty fields.** `export.js` runs `text.replace(/[ \t]+\n/g, "\n")`,
  so a line whose last field is empty ends in `,` not `, `. Split on `,` and
  strip; splitting on `", "` loses a field and shifts everything.
- **Accounts Thunderbird itself can't read** produce a placeholder record with an
  empty `hostDetails` and the literal string `undefined` as `socketType` (there
  is a `sokectType` typo in the `catch` branch upstream). That's a finding to
  report, not a parse failure.

Windows builds copy with CRLF. Accept both. Confirmed by capturing the clipboard
byte-exactly on Windows 11 / TB 153 (`Get-Clipboard -Raw` piped to
`[IO.File]::WriteAllText`, never through a terminal or editor, both of which
normalise): 468 CRLF and **zero** lone LF. That includes the newlines *inside*
multi-line Graphics values such as the WebGL WSI blobs, so a real dump is never
mixed-ending — the parser handles mixed input anyway, but no fixture needs to.

`export.js` is what writes them, explicitly and Windows-only, in
`createTextForElement()`:

```js
if ("@mozilla.org/windows-registry-key;1" in Cc) {
  text = text.replace(/\n/g, "\r\n");
}
```

That is a whole-document substitution, which is why even newlines *inside* a
single multi-line value come out CRLF, exactly as the capture shows.

## Why the no-PII rule is free

Thunderbird marks only `name` (incoming, usually the email address) and
`identityName` (outgoing) as private; "Include account names" blanks them while
leaving the comma separators intact. Everything needed to judge a configuration —
`hostDetails`, `socketType`, `authMethod` — is public and **always present**. So
the parser discards the two private fields unconditionally, whether or not the
user pasted them, and loses nothing. Records that private data *was* present, so
the UI can reassure the user it was dropped.

Verified end to end: the `tb153-macos-names-included` and
`tb153-macos-names-hidden` fixtures are the same profile with only that checkbox
changed, and a test asserts their parsed account settings are identical. PII
presence cannot alter a verdict.

Fixtures derived from real dumps have their identities scrubbed to `example.com`
addresses. Keep the structure verbatim and change only the identifying values.

## Thundermail expected settings

From `pulumi/config.prod.yaml` in
[thunderbird/thunderbird-accounts](https://github.com/thunderbird/thunderbird-accounts),
confirmed against the Thundermail "View Server Settings" UI.

| Protocol | Host | Port | `socketType` | `authMethod` | Provenance |
|---|---|---|---|---|---|
| IMAP | `mail.thundermail.com` | 993 | `SSL` | `OAuth2` | `_imaps._tcp` SRV, UI, prod config — all agree |
| SMTP | `mail.thundermail.com` | 587 | `alwaysSTARTTLS` | `OAuth2` | `_submission._tcp` SRV — what autoconfig produces |
| SMTP | `mail.thundermail.com` | 465 | `SSL` | `OAuth2` | Thundermail UI's manual instructions |

**Both SMTP rows are valid — accept either.** Thundermail has no ISPDB entry, so
Thunderbird autoconfigures from RFC 6186 SRV records, and Thundermail publishes
`_submission._tcp` → **587** (`clients.py:741` in thunderbird-accounts;
`records.json` in dns-scripts). Under RFC 6186 `_submission._tcp` is the STARTTLS
service on 587, as opposed to `_submissions._tcp` for implicit TLS on 465. So an
autoconfigured account lands on 587/`alwaysSTARTTLS` while the Thundermail web UI
tells users to type 465/`SSL`.

Encoding only 465/`SSL` — which is what `config.prod.yaml` and the UI screenshot
suggest in isolation — would report a false failure for **every autoconfigured
Thundermail account**. This is why `settings.json` stores a *set* of acceptable
(port, socketType) pairs per protocol with a provenance label, not one expected
value per field. Verified against a real profile containing both variants.

The DNS-vs-UI inconsistency is Thundermail's, not ours, and is worth raising with
that team rather than papering over in this tool.

- **POP is not offered.** No POP host or port exists in production config and
  the Thundermail UI has no POP tab. The verdict for a POP account is "POP isn't
  supported — use IMAP", not "your POP settings are wrong".
- **JMAP** is served on `mail.thundermail.com:443`, but **Thunderbird Desktop
  cannot use it yet** (expected 2026/2027). The settings schema is keyed by
  protocol so JMAP can be enabled later without restructuring; until then a
  `(jmap)` account reports that TB Desktop doesn't support it.
- **`OAuth2` is the default and recommended auth.** `passwordCleartext` (an app
  password) also works but is discouraged. This is why verdicts are
  **pass / warn / fail**, not pass/fail: reporting a working app-password
  account as broken sends support down the wrong path. Note the same value is a
  hard failure for providers that mandate OAuth2 — hence per-field severity in
  the settings JSON.
- `passwordCleartext` over `SSL` is fine. Over a `plain` socket it is a genuine
  defect. Judge the pair, not the field alone.
- **No ISPDB entry** — `autoconfig.thunderbird.net/v1.1/thundermail.com` 404s, so
  Thunderbird discovers Thundermail via RFC 6186 SRV records. That's why
  `dns-scripts` checks `_imaps._tcp` and `_submission._tcp`; the two tools are a
  natural cross-link, and those records are the *reason* an account's settings
  look the way they do.

## Stale and deleted accounts

A dump can contain accounts the user no longer uses, and **nothing in it marks an
account as dead**. Do not try to infer it.

**Troubleshooting Information can list accounts that have already been deleted.**
Confirmed end to end on a real profile. An account deleted via Account Settings →
Delete was removed from `prefs.js` immediately, but kept appearing in dumps because
`about:support` reads the account manager's live in-memory list rather than
`prefs.js`. After quitting and reopening Thunderbird the account was gone from the
dump. So when a user says they deleted an account the dump still shows, the remedy
is **restart Thunderbird and re-copy** — surface that in the UI rather than
treating the dump as wrong or the user as mistaken.

**A dump is not a complete list of accounts.** The Smart Mailboxes pseudo-account
(`hostname: "smart mailboxes"`, `type: none`) is present in
`mail.accountmanager.accounts` but never appears in `about:support`, even though
Local Folders — also `type: none` — does. So never show the user a count ("account
2 of 4") and never infer anything from an account being absent. Dump *order* is
reliable: `accounts.js` sorts by account number via `idCompare`.

- **Verdicts are per-account. Never emit one overall verdict**, and never let one
  account's failure set the process exit code — otherwise the tool tells someone
  to fix settings on a mailbox they abandoned.
- **"Which account?" is an input, not an inference.** When several accounts match
  a provider, the webapp asks the support person to pick and the CLI takes
  `--account`. Guessing wrong is worse than asking.
- **Several accounts on one host is a disambiguation prompt, not a fault** — and
  it's the most useful thing the tool can say in that situation.
- **Do not use gaps in account keys as a staleness signal.** Keys skip numbers
  whenever an account is deleted, which says deletions happened but never which
  survivor is stale. Cheap to compute, misleading to surface.
- Because names are discarded, accounts are distinguishable only by key and
  settings — and keys appear nowhere in Thunderbird's UI. Identify accounts to
  users by protocol/host/port and dump order, never by key alone.

### Account selection: the address is an optional accelerator, never required

- **Provider is detected from the incoming hostname**, not from an email address.
  `mail.thundermail.com` → Thundermail. Needs no user input and still works for
  custom domains, since the SRV target is the same host.
- **Report every account by default.** Often no selection is needed: if one
  Thundermail account shows SMTP 587 and another 465, showing both verdicts tells
  the story faster than forcing a choice.
- **If the user supplies an address and the dump happens to include names**, match
  it in the browser to preselect that account, then discard the address. Never
  stored, never sent anywhere.
- **Otherwise offer a picker** labelled by protocol/host/port and position.
- **Never instruct users to tick "Include account names."** Requiring the address
  would mean requiring that, which trains people to produce dumps containing their
  email address — dumps they then paste into public SUMO threads. Reducing
  ambiguity in this tool is not worth increasing PII exposure across the wider
  support workflow.
- An optional **domain** input is still worth having, but for cross-checking DNS
  against `dns-scripts`, not for selecting an account. The domain is the one thing
  a dump never contains.

## Layout

Shared code lives at the repo root; per-tool directories arrive with the first
tool that has its own webapp.

```
troubleshooting_info.py     shared parser (all tools use this)
parse_troubleshooting.py    CLI: dump or fragment -> JSON
fixtures/                   golden fixtures + .expected.json (Python/JS contract)
tests/                      pytest suite
```

Input must accept **either** a complete dump **or** just the pasted Accounts
lines — support staff routinely ask for, and users routinely send, only the
fragment. A fragment has no App Basics section, so version-dependent checks must
degrade gracefully.

## Licence

MPL-2.0. Every source file carries the standard MPL header. Contributions follow
Mozilla's Community Participation Guidelines.
