// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// Parse Thunderbird Desktop Troubleshooting Information into account records.
//
// This is a port of troubleshooting_info.py and must stay behaviourally
// identical to it. The two are held together by fixtures/: every fixture has an
// .expected.json companion and BOTH implementations are asserted against it.
// That harness is the only thing stopping these files from drifting apart, so
// when you change one, change the other and let the fixtures prove it.
//
// See the Python module's docstring for why the format is parsed this way. The
// short version: field counts are fixed and only the private fields are
// free-form, so parse positionally from the fixed end and let the free-form
// field absorb any commas it contains.

// socketType and authMethod arrive as either the name or the raw integer,
// depending on the Thunderbird version, and both normalise to the name. TB 153
// emits integers on both macOS and Windows. Numbers are from nsMsgSocketType /
// nsMsgAuthMethod in mailnews/base/public/MailNewsTypes2.idl.
const SOCKET_TYPE_BY_NUMBER = new Map([
  [0, "plain"],
  // trySTARTTLS has been removed from MailNewsTypes2.idl, but an old profile
  // can still carry the stored value, so keep decoding it.
  [1, "trySTARTTLS"],
  [2, "alwaysSTARTTLS"],
  [3, "SSL"],
]);

const AUTH_METHOD_BY_NUMBER = new Map([
  [1, "none"],
  [2, "old"],
  [3, "passwordCleartext"],
  [4, "passwordEncrypted"],
  [5, "GSSAPI"],
  [6, "NTLM"],
  [7, "External"],
  [8, "secure"],
  [9, "anything"],
  [10, "OAuth2"],
]);

export const SOCKET_TYPES = Object.freeze([...SOCKET_TYPE_BY_NUMBER.values()]);
export const AUTH_METHODS = Object.freeze([...AUTH_METHOD_BY_NUMBER.values()]);

// accounts.js builds hostDetails as "(" + type + ") " + hostName + optional
// ":" + port. The port is omitted entirely when it is -1 (meaning "default").
const HOST_DETAILS_RE = /^\(([^)]*)\)\s*([\s\S]*)$/;

// An account block starts with a bare "key:" line, emitted immediately before
// that account's INCOMING line.
const BARE_KEY_RE = /^([^\s:][^:]*):$/;

// Deliberately ASCII-only, matching the Python side exactly. Python's isdigit()
// would accept non-ASCII digits that JavaScript's \d does not, and the two
// implementations must not disagree about what counts as a number.
const INTEGER_RE = /^-?[0-9]+$/;

const INCOMING_PREFIX = "INCOMING:";
const OUTGOING_PREFIX = "OUTGOING:";

// App Basics rows serialise as "Label: value". These labels are *localised*,
// unlike the Accounts vocabularies above, so matching them is best-effort and
// English-only by design.
const APP_LABELS = new Map([
  ["Name", "name"],
  ["Version", "version"],
  ["Build ID", "buildId"],
  ["OS", "os"],
  ["User Agent", "userAgent"],
]);

/** Split an INCOMING/OUTGOING payload into trimmed fields.
 *
 * Splits on "," rather than ", " because export.js strips trailing whitespace
 * from every line, so a line whose last field is empty ends in "," and
 * splitting on ", " would yield one field too few and shift every field along.
 */
function splitFields(payload) {
  return payload.split(",").map((field) => field.trim());
}

/** Split "host:port". A missing port means "use the default". */
function splitHostPort(hostPort) {
  const trimmed = hostPort.trim();
  if (!trimmed) {
    return [null, null];
  }
  const index = trimmed.lastIndexOf(":");
  if (index !== -1) {
    const tail = trimmed.slice(index + 1);
    if (/^[0-9]+$/.test(tail)) {
      const host = trimmed.slice(0, index).trim();
      return [host || null, Number.parseInt(tail, 10)];
    }
  }
  return [trimmed, null];
}

/** Normalise a socketType/authMethod field to its name.
 *
 * Accepts either the name ("SSL") or the raw integer ("3"), since which one
 * Thunderbird writes depends on the version.
 */
function normaliseEnum(value, field, byNumber) {
  if (!value) {
    return [null, []];
  }
  for (const name of byNumber.values()) {
    if (value === name) {
      return [value, []];
    }
  }
  if (INTEGER_RE.test(value)) {
    const name = byNumber.get(Number.parseInt(value, 10));
    if (name !== undefined) {
      return [name, []];
    }
    return [value, [`unrecognised ${field} value: '${value}'`]];
  }
  return [value, [`unrecognised ${field}: '${value}'`]];
}

/** Parse an INCOMING field list: key, name, hostDetails, socketType, authMethod. */
function parseIncoming(fields) {
  const record = {
    protocol: null,
    host: null,
    port: null,
    socketType: null,
    authMethod: null,
    warnings: [],
  };

  if (fields.length < 5) {
    record.warnings.push("incoming line has too few fields to parse");
    return record;
  }

  const hostDetails = fields[fields.length - 3].trim();
  const [socketType, socketWarnings] = normaliseEnum(
    fields[fields.length - 2].trim(),
    "socketType",
    SOCKET_TYPE_BY_NUMBER,
  );
  const [authMethod, authWarnings] = normaliseEnum(
    fields[fields.length - 1].trim(),
    "authMethod",
    AUTH_METHOD_BY_NUMBER,
  );
  record.socketType = socketType;
  record.authMethod = authMethod;

  const match = HOST_DETAILS_RE.exec(hostDetails);
  if (match) {
    record.protocol = match[1].trim() || null;
    const [host, port] = splitHostPort(match[2]);
    record.host = host;
    record.port = port;
  } else if (hostDetails) {
    record.warnings.push(`unrecognised server details: '${hostDetails}'`);
  } else {
    // accounts.js emits a placeholder record with an empty hostDetails when
    // reading the incoming server throws. A support person seeing this has an
    // account Thunderbird itself cannot read, which is a finding in its own
    // right rather than a parse failure.
    record.warnings.push("Thunderbird could not read this account's incoming server");
  }

  record.warnings.push(...socketWarnings, ...authWarnings);
  return record;
}

/** Parse an OUTGOING field list: identityName, name, socketType, authMethod, isDefault. */
function parseOutgoing(fields) {
  const record = {
    host: null,
    port: null,
    socketType: null,
    authMethod: null,
    isDefault: null,
    warnings: [],
  };

  if (fields.length < 5) {
    record.warnings.push("outgoing line has too few fields to parse");
    return record;
  }

  const [host, port] = splitHostPort(fields[fields.length - 4]);
  record.host = host;
  record.port = port;

  const [socketType, socketWarnings] = normaliseEnum(
    fields[fields.length - 3].trim(),
    "socketType",
    SOCKET_TYPE_BY_NUMBER,
  );
  const [authMethod, authWarnings] = normaliseEnum(
    fields[fields.length - 2].trim(),
    "authMethod",
    AUTH_METHOD_BY_NUMBER,
  );
  record.socketType = socketType;
  record.authMethod = authMethod;

  const isDefault = fields[fields.length - 1].trim();
  if (isDefault === "true" || isDefault === "false") {
    record.isDefault = isDefault === "true";
  } else if (isDefault) {
    record.warnings.push(`unrecognised isDefault: '${isDefault}'`);
  }

  record.warnings.push(...socketWarnings, ...authWarnings);
  return record;
}

/** Was a private field non-empty, i.e. did the user paste with it shown? */
function privateDataPresent(fields, index) {
  return fields.length > index ? Boolean(fields[index].trim()) : false;
}

/** Parse troubleshooting text into a structured, PII-free result.
 *
 * Accepts either a complete Troubleshooting Information dump or just the
 * Accounts lines on their own.
 */
export function parse(text) {
  // Windows builds copy with CRLF line endings: createTextForElement in
  // export.js runs text.replace(/\n/g, "\r\n") over the whole document behind a
  // Windows-only check.
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");

  const accounts = [];
  const app = {};
  const warnings = [];
  let privateDataShown = false;
  let sawNonAccountContent = false;

  // The account key line is emitted immediately before that account's INCOMING
  // line, with nothing between them -- not even a blank line. So the key
  // candidate is cleared by *any* intervening line, which stops an unrelated
  // "Some Section:" heading elsewhere in the dump from being mistaken for one.
  let keyCandidate = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (!line) {
      keyCandidate = null;
      continue;
    }

    if (line.startsWith(INCOMING_PREFIX)) {
      const fields = splitFields(line.slice(INCOMING_PREFIX.length));
      if (privateDataPresent(fields, 1)) {
        privateDataShown = true;
      }
      accounts.push({
        key: keyCandidate,
        incoming: parseIncoming(fields),
        outgoing: [],
      });
      keyCandidate = null;
      continue;
    }

    if (line.startsWith(OUTGOING_PREFIX)) {
      const fields = splitFields(line.slice(OUTGOING_PREFIX.length));
      if (privateDataPresent(fields, 0)) {
        privateDataShown = true;
      }
      if (accounts.length === 0) {
        // A fragment that begins mid-account. Keep the data rather than
        // discarding it; the caller can still judge the SMTP settings.
        accounts.push({ key: null, incoming: null, outgoing: [] });
      }
      accounts[accounts.length - 1].outgoing.push(parseOutgoing(fields));
      keyCandidate = null;
      continue;
    }

    const keyMatch = BARE_KEY_RE.exec(line);
    if (keyMatch) {
      keyCandidate = keyMatch[1];
      continue;
    }

    keyCandidate = null;

    // Not an account line. Try App Basics, and note that this input has content
    // beyond the Accounts section.
    const separator = line.indexOf(":");
    if (separator !== -1) {
      const label = line.slice(0, separator).trim();
      const value = line.slice(separator + 1).trim();
      const field = APP_LABELS.get(label);
      // First occurrence wins: App Basics is the first section in the dump, and
      // later sections (Calendars, Chat) reuse labels like "Name" for unrelated
      // things.
      if (field !== undefined && value && !(field in app)) {
        app[field] = value;
      }
    }

    sawNonAccountContent = true;
  }

  const kind = sawNonAccountContent ? "full" : "fragment";

  if (accounts.length === 0) {
    warnings.push("no mail account information found in this input");
  } else if (kind === "full" && !("version" in app)) {
    // App Basics labels are localised, so a non-English dump parses fine for
    // accounts but yields no version. Anything version-dependent must treat
    // this as unknown rather than assume a value.
    warnings.push(
      "could not determine Thunderbird version " +
        "(App Basics labels are localised; only English is recognised)",
    );
  }
  if (privateDataShown) {
    warnings.push(
      "input contained private data (account or identity names); " +
        "it was discarded and is not included in this result",
    );
  }

  return {
    input: { kind, privateDataShown },
    app,
    accounts,
    warnings,
  };
}
