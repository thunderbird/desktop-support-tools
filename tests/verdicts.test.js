// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// The JavaScript half of the judgement contract, mirroring
// tests/test_verdicts.py against the same .verdict.json companions.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

import { parse } from "../troubleshooting_info.js";
import { check, FAIL, NOT_APPLICABLE, PASS, UNKNOWN, WARN } from "../verdicts.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURES = join(ROOT, "fixtures");

const SETTINGS = JSON.parse(readFileSync(join(ROOT, "settings.json"), "utf8"));

const fixtureNames = readdirSync(FIXTURES)
  .filter((name) => name.endsWith(".txt"))
  .map((name) => name.slice(0, -".txt".length))
  .sort();

const read = (name) => readFileSync(join(FIXTURES, name), "utf8");
const verdictFor = (name) => check(parse(read(`${name}.txt`)), SETTINGS);

for (const name of fixtureNames) {
  test(`${name} produces its expected verdict`, () => {
    const expected = JSON.parse(read(`${name}.verdict.json`));
    assert.deepEqual(verdictFor(name), expected);
  });
}

test("a correct Thundermail account passes", () => {
  const result = verdictFor("thundermail-correct");
  assert.deepEqual(
    result.accounts.map((account) => account.outcome),
    [PASS],
  );
});

test("autoconfigured SMTP on 587 is not a failure", () => {
  // The false failure settings.json exists to prevent: Thundermail publishes
  // _submission._tcp -> 587 while the vendor UI documents 465.
  const result = verdictFor("tb153-macos-names-hidden");
  const ports = new Set(
    result.accounts
      .filter((account) => account.provider)
      .flatMap((account) => account.servers)
      .filter((server) => server.role === "outgoing")
      .map((server) => server.checks[0].actual.port),
  );

  assert.ok(ports.has(587) && ports.has(465));
  const thundermail = result.accounts.filter(
    (account) => account.provider?.id === "thundermail",
  );
  assert.ok(thundermail.length > 0);
  assert.ok(thundermail.every((account) => account.outcome === PASS));
});

test("POP reports unsupported rather than wrong settings", () => {
  const incoming = verdictFor("thundermail-pop3-plain").accounts[0].servers[0];
  const protocolChecks = incoming.checks.filter((c) => c.check === "protocol");

  assert.equal(protocolChecks.length, 1);
  assert.equal(protocolChecks[0].outcome, FAIL);
  assert.ok(protocolChecks[0].message.includes("IMAP"));
  assert.equal(incoming.checks.filter((c) => c.check === "server").length, 0);
});

test("cleartext over a plain socket fails even for POP", () => {
  const incoming = verdictFor("thundermail-pop3-plain").accounts[0].servers[0];
  const rules = incoming.checks.filter((c) => c.check === "rule");

  assert.deepEqual(
    rules.map((rule) => rule.rule),
    ["cleartext-over-plain-socket"],
  );
  assert.equal(rules[0].outcome, FAIL);
});

test("Local Folders is not judged", () => {
  // It is (plain, passwordCleartext) and touches no network, so running the
  // cleartext rule on it would report a defect on a local mailbox.
  const local = verdictFor("tb153-macos-names-hidden").accounts.filter(
    (account) => account.outcome === NOT_APPLICABLE,
  );

  assert.equal(local.length, 1);
  assert.deepEqual(local[0].servers, []);
  assert.ok(local[0].notes.some((note) => note.includes("this computer")));
});

test("an uncatalogued provider is not reported as correct", () => {
  const gmail = verdictFor("tb153-windows-gmail").accounts[0];

  assert.equal(gmail.provider, null);
  assert.equal(gmail.outcome, UNKNOWN);
  assert.ok(
    gmail.servers.every((server) =>
      server.checks.every((entry) => entry.outcome === UNKNOWN),
    ),
  );
});

test("an unreadable account is reported, not skipped", () => {
  const broken = verdictFor("account-read-error").accounts[1];

  assert.equal(broken.outcome, UNKNOWN);
  assert.ok(broken.servers[0].checks[0].remediation);
});

test("an app password warns rather than fails", () => {
  const account = verdictFor("thundermail-private-shown").accounts[0];

  assert.equal(account.outcome, WARN);
  assert.equal(account.servers[2].outcome, WARN);
});

test("several outgoing servers are distinguishable", () => {
  const outgoing = verdictFor("thundermail-private-shown").accounts[0].servers.filter(
    (server) => server.role === "outgoing",
  );

  assert.equal(outgoing.length, 2);
  assert.equal(outgoing[0].label, outgoing[1].label);
  assert.deepEqual(
    outgoing.map((server) => server.ordinal),
    [1, 2],
  );
});

test("verdicts are per account and never rolled up", () => {
  const result = verdictFor("account-read-error");

  assert.ok(!("outcome" in result));
  assert.deepEqual(
    result.accounts.map((account) => account.outcome),
    [PASS, UNKNOWN],
  );
});

test("account selection works by key and by position", () => {
  const parsed = parse(read("tb153-macos-names-hidden.txt"));
  const byKey = check(parsed, SETTINGS, "account6");
  const byPosition = check(parsed, SETTINGS, "3");

  assert.equal(byKey.accounts.length, 1);
  assert.deepEqual(byKey, byPosition);
});

test("an unmatched selection falls back to showing everything", () => {
  // Silently reporting nothing would look like a clean bill of health.
  const parsed = parse(read("tb153-macos-names-hidden.txt"));
  const result = check(parsed, SETTINGS, "account99");

  assert.equal(result.accounts.length, 4);
  assert.ok(result.warnings.some((warning) => warning.includes("account99")));
});

test("the verdict layer does not reintroduce discarded PII", () => {
  const rendered = JSON.stringify(verdictFor("thundermail-private-shown"));

  for (const secret of ["tester@example.com", "tester+lists@example.com", "Work, Personal"]) {
    assert.ok(!rendered.includes(secret), secret);
  }
});

test("a known issue explains a mismatch rather than replacing it", () => {
  // The generic check says what is expected; the catalogue says what is wrong.
  // 465 with STARTTLS cannot connect at all, which "expected 587/STARTTLS or
  // 465/SSL" does not convey on its own.
  const outgoing = verdictFor("thundermail-smtp-465-starttls").accounts[0].servers[1];
  const kinds = outgoing.checks.map((entry) => entry.check);

  assert.ok(kinds.includes("server"));
  assert.ok(kinds.includes("knownIssue"));

  const issue = outgoing.checks.find((entry) => entry.check === "knownIssue");
  assert.equal(issue.issue, "implicit-tls-port-with-starttls");
  assert.equal(issue.outcome, FAIL);
  assert.equal(issue.observed, false);
});

test("the catalogue catches what provider detection cannot", () => {
  // A guessed hostname defeats provider matching, which is exactly when it
  // matters: without this the account reports "not checked".
  const account = verdictFor("thundermail-guessed-hostnames").accounts[0];

  assert.equal(account.provider, null);
  assert.equal(account.outcome, FAIL);

  for (const server of account.servers) {
    const issues = server.checks.filter((entry) => entry.check === "knownIssue");
    assert.deepEqual(
      issues.map((issue) => issue.issue),
      ["guessed-thundermail-hostname"],
    );
    assert.ok(issues[0].remediation.includes("mail.thundermail.com"));
  }
});

test("correct configurations trigger no catalogue entries", () => {
  // A catalogue that fires on working accounts is worse than no catalogue.
  for (const name of [
    "thundermail-correct",
    "tb153-macos-names-hidden",
    "tb153-windows-gmail",
  ]) {
    for (const account of verdictFor(name).accounts) {
      for (const server of account.servers) {
        assert.equal(
          server.checks.filter((entry) => entry.check === "knownIssue").length,
          0,
          name,
        );
      }
    }
  }
});
