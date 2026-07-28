// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// Turn one account's verdict into something pasteable into a reply.
//
// Produces both an HTML and a plain-text rendering of the same content, so the
// copy can be put on the clipboard as two flavours and each destination takes
// the one it understands -- exactly the mechanism Thunderbird's own
// about:support copy uses, and the reason a dump can reach support in two
// different shapes.
//
// Kept out of app.js so it can be tested without a browser: the escaping below
// is the only thing standing between a pasted dump and someone's mail client,
// and "it looked fine when I clicked the button" is not evidence.
//
// Constraints this inherits from the rest of the tool:
//
//   - No PII. Verdicts never contain account or identity names, and nothing
//     here reintroduces them. A test asserts it.
//   - Second person. This text is pasted into a reply *to the person whose
//     account it is*, which is the strongest case for the voice rule.
//   - Inline styles only. Mail clients strip <style> blocks, and several drop
//     class attributes, so anything that must survive is on the element.

const OUTCOME_LABELS = {
  pass: "Correct",
  warn: "Works, but not recommended",
  fail: "Needs changing",
  unknown: "Not checked",
  notApplicable: "Nothing to check",
};

// Deliberately dark enough to stay legible on the white background almost every
// mail client uses, and never the only signal -- the words carry the verdict.
const OUTCOME_COLOURS = {
  pass: "#1d7a3e",
  warn: "#8a5300",
  fail: "#b3151a",
  unknown: "#4a4a55",
  notApplicable: "#4a4a55",
};

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

/** Escape text for interpolation into HTML.
 *
 * Everything reaching this function came out of a pasted dump, which is
 * arbitrary text from a stranger's machine. Escaping the quote characters too
 * because some of these values land in attribute position in future edits, and
 * a function that is only safe in element position is a trap.
 */
export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function outcomeLabel(outcome) {
  return OUTCOME_LABELS[outcome] ?? outcome;
}

function serverHeading(server) {
  const role = server.ordinal ? `${server.role} ${server.ordinal}` : server.role;
  return `${role}: ${server.label}`;
}

/** Build the HTML and plain-text renderings of one account's verdict. */
export function accountReport(account, options = {}) {
  const source = options.source ?? "";
  const html = [];
  const text = [];

  const title = `Account ${account.position} — ${account.label}`;
  const provider = account.provider ? `${account.provider.displayName}: ` : "";
  const verdict = `${provider}${outcomeLabel(account.outcome)}`;

  html.push(
    `<div style="font-family: system-ui, sans-serif; font-size: 14px; line-height: 1.5;">`,
    `<p style="margin: 0 0 4px;"><strong style="font-family: ${MONO};">${escapeHtml(title)}</strong></p>`,
    `<p style="margin: 0 0 12px; color: ${OUTCOME_COLOURS[account.outcome] ?? "#4a4a55"};">` +
      `<strong>${escapeHtml(verdict)}</strong></p>`,
  );
  text.push(title, verdict, "");

  for (const note of account.notes ?? []) {
    html.push(`<p style="margin: 0 0 12px; color: #5b5b66;">${escapeHtml(note)}</p>`);
    text.push(note, "");
  }

  for (const server of account.servers ?? []) {
    const heading = serverHeading(server);
    html.push(
      `<p style="margin: 12px 0 4px; font-family: ${MONO};">${escapeHtml(heading)}</p>`,
      `<ul style="margin: 0; padding-left: 20px;">`,
    );
    text.push(heading);

    for (const entry of server.checks ?? []) {
      const colour = OUTCOME_COLOURS[entry.outcome] ?? "#4a4a55";
      html.push(
        `<li style="margin-bottom: 8px;">` +
          `<strong style="color: ${colour};">${escapeHtml(outcomeLabel(entry.outcome))}</strong> — ` +
          escapeHtml(entry.message),
      );
      text.push(`  [${outcomeLabel(entry.outcome)}] ${entry.message}`);

      if (entry.remediation) {
        html.push(
          `<br /><span style="display: inline-block; margin-top: 4px;">` +
            `<strong>Fix:</strong> ${escapeHtml(entry.remediation)}</span>`,
        );
        text.push(`      Fix: ${entry.remediation}`);
      }
      html.push(`</li>`);
    }

    html.push(`</ul>`);
    text.push("");
  }

  if (source) {
    html.push(
      `<p style="margin: 16px 0 0; font-size: 12px; color: #5b5b66;">` +
        `Checked with <a href="${escapeHtml(source)}">${escapeHtml(source)}</a>. ` +
        `Nothing was uploaded; no email addresses appear above.</p>`,
    );
    text.push(
      `Checked with ${source}`,
      "Nothing was uploaded; no email addresses appear above.",
    );
  }

  html.push(`</div>`);

  return { html: html.join("\n"), text: text.join("\n").trimEnd() + "\n" };
}
