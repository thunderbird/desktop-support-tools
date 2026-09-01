// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// Everything the add-on does happens here, in the popup, and there is no
// background script at all. That is deliberate: Firefox MV3 backs an extension
// with an event page and Chrome MV3 with a service worker, and code that assumes
// one does not run on the other. A popup is the same thing in both, so the
// difference never arises.
//
// The parse, the default-calendar rule and the request bodies come from
// caldav_account.js, which is the repo's shared module and is asserted against
// the same fixtures as caldav_account.py. Nothing here reimplements any of it.

import {
  DEFAULT,
  LISTING,
  addressFor,
  basicAuth,
  calendarsIn,
  defaultAmong,
  defaultIn,
  makeBody,
  pathOf,
  targetProblem,
} from "./caldav_account.js";

// Firefox is the target and `browser` is what it provides. Chrome's `chrome`
// is picked up here so a port is a smaller job, but nothing else in this file
// has been written for Chrome yet.
const api = globalThis.browser ?? globalThis.chrome;

const form = document.querySelector("#account");
const makeForm = document.querySelector("#make");
const results = document.querySelector("#results");
const status = document.querySelector("#status");
const preview = document.querySelector("#preview");

// A popup is closed by the browser the moment it loses focus, and there is no
// setting that changes that -- so the answer is to be somewhere else. The
// sidebar is the same document, and it survives switching tabs, which also
// means it keeps what you have typed. Nothing is stored to achieve that: the
// page simply stays alive, and closing the sidebar still takes it all with it.
//
// The offer is only shown in the popup, and only where there is a sidebar to
// move to. Chrome's equivalent is a different manifest key and a different API,
// and Chrome is not the target yet.
const stay = document.querySelector("#stay");
if (api?.sidebarAction && !new URLSearchParams(location.search).has("in")) {
  stay.hidden = false;
  document.querySelector("#to-sidebar").addEventListener("click", () => {
    // Called on the click itself, like permissions.request(): Firefox opens a
    // sidebar only in answer to a gesture.
    const opening = api.sidebarAction.open();
    Promise.resolve(opening).finally(() => window.close());
  });
}

// What the last listing found, so making a calendar can refuse a name or an
// address the account is already using -- the same check caldav_make_calendar.py
// does, and the reason it lists before it makes.
let known = { home: null, calendars: [], default: { path: null, said: false } };

// Whether this add-on may talk to the server the form names, asked for once per
// click and awaited by every request that click makes.
let permitted = null;

form.addEventListener("submit", (event) => {
  event.preventDefault();
  run(list, askedFor());
});

makeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  run(make, askedFor());
});

/**
 * Ask to talk to whatever server is in the form, before anything else happens.
 *
 * This has to be the *first* thing a click does. Firefox only grants a
 * permission while a user gesture is in hand, and the first `await` spends it --
 * so checking whether we already have it, which is itself asynchronous, would
 * cost us the right to ask. Asking outright is safe instead of wasteful:
 * requesting a permission that has already been granted resolves true without
 * showing anybody anything.
 */
function askedFor() {
  try {
    const origin = `${new URL(document.querySelector("#home").value.trim()).origin}/*`;
    return api.permissions.request({ origins: [origin] });
  } catch {
    // Not an address at all. Let credentials() say so in words, rather than
    // failing here as a permission problem, which it is not.
    return Promise.resolve(true);
  }
}

document.querySelector("#name").addEventListener("input", (event) => {
  const name = event.target.value.trim();
  preview.textContent = name ? `…/${addressFor(name)}/` : "…";
});

/** Run one action, with the buttons off and whatever went wrong reported. */
async function run(action, permission) {
  permitted = permission;
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach((button) => (button.disabled = true));
  try {
    await action();
  } catch (error) {
    say(error.message, "bad");
  } finally {
    buttons.forEach((button) => (button.disabled = false));
  }
}

async function list() {
  const { home, user, password } = credentials();
  say("Asking the server…");

  const listing = await dav("PROPFIND", home, { user, password, body: LISTING, depth: "1" });
  const { calendars, advertised, principal } = calendarsIn(listing);
  if (calendars.length === 0) {
    throw new Error(
      "No calendars under that address. It is probably one calendar rather than all of " +
        "them — take the last part off it and try again.",
    );
  }

  // The home did not say which is the default, so ask the principal, exactly as
  // caldav_account.py does. Thundermail answers neither, and then the address is
  // all there is to go on.
  let said = advertised;
  if (!said && principal) {
    try {
      const reply = await dav("PROPFIND", new URL(principal + "/", home).href, {
        user,
        password,
        body: DEFAULT,
        depth: "0",
      });
      said = defaultIn(reply);
    } catch {
      said = null; // A principal that will not answer is not an error worth showing.
    }
  }

  known = { home, calendars, default: defaultAmong(calendars, said) };
  show();
  say("");
}

async function make() {
  const { home, user, password } = credentials();
  const name = document.querySelector("#name").value.trim();
  if (!name) throw new Error("The calendar needs a name.");
  if (home !== known.home || known.calendars.length === 0) {
    throw new Error("List the calendars again first, so this can see what is already there.");
  }

  // Nothing is overwritten, and the check is the listing rather than the
  // server's refusal: two calendars with one name are indistinguishable in
  // Thunderbird's list, and the server has no opinion about that.
  const segment = addressFor(name);
  for (const calendar of known.calendars) {
    if (calendar.path.split("/").pop().toLowerCase() === segment.toLowerCase()) {
      throw new Error(`${calendar.path}/ is already there, called “${calendar.name}”.`);
    }
    if (calendar.name.toLowerCase() === name.toLowerCase()) {
      throw new Error(
        `This account already has a calendar called “${calendar.name}”. Two calendars with ` +
          "one name are indistinguishable in Thunderbird's list, so pick another.",
      );
    }
  }

  const address = new URL(`${segment}/`, home).href;
  say(`Making ${name}…`);
  await dav("MKCALENDAR", address, { user, password, body: makeBody(name) });

  known.calendars.push({ href: address, path: pathOf(address), name });
  show();
  say(
    `Made “${name}”. Subscribe to it in Thunderbird with New Calendar → On the Network, ` +
      "which lists what the account has.",
    "good",
  );
  document.querySelector("#name").value = "";
  preview.textContent = "…";
}

/** What is in the three fields, checked before anything is sent. */
function credentials() {
  const home = document.querySelector("#home").value.trim();
  const user = document.querySelector("#user").value.trim();
  const password = document.querySelector("#password").value;
  const problem = targetProblem(home);
  if (problem) throw new Error(problem);
  if (!user) throw new Error("Which username should this sign in with?");
  if (!password) throw new Error("This needs your app password to ask the server anything.");
  // A collection's address ends in a slash, and servers differ on whether they
  // forgive a missing one. None mind an extra.
  return { home: home.endsWith("/") ? home : `${home}/`, user, password };
}

/**
 * One request, once the permission asked for by the click has come back.
 *
 * The permission is asked for at the moment it is needed rather than at install,
 * because the server is whatever you typed. An add-on that could read every site
 * is a much bigger thing to install than one that can read your mail server.
 */
async function dav(method, url, { user, password, body, depth }) {
  if (permitted && !(await permitted)) {
    throw new Error(
      `Without permission to talk to ${new URL(url).host}, this cannot ask it anything.`,
    );
  }

  const headers = { Authorization: basicAuth(user, password) };
  if (body !== undefined) headers["Content-Type"] = "application/xml; charset=utf-8";
  if (depth !== undefined) headers.Depth = depth;

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body,
      // No cookies, ever: this signs in with the app password you typed and
      // nothing else, whatever session the browser happens to be holding.
      credentials: "omit",
      cache: "no-store",
    });
  } catch {
    throw new Error(
      `Could not reach ${new URL(url).host}. Check the address, and that you are online.`,
    );
  }

  if (response.status === 401) {
    throw new Error(
      "The server would not accept that username and password. CalDAV here needs an app " +
        "password; an OAuth2 account password will not do. Some servers also want the bare " +
        "username rather than the full address.",
    );
  }
  if (response.status === 404) throw new Error("There is nothing at that address — check the Location field.");
  if (response.status === 405) throw new Error("Something is already at that address.");
  if (response.status === 507) throw new Error("The account is out of space.");
  if (response.status === 403) throw new Error("The server would not allow that.");
  if (!response.ok && response.status !== 207) throw new Error(`The server answered HTTP ${response.status}.`);
  return response.text();
}

/** Draw the calendars, the default marked, and how the default was decided. */
function show() {
  const list = document.querySelector("#calendars");
  list.replaceChildren();

  for (const calendar of known.calendars) {
    const isDefault = known.default.path && calendar.path === known.default.path;
    const item = document.createElement("li");
    if (isDefault) item.classList.add("default");

    const name = document.createElement("span");
    name.className = "name";
    // textContent, never innerHTML: everything here came from a server.
    name.textContent = calendar.name;
    item.append(name);

    if (isDefault) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = known.default.said ? "default" : "probably default";
      item.append(chip);
    }

    const where = document.createElement("code");
    where.textContent = `${calendar.path}/`;
    item.append(where);
    list.append(item);
  }

  document.querySelector("#summary").textContent =
    known.calendars.length === 1 ? "1 calendar" : `${known.calendars.length} calendars`;

  const how = document.querySelector("#how-default");
  if (!known.default.path) {
    how.textContent =
      "None of these is the default as far as this can tell: the server does not say which " +
      "one it is, and no address ends in /default.";
  } else if (known.default.said) {
    how.textContent = "The server says which calendar is its default, and that is the one marked.";
  } else {
    how.textContent =
      "The server did not say which calendar is its default, so that was worked out from the " +
      "addresses: it is the one whose address ends in /default. Treat it as likely, not certain.";
  }

  // The Thundermail default calendar is named after the account, so the answer
  // to "what is my default calendar called?" is your own address. Worth knowing
  // before this ends up in a screenshot.
  const warning = document.querySelector("#name-warning");
  const named = known.calendars.find((calendar) => calendar.name.includes("@"));
  warning.hidden = !named;
  if (named) {
    warning.textContent =
      "One of these names contains an email address, because Thundermail makes the name from " +
      "your account. Worth a look before you paste or screenshot this anywhere.";
  }

  results.hidden = false;
}

function say(message, kind = "") {
  status.textContent = message;
  status.className = kind;
}
