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

## Audience, and why everything is written in the second person

**End users are the eventual audience, not just support staff.** The rollout is
staged: staff, volunteers, and power users first, to shake out the bugs, and
then anyone. That is a decision about *sequence*, not about who the tool is for,
so nothing may be written in a way that has to be rewritten later.

In practice this means **address the reader as "you", never "the user"**, in
both front-ends. Three reasons, in increasing order of importance:

1. It is correct for an end user running the tool on their own dump.
2. It is still correct for a volunteer, whose main next action is pasting the
   advice into a reply — where second person is what they want anyway.
3. Third-person text ("if the user deleted that account…") is *wrong* the moment
   an end user reads it, and there is no warning when that day arrives.

The two front-ends disagreed on this for a while: the CLI said "you" and the web
page said "the user". Neither reading is wrong for its original audience, which
is exactly why the drift went unnoticed. Prefer wording that reads correctly to
both, and avoid jargon the tool itself introduced — "dump", "fixture",
"socketType" — in anything a reader sees.

## Commands

Managed with [`uv`](https://docs.astral.sh/uv/), like `dns-scripts`.

```sh
uv sync                                                  # set up
uv run --with pytest pytest -q                            # tests
uv run parse_troubleshooting.py fixtures/thundermail-correct.txt
pbpaste | uv run parse_troubleshooting.py                 # parse a real paste
uv run anonymize_ics.py Calendar.ics -o scrubbed.ics      # scrub a calendar
uv run anonymize_ics.py --check scrubbed.ics              # audit a scrubbed one
uv run caldav_list_calendars.py "$CAL_HOME"               # names the default calendar
uv run package_addon.py                                   # build/ the Firefox add-on
uv run caldav_make_calendar.py "$CAL_HOME" "ticket 7067"  # a calendar to test in
uv run caldav_import_ics.py "$CAL" scrubbed.ics           # dry run; --upload sends it
uv run caldav_delete_events.py "$CAL" --everything        # dry run; --delete empties it
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

## Scrubbing calendars: what `anonymize_ics.py` learned the hard way

Written for [ticket 7067](https://tbpro.zendesk.com/agent/tickets/7067), whose
1,338-event `Calendar.ics` was the only reproduction of the bug and could not be
shared. **Neither that calendar nor any scrubbed copy of it is in this repo** —
the calendar fixtures here are hand-written and synthetic, on purpose. The first
version of the scrubber shipped five defects, each now a fixture and a test. Do
not simplify them away.

**A calendar cannot be scrubbed one physical line at a time.** Long values wrap
onto continuation lines beginning with a space, so the first version's
whole-file regex — which used `$` under `re.MULTILINE` — stopped every match at
the first line break and left the rest of the value in place, producing
`SUMMARY:Anonymized Datas et auteurs.` Unfold first, scrub, then rewrap. The
rewrap counts **octets, not characters**: the limit is 75 octets, and breaking a
multi-byte character in half corrupts the file.

**Blanking a value while keeping its parameters scrubs nothing.** `CN`,
`SENT-BY`, `DIR`, `ALTREP`, `MEMBER`, `DELEGATED-FROM`, `DELEGATED-TO`, `EMAIL`
and any `X-` parameter carry names, addresses and links. Hence an allow-list of
the parameters that describe rather than identify, not a deny-list.

**The obvious property list is too short.** `ATTACH`, `URL`, `CATEGORIES`, `GEO`
and the calendar's own `NAME` each carried a real email address past the first
version. Replacements have to keep their value type — a `VALUE=BINARY`
attachment must stay decodable base64, or the file stops parsing.

**Everyone gets their own stand-in, not a shared one.** Collapsing every address
onto `anon@example.com` turned a three-attendee meeting into one attendee listed
three times, and made the organiser indistinguishable from an attendee. iTIP
keys attendees by address, so that is a change of meaning, not of cosmetics. A
first-seen map gives `person1@example.com`, `person2@example.com`, …, matched
case-insensitively and shared between `ORGANIZER` and `ATTENDEE`.

**An empty address stays empty.** 533 of the 534 organisers in the ticket-7067
calendar were `ORGANIZER:MAILTO:` with nothing after the colon — an Exchange
export quirk — and the first version invented an address for every one of them.
An absent organiser is exactly the sort of oddity a calendar bug turns on.

Identifiers are the one thing replaced with something unpredictable: a scrubbed
calendar gets imported into real profiles, where a numbered identifier could
collide with an entry already there. A repeating event and its changed
occurrence share an identifier, so the replacement is mapped rather than
regenerated per line — otherwise the changed occurrence stops pointing at its
series. The tests normalise identifiers to `UID-1`, `UID-2` before comparing
against the golden files.

**Fidelity is a feature, not politeness.** Everything outside the scrub list
comes through byte-identical, and a test asserts it. A scrubbed calendar is only
worth having if it still reproduces what the original did, and hand-trimming one
usually destroys exactly the thing that triggered the bug.

**`--check` reuses the scrubber rather than reimplementing it.** Clean means
scrubbing again would change nothing, identifiers excluded. One definition, so
the reporter and the rewriter cannot drift apart.

**Git will strip the carriage returns if you let it.** `core.autocrlf=input`
rewrote every committed `.ics` to bare LF, and the golden fixtures — compared
byte for byte against what the scrubber emits — stopped matching the moment they
came back out of the index. `.gitattributes` marks `*.ics` as `-text` so the
line endings survive a round trip. CRLF is the calendar format, not a platform
preference.

**No JavaScript twin, and that is not an oversight.** "The parser exists twice"
applies to the tools behind the web page, which exist so somebody can paste
something and get an answer. This one is a step support staff run over a file
before attaching it to a bug; it never had a page to be half of.

That was true of all five CalDAV tools until the add-on, and it is worth being
exact about what changed. A *page* still cannot do any of this — see below — but
a browser **extension** is not subject to CORS, so `caldav_account.js` is now a
real second implementation of the account listing and the default-calendar rule,
and `fixtures/caldav-home-*.xml` are the contract that holds it to the Python.
Everything else stays CLI-only: nothing sends `PUT` or `DELETE` from a browser.

## Sending a calendar back up: `caldav_import_ics.py`

**Thunderbird's Import stays the right way to do the first round.** It is the
code path the reporter went through, so a bug that lives there only appears that
way. This tool is for the rounds after it, and for the ones where the client is
what you are trying to keep out of the way.

**One request holds one *entry*, which is not one component.** CalDAV has no
"here is the whole file" request, so the file has to be split, and the split is
by `UID`:

- **A series and its `RECURRENCE-ID` overrides are one entry** and go in one
  request, master first. Split apart, the exception becomes an entry of its own —
  the calendar gains a meeting instead of moving one. `ics-recurrence-override`
  is the fixture.
- **`VTIMEZONE` is copied into every request that names it**, matched on the
  `TZID` parameter, quoted or not. A zone named but never defined is reported, not
  invented: `ics-identifying-params` names `America/Toronto` and defines nothing.
- **`METHOD` is dropped**, since RFC 4791 bars it on a stored entry, and exporters
  write it anyway.
- **No `UID` means the entry is left out and counted.** Generating one would
  import something the file does not say.
- Values are re-folded, not copied line for line, so the reader is `_unfold`'s
  logical lines and the writer is `_fold` — 75 **octets**, as in the scrubber.

**Nothing already in the calendar is overwritten**, and that is what makes the
loop usable rather than merely safe. Each entry's address comes from its
identifier, so it is the same every run, and the `PUT` carries `If-None-Match: *`
so the server refuses instead of replacing. An entry already there is reported as
left alone, not as a failure — which is why a run that a rate limit interrupted at
entry 400 is finished by running it again. `--replace` is the deliberate opposite
and asks for the count first.

**The refusal reuses `audit()`.** A calendar that still identifies somebody is not
sent, by the same definition of clean that `anonymize_ics.py --check` reports, and
the check happens *before* the password is asked for. `--unscrubbed` is accepted
only when the host is loopback, a private address, or a `.local` name: somebody
else's calendar may be reproduced against a local Stalwart and never against
production, where a `DELETE` afterwards reaches neither the backups, nor the
logs, nor the other clients on that account. This is the one rule in the tool that
is a policy decision rather than a protocol one, so it is enforced in code rather
than written in the docs.

## The Firefox add-on

**A page cannot do this and an extension can**, and the difference is CORS.
Thundermail's CalDAV answers a preflight with `200` and no
`Access-Control-Allow-*` header at all (checked 2026-09-01), so `fetch()` from
any origin that is not `mail.thundermail.com` never gets to send the request.
An extension with a host permission is not subject to that. Issue #20 is the ask
to Thundermail; the add-on does not wait on it.

**It is all popup, and there is no background script.** Firefox MV3 backs an
extension with an event page and Chrome MV3 with a service worker; a popup is
the same thing in both, so the difference never has to be handled. Firefox is
the target and every feature works there first.

**The sidebar is the answer to the popup closing**, which it does on any loss of
focus, switching tabs included, with no setting to change it. `sidebar_action`
points at the same `popup.html` with `?in=sidebar` on the end, so the page can
tell which of the two it is and hide the offer when it is already there — one
copy of the markup rather than two to keep in step. Nothing is carried across
when you move: the sidebar starts empty, which is why the offer sits above the
form rather than below it. The state survives switching tabs because the page
stays alive, not because anything is stored.

**The host permission is asked for at the moment it is needed**, through
`optional_host_permissions` and `permissions.request()`, because the server is
whatever you typed into the form. An add-on that can read every site is a much
bigger thing to install than one that can read your mail server.

**The JavaScript and the Python must stay in sync, and a security fix in one is
a fix in both.** This is a rule, not an aspiration. `caldav_account.js` and
`caldav_account.py` answer the same questions about an account, and the
`fixtures/caldav-home-*.xml` companions exist to make a disagreement between
them fail a test rather than surface as a wrong answer — or as a hole.

It has already cost something. `_path_of` in Python reduces `//host/path` to
`/path`, because `urlsplit` drops the authority; `pathOf` in JavaScript only
recognised a *schemed* URL, so a server answering with
`<current-user-principal><href>//attacker.example/x</href></...>` had that href
survive, resolve against the calendar home into somebody else's origin, and
receive the next request with the app password on it. Same intent, two
implementations, one of them wrong, and no fixture covering the case.
`fixtures/caldav-home-hostile-principal.xml` is that case now, asserted by both
suites, and the add-on additionally refuses to send a request to any origin
other than the one it asked permission for.

So: when either half changes, change the other, and add the fixture that would
have caught the difference. A twin that has drifted is worse than no twin,
because the tests keep passing.

**The privacy claim is written down where it can be checked.**
`firefox-addon/privacy.html`, and the PDF printed from it, cite a line of code
for every claim and name the commit those line numbers belong to, because a
claim about what code does not do goes stale silently. Two things make it hold
up rather than reassure: the storage argument rests on the manifest having no
`permissions` key at all, so the storage APIs are *unavailable* rather than
merely unused, and the evidence for the absences is a `grep` anybody can re-run,
which does not depend on line numbers. It ends with what it does not claim —
the browser's own password manager, the password going to your mail server,
the persisted permission grant, a visible screen. Update it, and the commit it
names, whenever the add-on's handling of either value changes.

To reprint it: open the HTML in a browser and print to PDF. There is no script,
because rendering needs a browser and this repo has no dependencies.

**`caldav_account.js` reads the XML itself rather than using `DOMParser`.** Two
reasons, and the first is the one that matters: the same code then runs under
`node --test` and in the add-on, so the tests exercise what ships. The second is
that a server's XML never touches a DOM API at all, which is a stronger version
of the rule that it must never be assigned into the live document.

**Nothing is stored.** No `storage.local`, no `storage.sync` — an app password on
Mozilla's sync servers is the opposite of what an app password is for — no
cookies (`credentials: "omit"`), and nothing in a URL. What you type lives in the
popup and goes when the popup closes. The UI says so, and says the other true
thing too: your browser may still offer to save the password, and that is your
browser rather than the add-on.

**`package_addon.py` exists because an extension can only load files from its own
directory.** `caldav_account.js` is shared, so it stays at the repo root and gets
copied into `build/` at packaging time rather than committed twice. Run it again
after editing the shared file; `firefox-addon/` itself you edit in place.

**Verified in Firefox on 2026-09-01**, loaded from `about:debugging`: it asked
for permission to talk to the server named in the form, and then worked. So
`optional_host_permissions` plus `permissions.request()` on the click is the
right shape in Firefox MV3, and asking outright rather than checking
`permissions.contains()` first — which would spend the user gesture — is what
makes the prompt appear at all.

**`strict_min_version` is `140.15.0`**, the newest ESR 140 as of 2026-09-01
(`product-details.mozilla.org/1.0/firefox_versions.json`, `FIREFOX_ESR`). ESR is
what support-facing tooling should be pinned to, and the newest point release
rather than `140.0` is a deliberate choice with a cost: somebody sitting on
140.3 is told the add-on is incompatible rather than being allowed to try it.
Acceptable because ESR takes its point releases automatically; loosen it to
`140.0` the day that turns out to be wrong.

**The calendar home fills itself in from the address you type**, via `homeFor()`
in `caldav_account.js`. That is one observation — `nemo@thundermail.com`'s
calendars are at `https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/`,
read on 2026-09-01 — and not a documented rule, so it fills a field you can then
change and it never guesses for any other provider. A wrong path 404s, and a 404
from the wrong address looks exactly like an account with no calendars in it,
which is a worse place to leave somebody than an empty field. Typing in the
field yourself stops the guessing for good.

## Asking which calendar is the default: `caldav_list_calendars.py`

**The default calendar is the one thing you have to know before testing and the
one thing the server will not tell you.** RFC 4791 has
`schedule-default-calendar-URL` for exactly this, and Thundermail's Stalwart
answers it nowhere — not on the calendar home, not on the principal. Confirmed
2026-08-01 and again 2026-09-01, so treat it as how that server is, not as a
transient.

What is left is the `/default` path segment, which is a guess, and the server
refusing to `DELETE` it, which is certain and destructive. So the rule is:
**a tool that only lists sends `PROPFIND` and nothing else**, and says which of
the two answers you are getting. `default_among()` in `caldav_account.py` is the
single place that decides, and it returns *whether the server said so* alongside
the answer — a caller that cannot tell a fact from an inference will eventually
print one as the other.

The name is never a signal. A calendar can be called anything, "Default"
included, and the display name is the last thing that tells you which calendar
the account schedules into.

**Its output is the one place these tools emit a real address.** Thundermail
generates the default calendar's name from the account, so on
`nemo@thundermail.com` it is `Nemo Thundermail Calendar (nemo@thundermail.com)`.
The no-PII rule is free everywhere else because no verdict needs an address;
here the answer *is* one. Issue #18 is where what to do about that is being
decided — until then, do not treat that output as safe to paste.

`caldav_account.py` exists because of this tool. The connection and the listing
used to live in `caldav_delete_calendars.py`, which was fine while every tool
changed something; a read-only tool would have had to import `DELETE` to ask a
question. `Connection` is the transport, `Account` is the calendars on it, and
the verbs are subclasses: `Deleter`, `Maker`, `Importer`.

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

## The known-bad-config catalogue

`knownIssues` in `settings.json`. It answers the third of the four questions —
*is this a known bad config?* — as opposed to merely "not what we expected".

Entries fire **alongside** the generic server mismatch, not instead of it. The
mismatch says what the settings should be; the catalogue entry says what is
specifically wrong with what they are. That is the difference between a
volunteer copying values across and a volunteer understanding the problem.

The most valuable entries are the ones that fire when *provider detection
cannot*. A guessed hostname (`imap.thundermail.com`) matches no provider, so
without a catalogue entry the account reports "not checked" — the least useful
possible answer for someone who has simply typed the wrong server name.

`rules` and `knownIssues` share one matcher, documented in `$matcherComment`.
It deliberately has **no regular expressions**: two implementations evaluate it,
and Python and JavaScript regex flavours differ in ways that would surface as a
wrong verdict rather than as a failing test. `hostSuffix` and `hostNotOneOf`
cover what is actually needed.

**`observed` records whether anyone has really met the configuration**, as
opposed to it being derived from a specification. Every entry is currently
`false`: they are structural certainties — 465 with STARTTLS cannot connect,
whoever tries it — but nobody has counted how often they occur. Never let output
imply a frequency that has not been measured, and flip the flag as real cases
arrive.

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

Shared code lives at the repo root; per-tool directories arrive with the second
tool, since the first one's page is the site's index.

```
settings.json               expected settings, read by BOTH front-ends
troubleshooting_info.py     shared parser (all tools use this)
verdicts.py                 judge parsed settings against settings.json
parse_troubleshooting.py    CLI: dump or fragment -> JSON
check_settings.py           CLI: dump or fragment -> per-account verdicts
anonymize_ics.py            CLI: calendar -> the same calendar without the people
caldav_account.py           shared by the CalDAV tools: the connection, and the calendars on it
caldav_asking.py            shared by the CalDAV tools: credentials, and confirming
caldav_list_calendars.py    CLI: name the default calendar, and list the rest
caldav_make_calendar.py     CLI: make a calendar to test in, which Thunderbird cannot
caldav_import_ics.py        CLI: calendar file -> one entry per request on a server
caldav_delete_events.py     CLI: take a scrubbed import back off a server
caldav_delete_calendars.py  CLI: delete the test calendars, keeping the default
caldav_account.js           the account listing again, for the add-on
package_addon.py            assemble firefox-addon/ + the shared module into build/
firefox-addon/              the add-on: manifest, popup, and nothing else
firefox-addon/privacy.{html,pdf}  what happens to your address and app password,
                            line by line; the PDF is printed from the HTML
troubleshooting_info.js     the parser again, for the browser
verdicts.js                 the engine again, for the browser
index.html, app.js, style.css   the webapp; app.js only reads the textarea
fixtures/                   golden fixtures, plus two companions each
                            (ics-*.ics have one: .expected.ics;
                            caldav-home-*.xml have one: .expected.json,
                            asserted by BOTH caldav_account.py and .js)
tests/                      pytest suite and node:test suite, same fixtures
package.json                no dependencies; "type": "module" and a test script
```

**Each fixture has two companions, and they are separate on purpose.**
`.expected.json` is the *parsing* contract and `.verdict.json` the *judgement*
contract. They change for unrelated reasons — adding a provider to
`settings.json` rewrites every verdict while leaving parsing untouched — and
both are asserted by both implementations. Run `uv run --with pytest pytest -q`
and `node --test`; either alone proves only half of it.

Input must accept **either** a complete dump **or** just the pasted Accounts
lines — support staff routinely ask for, and users routinely send, only the
fragment. A fragment has no App Basics section, so version-dependent checks must
degrade gracefully.

## Licence

MPL-2.0. Every source file carries the standard MPL header. Contributions follow
Mozilla's Community Participation Guidelines.
