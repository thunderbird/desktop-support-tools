// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// The copy-for-email rendering. Tested without a browser because the escaping
// here is the only thing between a pasted dump and someone's mail client, and
// "it looked fine when I clicked the button" is not evidence.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

import { accountReport, escapeHtml } from "../email_report.js";
import { parse } from "../troubleshooting_info.js";
import { check } from "../verdicts.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SETTINGS = JSON.parse(readFileSync(join(ROOT, "settings.json"), "utf8"));

const read = (name) => readFileSync(join(ROOT, "fixtures", name), "utf8");
const firstAccount = (name) => check(parse(read(`${name}.txt`)), SETTINGS).accounts[0];

test("escapes every character that could break out of HTML", () => {
  assert.equal(
    escapeHtml(`<img src=x onerror="alert('x')">&`),
    "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;",
  );
});

test("a hostile hostname cannot inject markup", () => {
  // The parser keeps whatever hostname it is given, and a dump is arbitrary
  // text from a stranger's machine. This is the path from that text into a
  // mail client's HTML renderer.
  const parsed = parse(
    'account1:\n  INCOMING: account1, , (imap) <script>alert(1)</script>:993, 3, 10\n',
  );
  const account = check(parsed, SETTINGS).accounts[0];
  const { html } = accountReport(account);

  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("renders both flavours of the same verdict", () => {
  const account = firstAccount("thundermail-pop3-plain");
  const { html, text } = accountReport(account, { source: "https://example.invalid/" });

  for (const rendering of [html, text]) {
    assert.ok(rendering.includes("use IMAP"));
    assert.ok(rendering.includes("Needs changing"));
    assert.ok(rendering.includes("https://example.invalid/"));
  }
  assert.ok(html.startsWith("<div"));
  assert.ok(!text.includes("<div"));
  assert.ok(text.includes("Fix:"));

  // The apostrophe in "POP isn't supported" is escaped in the HTML flavour and
  // literal in the plain one. Both render as an apostrophe where they land;
  // the point is that the two flavours are not interchangeable strings.
  assert.ok(html.includes("POP isn&#39;t supported"));
  assert.ok(text.includes("POP isn't supported"));
});

test("uses inline styles only, since mail clients strip style blocks", () => {
  const { html } = accountReport(firstAccount("thundermail-correct"));

  assert.ok(!html.includes("<style"));
  assert.ok(!html.includes("class="));
  assert.ok(html.includes("style="));
});

test("carries no PII, even when the dump had names in it", () => {
  const account = firstAccount("thundermail-private-shown");
  const { html, text } = accountReport(account);

  for (const secret of ["tester@example.com", "tester+lists@example.com", "Work, Personal"]) {
    assert.ok(!html.includes(secret), secret);
    assert.ok(!text.includes(secret), secret);
  }
});

test("omits the source line when no source is given", () => {
  const { html, text } = accountReport(firstAccount("thundermail-correct"));

  assert.ok(!html.includes("Checked with"));
  assert.ok(!text.includes("Checked with"));
});
