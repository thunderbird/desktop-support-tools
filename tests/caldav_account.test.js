// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// The browser half of the CalDAV account, against the same fixtures
// tests/test_caldav_cleanup.py runs the Python half against. Either suite alone
// proves half of it; the .expected.json companions are what stop the two from
// drifting.

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  DEFAULT,
  LISTING,
  UNNAMED,
  addressFor,
  basicAuth,
  calendarsIn,
  defaultAmong,
  defaultIn,
  escapeXml,
  homeFor,
  makeBody,
  onThisMachine,
  parseXml,
  pathOf,
  targetProblem,
} from "../caldav_account.js";

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
const HOMES = [
  "caldav-home-guessed-default",
  "caldav-home-advertised-default",
  "caldav-home-no-default",
  "caldav-home-hostile-principal",
];

function read(name) {
  return readFileSync(join(FIXTURES, name), "utf8");
}

for (const name of HOMES) {
  test(`${name} parses to its expected companion`, () => {
    const expected = JSON.parse(read(`${name}.expected.json`));
    const { calendars, advertised, principal } = calendarsIn(read(`${name}.xml`));

    assert.deepEqual(
      calendars.map(({ href, path, name: displayed }) => ({ href, path, name: displayed })),
      expected.calendars,
    );
    assert.equal(advertised, expected.advertised);
    assert.equal(principal, expected.principal);
    assert.deepEqual(defaultAmong(calendars, advertised), expected.default);
  });
}

test("the scheduling inbox and outbox are not calendars", () => {
  const { calendars } = calendarsIn(read("caldav-home-guessed-default.xml"));
  const names = calendars.map((calendar) => calendar.name);
  assert.ok(!names.includes("Inbox") && !names.includes("Outbox"));
  assert.ok(!calendars.some((calendar) => calendar.path.endsWith("/inbox")));
});

test("the calendar home itself is not one of its calendars", () => {
  const { calendars } = calendarsIn(read("caldav-home-guessed-default.xml"));
  assert.ok(!calendars.some((calendar) => calendar.path === "/dav/cal/you@example.com"));
});

test("a calendar the server never named still has something to show", () => {
  const { calendars } = calendarsIn(read("caldav-home-guessed-default.xml"));
  assert.equal(calendars.at(-1).name, UNNAMED);
});

test("the principal is read, for asking it what the home would not say", () => {
  const { principal } = calendarsIn(read("caldav-home-guessed-default.xml"));
  assert.equal(principal, "/dav/princ/you@example.com");
});

test("a host in a reply is dropped, whichever way it was written", () => {
  // The one that got through: //host/path is a host with the scheme left off,
  // and resolving it against the calendar home gives somebody else's origin --
  // which is then where the next request, and the app password on it, would go.
  assert.equal(pathOf("//attacker.example/dav/princ/you/"), "/dav/princ/you");
  assert.equal(pathOf("https://attacker.example/dav/princ/you/"), "/dav/princ/you");
  assert.equal(
    new URL(`${pathOf("//attacker.example/x")}/`, "https://mail.example.com/dav/cal/you/").origin,
    "https://mail.example.com",
  );
});

test("a hostile principal cannot move the next request", () => {
  const { principal } = calendarsIn(read("caldav-home-hostile-principal.xml"));
  assert.equal(principal, "/dav/princ/you");
  assert.ok(!principal.includes("attacker.example"));
});

test("an advertised default beats an address that says default", () => {
  // The whole point of the second fixture: a calendar sitting at /default that
  // is not the default one. Guessing has to lose to being told.
  const { calendars, advertised } = calendarsIn(read("caldav-home-advertised-default.xml"));
  const { path, said } = defaultAmong(calendars, advertised);
  assert.equal(path, "/dav/cal/you@example.com/work");
  assert.equal(said, true);
});

test("the name is never consulted", () => {
  const calendars = [
    { path: "/dav/cal/you/holidays", name: "Default" },
    { path: "/dav/cal/you/default", name: "Something else" },
  ];
  assert.equal(defaultAmong(calendars, null).path, "/dav/cal/you/default");
});

test("the prefix a server picks does not matter", () => {
  const xml = `<x:multistatus xmlns:x="DAV:" xmlns:q="urn:ietf:params:xml:ns:caldav">
    <x:response><x:href>/c/one/</x:href>
      <x:prop><x:resourcetype><x:collection/><q:calendar/></x:resourcetype>
      <x:displayname>One</x:displayname></x:prop></x:response>
  </x:multistatus>`;
  assert.deepEqual(calendarsIn(xml).calendars.map((c) => c.name), ["One"]);
});

test("a namespace that only looks right is not one", () => {
  // DAV:1 is not DAV:, and a parser matching on local names alone would take it.
  const xml = `<multistatus xmlns="DAV:1" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <response><href>/c/one/</href>
      <prop><resourcetype><collection/><C:calendar/></resourcetype></prop></response>
  </multistatus>`;
  assert.deepEqual(calendarsIn(xml).calendars, []);
});

test("entities in a display name come back as characters", () => {
  const { calendars } = calendarsIn(read("caldav-home-advertised-default.xml"));
  assert.equal(calendars[0].name, "Work & everything else");
});

test("a numeric entity is decoded too", () => {
  const xml = `<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:response><D:href>/c/one/</D:href>
      <D:prop><D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
      <D:displayname>R&#233;union budg&#xe9;taire</D:displayname></D:prop></D:response>
  </D:multistatus>`;
  assert.equal(calendarsIn(xml).calendars[0].name, "Réunion budgétaire");
});

test("comments and a doctype are not content", () => {
  const xml = `<?xml version="1.0"?><!-- <D:href>/nope/</D:href> -->
  <D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:response><D:href>/c/one/</D:href>
      <D:prop><D:resourcetype><D:collection/><C:calendar/></D:resourcetype></D:prop></D:response>
  </D:multistatus>`;
  const { calendars } = calendarsIn(xml);
  assert.deepEqual(calendars.map((c) => c.path), ["/c/one"]);
});

test("nothing recognisable parses to nothing rather than throwing", () => {
  // A server behind a captive portal answers with a login page, and a tool that
  // throws a parse error at you is a tool that tells you nothing about why.
  for (const rubbish of ["", "<html><body>Sign in</body></html>", "not xml at all", "<D:multi"]) {
    assert.deepEqual(calendarsIn(rubbish).calendars, []);
  }
});

test("a default the principal names is read from its own reply", () => {
  const xml = `<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:response><D:href>/dav/princ/you/</D:href><D:propstat><D:prop>
      <C:schedule-default-calendar-URL><D:href>/dav/cal/you/work/</D:href></C:schedule-default-calendar-URL>
    </D:prop></D:propstat></D:response></D:multistatus>`;
  assert.equal(defaultIn(xml), "/dav/cal/you/work");
  assert.equal(defaultIn(`<D:multistatus xmlns:D="DAV:"/>`), null);
});

test("a href is a path however the server wrote it", () => {
  assert.equal(pathOf("https://mail.example.com/dav/cal/you/default/"), "/dav/cal/you/default");
  assert.equal(pathOf("/dav/cal/you/default"), "/dav/cal/you/default");
  assert.equal(pathOf(""), "");
});

test("the address comes from the name, as it does in the CLI", () => {
  // The same three assertions as test_the_address_comes_from_the_name in the
  // Python suite. Two implementations, one answer, or a calendar made by the
  // add-on and one made by the CLI end up at different addresses.
  assert.equal(addressFor("ticket 7067"), "ticket-7067");
  assert.equal(addressFor("Réunion budgétaire!"), "r-union-budg-taire");
  assert.equal(addressFor("  "), "calendar");
});

test("a Thundermail address knows where its calendars are", () => {
  assert.equal(
    homeFor("nemo@thundermail.com"),
    "https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/",
  );
  // Read as typed. The domain is matched case-insensitively because domains are,
  // but the part before the @ is the server's business and not ours to change.
  assert.equal(
    homeFor("Nemo.Test@Thundermail.COM"),
    "https://mail.thundermail.com/dav/cal/Nemo.Test%40Thundermail.COM/",
  );
});

test("nothing is guessed for anybody else", () => {
  // A wrong path 404s, and a 404 looks exactly like an account with no calendars.
  for (const user of ["you@example.com", "you@gmail.com", "", "not an address", "@thundermail.com"]) {
    assert.equal(homeFor(user), null, user);
  }
});

test("http is refused unless the server is on this machine", () => {
  assert.equal(targetProblem("https://mail.example.com/dav/cal/you/"), null);
  assert.equal(targetProblem("http://localhost:8080/dav/"), null);
  assert.equal(targetProblem("http://127.0.0.1:8080/dav/"), null);
  assert.equal(targetProblem("http://stalwart.local/dav/"), null);
  assert.match(targetProblem("http://mail.example.com/dav/"), /app password/);
  assert.match(targetProblem("ftp://mail.example.com/dav/"), /not an address/);
  assert.match(targetProblem("mail.example.com"), /https:\/\//);
});

test("what counts as this machine matches the Python", () => {
  for (const host of ["localhost", "127.0.0.1", "::1", "10.0.0.5", "192.168.1.9", "172.20.1.1", "box.local"]) {
    assert.ok(onThisMachine(host), host);
  }
  for (const host of ["mail.example.com", "8.8.8.8", "172.32.0.1", "example.localhost.example.com"]) {
    assert.ok(!onThisMachine(host), host);
  }
});

test("the MKCALENDAR body escapes what a name can contain", () => {
  const body = makeBody('Ticket & "7067" <urgent>');
  assert.match(body, /<D:displayname>Ticket &amp; &quot;7067&quot; &lt;urgent&gt;<\/D:displayname>/);
  assert.equal(escapeXml("a&b"), "a&amp;b");
});

test("credentials are encoded as bytes, not as characters", () => {
  // btoa() throws on anything above U+00FF, and an app password is whatever the
  // provider generated.
  assert.equal(basicAuth("you", "pass"), "Basic eW91OnBhc3M=");
  assert.doesNotThrow(() => basicAuth("you@example.com", "påsswörd–ü"));
});

test("both halves ask the server for exactly the same thing", () => {
  // Not decoration: a property one side forgets is a question one front-end
  // can answer and the other cannot, and nothing else would catch it.
  const python = readFileSync(join(FIXTURES, "..", "caldav_account.py"), "utf8");
  const bodyOf = (name) => python.split(`${name} = """`)[1].split('"""')[0];
  assert.equal(LISTING.trim(), bodyOf("LISTING").trim());
  assert.equal(DEFAULT.trim(), bodyOf("DEFAULT").trim());
});

test("parseXml keeps document order", () => {
  const root = parseXml("<a><b>1</b><c>2</c></a>");
  assert.deepEqual(root.children[0].children.map((child) => child.name), ["b", "c"]);
});
