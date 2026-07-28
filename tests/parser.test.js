// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// The JavaScript half of the fixture contract.
//
// These assertions deliberately mirror tests/test_troubleshooting_info.py
// against the same files. Two parsers exist because the CLI is Python and the
// web page is JavaScript; fixtures/ is the only thing keeping them equivalent,
// so a fixture asserted on one side only is a fixture that proves nothing.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

import { parse } from "../troubleshooting_info.js";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");

const fixtureNames = readdirSync(FIXTURES)
  .filter((name) => name.endsWith(".txt"))
  .map((name) => name.slice(0, -".txt".length))
  .sort();

const read = (name) => readFileSync(join(FIXTURES, name), "utf8");
const readJson = (name) => JSON.parse(read(name));

// Values that would identify a person, as they appear in the fixtures. None of
// these may survive parsing. "Local Folders" is deliberately absent: it is a
// fixed Thunderbird string in the public hostDetails field, and legitimately
// survives.
const PII_STRINGS = [
  "tester@example.com",
  "tester+lists@example.com",
  "Work, Personal",
];

test("every fixture has a .expected.json companion", () => {
  assert.ok(fixtureNames.length > 0);
  for (const name of fixtureNames) {
    assert.doesNotThrow(() => readJson(`${name}.expected.json`), name);
  }
});

for (const name of fixtureNames) {
  test(`${name} parses to its expected JSON`, () => {
    assert.deepEqual(parse(read(`${name}.txt`)), readJson(`${name}.expected.json`));
  });

  test(`${name} parses identically with CRLF line endings`, () => {
    const text = read(`${name}.txt`);
    assert.deepEqual(parse(text.replaceAll("\n", "\r\n")), parse(text));
  });
}

test("private fields never reach the output, even when pasted", () => {
  const result = parse(read("thundermail-private-shown.txt"));
  const rendered = JSON.stringify(result);

  assert.equal(result.input.privateDataShown, true);
  for (const secret of PII_STRINGS) {
    assert.ok(!rendered.includes(secret), secret);
  }
});

test("hiding account names cannot change the parsed settings", () => {
  // The same profile, copied twice with only the "Include account names"
  // checkbox changed. This is what makes the no-PII rule free rather than a
  // trade-off: nothing needed to judge a configuration is private.
  const shown = parse(read("tb153-macos-names-included.txt"));
  const hidden = parse(read("tb153-macos-names-hidden.txt"));

  assert.notEqual(shown.input.privateDataShown, hidden.input.privateDataShown);
  assert.deepEqual(shown.accounts, hidden.accounts);
});

test("a malformed enum is reported rather than throwing", () => {
  // "--3" reaches the integer branch on a naive check and blows up. Both
  // implementations use the same strict ASCII rule so they agree here.
  const result = parse("INCOMING: a, b, (imap) h:993, --3, 10");
  const incoming = result.accounts[0].incoming;

  assert.equal(incoming.socketType, "--3");
  assert.deepEqual(incoming.warnings, ["unrecognised socketType: '--3'"]);
});
