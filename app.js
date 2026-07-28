// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// The page. Everything of substance lives in troubleshooting_info.js and
// verdicts.js, which are shared with the tests and mirror the Python CLI; this
// file only reads the textarea and builds DOM nodes.
//
// Nothing is uploaded and nothing is stored. There is no fetch() here beyond
// settings.json, which is served from this same origin.
//
// Every value that came from the pasted text is written with textContent, never
// innerHTML. A dump is arbitrary text from a stranger's machine.

import { parse } from "./troubleshooting_info.js";
import { check } from "./verdicts.js";

const OUTCOME_LABELS = {
  pass: "Correct",
  warn: "Works, but not recommended",
  fail: "Needs changing",
  unknown: "Not checked",
  notApplicable: "Nothing to check",
};

const heading = document.querySelector("#heading");
const scope = document.querySelector("#scope");
const input = document.querySelector("#input");
const results = document.querySelector("#results");
const status = document.querySelector("#status");
const picker = document.querySelector("#account-picker");
const accountSelect = document.querySelector("#account");
const clearButton = document.querySelector("#clear");

let settings = null;
let lastParsed = null;

/** Join a list into prose: "a", "a and b", "a, b and c". */
function series(items) {
  if (items.length < 3) {
    return items.join(" and ");
  }
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Name the tool after whatever settings.json actually covers.
 *
 * Today that is Thundermail alone, and saying so up front is worth more than
 * generality: a volunteer who pastes a Gmail account should learn that from
 * the heading, not from four "Not checked" verdicts. Adding a provider to
 * settings.json rewords the page on its own -- nothing here needs editing,
 * and nothing here can contradict the catalogue.
 */
function applyScope(loaded) {
  const names = loaded.providers.map((provider) => provider.displayName);
  const hosts = loaded.providers.flatMap((provider) => provider.match.hosts);
  if (names.length === 0) {
    return;
  }

  const covered = series(names);
  heading.textContent = `Check ${covered} account settings in Thunderbird`;
  document.title = `Check ${covered} account settings`;

  scope.replaceChildren(
    document.createTextNode(`This checks ${covered} accounts (`),
    element("code", null, series(hosts)),
    document.createTextNode(
      "). Paste the whole thing anyway — every account is listed, and any that " +
        "belong to another provider are marked as not checked rather than " +
        "judged against settings we have not verified.",
    ),
  );
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = text;
  }
  return node;
}

function badge(outcome) {
  const node = element("span", `badge badge-${outcome}`, OUTCOME_LABELS[outcome] ?? outcome);
  return node;
}

function renderCheck(entry) {
  const item = element("li", `check check-${entry.outcome}`);
  item.append(badge(entry.outcome), element("p", "check-message", entry.message));

  if (entry.remediation) {
    const fix = element("p", "check-fix");
    fix.append(element("b", null, "Fix: "), document.createTextNode(entry.remediation));
    item.append(fix);
  }
  if (entry.provenance) {
    const why = element("details", "check-why");
    why.append(element("summary", null, "Why this is the expected value"));
    why.append(element("p", null, entry.provenance));
    item.append(why);
  }
  return item;
}

function renderServer(server) {
  const section = element("section", "server");
  const role = server.ordinal ? `${server.role} ${server.ordinal}` : server.role;
  section.append(element("h3", null, `${role}: ${server.label}`));

  const list = element("ul", "checks");
  for (const entry of server.checks ?? []) {
    list.append(renderCheck(entry));
  }
  section.append(list);
  return section;
}

function renderAccount(account) {
  const article = element("article", `account account-${account.outcome}`);

  const heading = element("h2");
  heading.append(document.createTextNode(`Account ${account.position} — ${account.label}`));
  article.append(heading);

  const meta = element("p", "account-meta");
  meta.append(badge(account.outcome));
  if (account.provider) {
    meta.append(element("span", "provider", account.provider.displayName));
    if (!account.provider.verified) {
      // dns-scripts marks providers whose behaviour was inferred rather than
      // confirmed, and says so in the output. Same habit here.
      meta.append(element("span", "unverified", "unverified provider"));
    }
  }
  article.append(meta);

  for (const note of account.notes) {
    article.append(element("p", "note", note));
  }
  for (const server of account.servers) {
    article.append(renderServer(server));
  }
  return article;
}

function renderEmpty() {
  const help = element("div", "empty");
  help.append(element("p", null, "No mail accounts found in that text."));

  const list = element("ul");
  for (const reason of [
    "If you pasted only part of the dump, include the lines under “Mail and News Accounts”.",
    "If you copied it out of an email or a forum reply, the formatting may have been lost on the way. Paste it straight from Thunderbird's “Copy text to clipboard” instead.",
    "Accounts with no mail server, such as Local Folders, are not listed here.",
  ]) {
    list.append(element("li", null, reason));
  }
  help.append(list);
  return help;
}

function populatePicker(accounts) {
  const previous = accountSelect.value;
  accountSelect.replaceChildren();

  const all = element("option", null, "All accounts");
  all.value = "";
  accountSelect.append(all);

  for (const account of accounts) {
    // Labelled by protocol, host, port and position -- never by key, which
    // appears nowhere in Thunderbird's UI, and never by name, which is
    // discarded before we get here.
    const option = element("option", null, `${account.position} — ${account.label}`);
    option.value = String(account.position);
    accountSelect.append(option);
  }

  accountSelect.value = accounts.some((a) => String(a.position) === previous)
    ? previous
    : "";
  // Several accounts on one host is a disambiguation prompt, not a fault, and
  // reporting all of them is usually more useful than forcing a choice.
  picker.hidden = accounts.length < 2;
}

function render() {
  results.replaceChildren();
  status.replaceChildren();

  const text = input.value;
  if (!text.trim()) {
    picker.hidden = true;
    return;
  }
  if (settings === null) {
    status.textContent = "Loading expected settings…";
    return;
  }

  lastParsed = parse(text);
  populatePicker(check(lastParsed, settings).accounts);

  const result = check(lastParsed, settings, accountSelect.value || null);

  if (result.input.privateDataShown) {
    status.append(
      element(
        "p",
        "reassurance",
        "That text included account names. They were discarded and are not shown below.",
      ),
    );
  }
  for (const warning of result.warnings) {
    status.append(element("p", "warning", warning));
  }

  if (result.accounts.length === 0) {
    results.append(renderEmpty());
    return;
  }

  // Accounts were found, but none belong to a provider we cover. Worth saying
  // once at the top: a column of "Not checked" verdicts reads as a fault in
  // the tool, or worse, as a clean bill of health.
  const covered = series(settings.providers.map((provider) => provider.displayName));
  if (result.accounts.every((account) => account.provider === null)) {
    status.append(
      element(
        "p",
        "warning",
        `None of these accounts are ${covered} accounts, which is all this ` +
          `page can check so far. Their settings may be perfectly correct — ` +
          `this tool simply has nothing to compare them against.`,
      ),
    );
  }

  for (const account of result.accounts) {
    results.append(renderAccount(account));
  }

  if (result.accounts.length > 1) {
    // No overall verdict and no count: a dump can list accounts the user
    // abandoned, and the Smart Mailboxes pseudo-account never appears at all.
    results.append(
      element(
        "p",
        "footnote",
        "Each account is judged on its own. If one of these is a mailbox you no longer use, ignore its verdict.",
      ),
    );
  }
  results.append(
    element(
      "p",
      "footnote",
      "If an account you deleted still appears here, quit Thunderbird, reopen it, and copy the troubleshooting information again.",
    ),
  );
}

input.addEventListener("input", render);
accountSelect.addEventListener("change", render);
clearButton.addEventListener("click", () => {
  input.value = "";
  lastParsed = null;
  render();
  input.focus();
});

fetch("settings.json")
  .then((response) => response.json())
  .then((loaded) => {
    settings = loaded;
    applyScope(loaded);
    render();
  })
  .catch(() => {
    status.textContent =
      "Could not load settings.json, so nothing can be checked. " +
      "If you opened this file directly, serve the folder over HTTP instead.";
  });
