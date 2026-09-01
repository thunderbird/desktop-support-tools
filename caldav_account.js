// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// The CalDAV account, again, for a browser: what is on it, and which calendar
// is the default. `caldav_account.py` is the other half of this pair, and the
// two are held together by the fixtures they are both asserted against --
// `fixtures/caldav-home-*.xml` and its `.expected.json` companions. Change
// what one of them decides and the other's tests fail, which is the point.
//
// The XML is read by the small parser at the bottom of this file rather than by
// DOMParser, for two reasons. It runs identically under `node --test` and in the
// add-on, so the tests exercise the code that ships; and a server's XML never
// reaches a DOM API at all, which is a stronger version of the rule that it must
// never be assigned into the live document.

export const DAV = "DAV:";
export const CALDAV = "urn:ietf:params:xml:ns:caldav";

// What a calendar with no name is called, matching UNNAMED in caldav_account.py.
export const UNNAMED = "(unnamed)";

// What is in the account: every child collection, what kind it is, and what it
// is called. Byte for byte the body caldav_account.py sends.
export const LISTING = `<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:resourcetype/>
    <D:displayname/>
    <D:current-user-principal/>
    <C:schedule-default-calendar-URL/>
  </D:prop>
</D:propfind>
`;

// Which calendar the account treats as its default, asked of the principal when
// the home did not say.
export const DEFAULT = `<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:schedule-default-calendar-URL/>
  </D:prop>
</D:propfind>
`;

/** The MKCALENDAR body for a calendar of this name, as caldav_make_calendar.py sends it. */
export function makeBody(name) {
  return `<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set><D:prop><D:displayname>${escapeXml(name)}</D:displayname></D:prop></D:set>
</C:mkcalendar>
`;
}

/** A href as a bare path, however the server chose to write it. */
export function pathOf(href) {
  const raw = (href || "").trim();
  if (!raw) return "";
  let path = raw;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
    try {
      path = new URL(raw).pathname;
    } catch {
      path = raw;
    }
  }
  return path.replace(/\/+$/, "");
}

/**
 * Every calendar in a multistatus reply, and which one the server calls default.
 *
 * The second half is null far more often than the specification suggests: it is
 * what the server *said*, and Stalwart says nothing. Pass both to defaultAmong()
 * rather than reading the null as "there isn't one".
 */
export function calendarsIn(xml) {
  const root = parseXml(xml);
  const calendars = [];
  let advertised = null;
  let principal = null;

  for (const response of findAll(root, DAV, "response")) {
    const href = textOf(find(response, DAV, "href"));
    const kind = find(response, DAV, "resourcetype");
    if (principal === null) {
      principal = textOf(find(find(response, DAV, "current-user-principal"), DAV, "href")) || null;
    }
    if (advertised === null) {
      const said = find(response, CALDAV, "schedule-default-calendar-URL");
      advertised = textOf(find(said, DAV, "href")) || null;
    }
    if (!href || !kind) continue;
    // A calendar, and specifically not the scheduling inbox or outbox, which
    // are collections in the same place and would break the account if they
    // went. Nothing here deletes anything, but a listing that calls the inbox a
    // calendar is a listing that invites somebody else's code to.
    if (!find(kind, CALDAV, "calendar")) continue;
    if (find(kind, CALDAV, "schedule-inbox")) continue;
    if (find(kind, CALDAV, "schedule-outbox")) continue;
    const name = textOf(find(response, DAV, "displayname"));
    calendars.push({ href, path: pathOf(href), name: name || UNNAMED });
  }

  return {
    calendars,
    advertised: advertised ? pathOf(advertised) : null,
    principal: principal ? pathOf(principal) : null,
  };
}

/** The one href a schedule-default-calendar-URL reply carries, or null. */
export function defaultIn(xml) {
  const said = find(parseXml(xml), CALDAV, "schedule-default-calendar-URL");
  const href = textOf(find(said, DAV, "href"));
  return href ? pathOf(href) : null;
}

/**
 * Which calendar is the default, and whether the server actually said so.
 *
 * Thundermail's Stalwart advertises schedule-default-calendar-URL nowhere, so on
 * the server these tools were written for the answer always comes from the
 * address ending in /default. That is a guess, and callers have to be able to
 * tell the two apart: naming the wrong calendar as your default sends somebody
 * to test against the one calendar they cannot delete afterwards.
 *
 * The name is deliberately not consulted. A calendar can be called anything,
 * "Default" included, and the display name is the last thing that tells you
 * which calendar the account schedules into.
 */
export function defaultAmong(calendars, advertised) {
  if (advertised) return { path: advertised, said: true };
  for (const calendar of calendars) {
    if (calendar.path.split("/").pop().toLowerCase() === "default") {
      return { path: calendar.path, said: false };
    }
  }
  return { path: null, said: false };
}

/**
 * The last part of a calendar's address, worked out from its name.
 *
 * Names have spaces, accents and punctuation in them and addresses should not,
 * so this keeps the letters and digits and joins the rest with hyphens --
 * character for character what address_for() does in caldav_make_calendar.py.
 */
export function addressFor(name) {
  const slug = (name || "").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
  return slug || "calendar";
}

// Where Thundermail keeps an account's calendars. Read off a real account on
// 2026-09-01 -- nemo@thundermail.com's calendars are at
// https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/ -- so it is one
// observation rather than a documented rule, which is why it only ever fills a
// field in that you can then change.
const THUNDERMAIL = { domain: "thundermail.com", host: "mail.thundermail.com", dav: "/dav/cal/" };

/**
 * The address of that account's calendars, where it can be worked out.
 *
 * Only Thundermail, and only because it has been looked at. Every other
 * provider puts its calendars somewhere of its own choosing, and guessing at a
 * path that turns out to be wrong wastes more of your time than an empty field
 * does: a 404 from the wrong address looks exactly like an account with no
 * calendars in it.
 */
export function homeFor(user) {
  const address = (user || "").trim();
  const at = address.lastIndexOf("@");
  if (at < 1) return null;
  if (address.slice(at + 1).toLowerCase() !== THUNDERMAIL.domain) return null;
  // The address is a path segment here, so its @ has to be written %40.
  return `https://${THUNDERMAIL.host}${THUNDERMAIL.dav}${encodeURIComponent(address)}/`;
}

/** Whether that host is a server here rather than one out on the internet. */
export function onThisMachine(host) {
  const name = (host || "").replace(/^\[|\]$/g, "").toLowerCase().replace(/\.$/, "");
  if (name === "localhost" || /\.(localhost|local|internal)$/.test(name)) return true;
  if (name === "::1" || name === "0:0:0:0:0:0:0:1") return true;
  const parts = name.split(".");
  if (parts.length === 4 && parts.every((p) => /^\d{1,3}$/.test(p) && Number(p) < 256)) {
    const [a, b] = parts.map(Number);
    return a === 127 || a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
  }
  return false;
}

/**
 * Whether this address may be asked for a calendar list, and why not.
 *
 * Plain HTTP to somebody's mail server puts an app password on the wire, so it
 * is allowed only where the wire is this machine. That is the same line
 * caldav_import_ics.py draws for --unscrubbed, and it is drawn here in code for
 * the same reason: a rule written only in the documentation is a rule that gets
 * read after the request has gone.
 */
export function targetProblem(address) {
  let url;
  try {
    url = new URL(address);
  } catch {
    return "That does not look like a web address. It should start with https:// and end with a /";
  }
  if (url.protocol === "https:") return null;
  if (url.protocol !== "http:") return `${url.protocol} is not an address a calendar lives at.`;
  if (onThisMachine(url.hostname)) return null;
  return (
    "That address is plain http, which would send your app password across the network " +
    "unencrypted. Use https, unless the server is on this machine."
  );
}

/** The Authorization header for a username and an app password, non-ASCII included. */
export function basicAuth(user, password) {
  const bytes = new TextEncoder().encode(`${user}:${password}`);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `Basic ${btoa(binary)}`;
}

export function escapeXml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

// --------------------------------------------------------------------------
// Reading the XML.
//
// Enough of a parser for a multistatus reply and no more: elements, text,
// attributes read only for their xmlns declarations, and the five entities a
// display name can contain. Namespaces are resolved rather than assumed,
// because the prefix is the server's choice -- D:, A:, d:, or none at all --
// and a parser that keys on "D:href" works until the day a server changes it.

const NAME = "[^\\s/>=]+";
const TAG = new RegExp(`<(/?)(${NAME})((?:[^>"']|"[^"]*"|'[^']*')*?)(/?)>`, "g");
const ATTRIBUTE = new RegExp(`(${NAME})\\s*=\\s*("[^"]*"|'[^']*')`, "g");

/** The document as a tree of {ns, name, children, text}. */
export function parseXml(text) {
  const source = String(text)
    .replace(/<\?[\s\S]*?\?>/g, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<!DOCTYPE[^>]*>/gi, "");

  const root = { ns: null, name: "#document", children: [], text: "" };
  const stack = [{ node: root, namespaces: {} }];
  let last = 0;
  TAG.lastIndex = 0;

  for (let tag = TAG.exec(source); tag; tag = TAG.exec(source)) {
    const [whole, closing, qualified, attributes, selfClosing] = tag;
    const between = source.slice(last, tag.index);
    last = tag.index + whole.length;
    if (between.trim()) stack[stack.length - 1].node.text += decode(between);

    if (closing) {
      if (stack.length > 1) stack.pop();
      continue;
    }

    const namespaces = { ...stack[stack.length - 1].namespaces };
    ATTRIBUTE.lastIndex = 0;
    for (let attr = ATTRIBUTE.exec(attributes); attr; attr = ATTRIBUTE.exec(attributes)) {
      const value = decode(attr[2].slice(1, -1));
      if (attr[1] === "xmlns") namespaces[""] = value;
      else if (attr[1].startsWith("xmlns:")) namespaces[attr[1].slice(6)] = value;
    }

    const colon = qualified.indexOf(":");
    const prefix = colon === -1 ? "" : qualified.slice(0, colon);
    const node = {
      ns: namespaces[prefix] || null,
      name: colon === -1 ? qualified : qualified.slice(colon + 1),
      children: [],
      text: "",
    };
    stack[stack.length - 1].node.children.push(node);
    if (!selfClosing) stack.push({ node, namespaces });
  }

  return root;
}

/** Element names are compared case-insensitively: schedule-default-calendar-URL. */
function matches(node, ns, name) {
  return node.ns === ns && node.name.toLowerCase() === name.toLowerCase();
}

/**
 * Every descendant with this name, at any depth, in document order.
 *
 * Depth rather than a fixed path because the shape above a <response> is the
 * server's business: parseXml() hands back the document, the multistatus sits
 * under it, and a server is free to wrap things differently again.
 */
export function findAll(node, ns, name, found = []) {
  if (!node) return found;
  for (const child of node.children) {
    if (matches(child, ns, name)) found.push(child);
    else findAll(child, ns, name, found);
  }
  return found;
}

/** The first descendant with this name, at any depth, or null. */
export function find(node, ns, name) {
  if (!node) return null;
  for (const child of node.children) {
    if (matches(child, ns, name)) return child;
    const deeper = find(child, ns, name);
    if (deeper) return deeper;
  }
  return null;
}

export function textOf(node) {
  return node ? node.text.trim() : "";
}

function decode(text) {
  return text.replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (whole, body) => {
    if (body[0] === "#") {
      const code = body[1] === "x" || body[1] === "X"
        ? parseInt(body.slice(2), 16)
        : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    const named = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" }[body.toLowerCase()];
    return named === undefined ? whole : named;
  });
}
