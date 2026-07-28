// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// Judge parsed account settings against settings.json.
//
// A port of verdicts.py, held to the same fixtures/*.verdict.json companions.
// See that module for the reasoning; the rules that matter most here are:
//
//   - One verdict per account, never rolled up. A dump routinely lists
//     mailboxes the user abandoned and nothing marks one as dead.
//   - "unknown" is not "pass". Saying a provider we have never verified looks
//     fine is exactly the confidently-worded wrong answer to avoid.
//   - Local Folders is excluded from the global rules as well as from provider
//     matching, or cleartext-over-plain fires on a local mailbox.
//
// Settings are passed in rather than loaded here, so the browser can fetch()
// settings.json and Node can read it from disk without this module caring.

export const PASS = "pass";
export const WARN = "warn";
export const FAIL = "fail";
export const UNKNOWN = "unknown";
export const NOT_APPLICABLE = "notApplicable";

const SEVERITY = new Map([
  [PASS, 0],
  [WARN, 1],
  [FAIL, 2],
]);

// Thunderbird's own pseudo-account protocol for Local Folders.
const LOCAL_PROTOCOL = "none";
const OUTGOING_PROTOCOL = "smtp";

function worst(outcomes) {
  let result = null;
  let rank = -1;
  for (const outcome of outcomes) {
    const candidate = SEVERITY.get(outcome);
    if (candidate !== undefined && candidate > rank) {
      rank = candidate;
      result = outcome;
    }
  }
  return result;
}

/** The dropdown wording for a socketType, or the raw value if it has none.
 *
 * trySTARTTLS has no dropdown entry: it was removed from the IDL and
 * Thunderbird offers no such choice. Falling back to the raw value keeps the
 * output honest -- describing what the account holds without implying it can
 * be selected.
 */
function socketLabel(settings, value) {
  if (value === null || value === undefined) {
    return null;
  }
  const choice = settings.ui.socketTypeChoices[value];
  if (choice === undefined) {
    return value;
  }
  return choice.label === null ? value : choice.label;
}

function isSelectable(choice, direction) {
  return Boolean(choice) && choice.offeredIn.includes(direction);
}

function authLabel(settings, value) {
  if (value === null || value === undefined) {
    return null;
  }
  const choice = settings.ui.authMethodChoices[value];
  return choice === undefined ? value : choice.label;
}

/** Identify a server to a human by protocol, host and port.
 *
 * Never by key, which appears nowhere in Thunderbird's UI, and never by name,
 * which this tool discards.
 */
function hostPort(host, port) {
  const where = host || "(server unknown)";
  return port === null || port === undefined ? where : `${where}:${port}`;
}

function describeServer(protocol, host, port) {
  return `${protocol || "?"} ${hostPort(host, port)}`;
}

function findProvider(settings, ...hosts) {
  for (const host of hosts) {
    if (!host) {
      continue;
    }
    const needle = host.trim().toLowerCase();
    for (const provider of settings.providers) {
      if (provider.match.hosts.some((known) => known.toLowerCase() === needle)) {
        return provider;
      }
    }
  }
  return null;
}

/** Render acceptable (port, socketType) pairs as prose.
 *
 * Alternatives, because several can be correct at once: Thundermail SMTP is
 * valid on both 587/STARTTLS and 465/SSL.
 */
function expectedSummary(settings, servers) {
  const parts = servers.map(
    (server) =>
      `${server.host}:${server.port} with ` +
      `${settings.ui.fieldLabels.socketType} ` +
      `${socketLabel(settings, server.socketType)}`,
  );
  return parts.length === 1 ? parts[0] : parts.join(" — or — ");
}

function preferredServer(servers) {
  return servers.find((server) => server.preferredForRemediation) ?? servers[0];
}

/** Judge (host, port, socketType) as a unit, not field by field. */
function checkServerSettings(settings, protocolSettings, host, port, socketType) {
  const servers = protocolSettings.servers;
  const location = settings.ui.locations[protocolSettings.direction];

  const actual = {
    host,
    port,
    socketType,
    socketTypeLabel: socketLabel(settings, socketType),
  };

  const matched = servers.find(
    (server) =>
      host &&
      host.toLowerCase() === server.host.toLowerCase() &&
      port === server.port &&
      socketType === server.socketType,
  );

  if (matched !== undefined) {
    return {
      check: "server",
      outcome: PASS,
      actual,
      message: "Server, port and connection security are correct.",
      provenance: matched.provenance,
    };
  }

  // A dump omits the port entirely when it is -1, meaning "use the default for
  // this connection security". We cannot tell which port that resolves to, so
  // this is reported rather than judged.
  if (port === null || port === undefined) {
    return {
      check: "server",
      outcome: UNKNOWN,
      actual,
      message:
        "This account has no explicit port, so Thunderbird is using the " +
        "default for its connection security. The dump does not say " +
        "which port that is.",
      expected: expectedSummary(settings, servers),
    };
  }

  const preferred = preferredServer(servers);

  // A stored value Thunderbird's dropdown does not offer -- trySTARTTLS from an
  // old profile -- cannot be described as if the user had chosen it, and cannot
  // be left as it is either.
  const choice = settings.ui.socketTypeChoices[socketType];
  const unofferable =
    socketType !== null && !isSelectable(choice, protocolSettings.direction);

  return {
    check: "server",
    outcome: FAIL,
    actual,
    expected: expectedSummary(settings, servers),
    message:
      `Expected ${expectedSummary(settings, servers)}, ` +
      `but this account has ${hostPort(host, port)} with ` +
      `${settings.ui.fieldLabels.socketType} ${socketLabel(settings, socketType)}.` +
      (unofferable
        ? " Thunderbird no longer offers that connection security " +
          "setting, so it must be changed to one of the three it does."
        : ""),
    remediation:
      `In ${location}, set ` +
      `${settings.ui.fieldLabels.host} to ${preferred.host}, ` +
      `${settings.ui.fieldLabels.port} to ${preferred.port}, and ` +
      `${settings.ui.fieldLabels.socketType} to ` +
      `${socketLabel(settings, preferred.socketType)}.`,
    provenance: preferred.provenance,
  };
}

function checkAuthMethod(settings, protocolSettings, authMethod) {
  const auth = protocolSettings.authMethods;
  const location = settings.ui.locations[protocolSettings.direction];

  const actual = {
    authMethod,
    authMethodLabel: authLabel(settings, authMethod),
  };

  if (authMethod === null || authMethod === undefined) {
    return {
      check: "authMethod",
      outcome: UNKNOWN,
      actual,
      message: "The dump does not show an authentication method for this server.",
    };
  }

  const accepted = auth.accepted[authMethod];
  const entry = accepted === undefined ? auth.otherwise : accepted;

  const result = {
    check: "authMethod",
    outcome: entry.verdict,
    actual,
    message: entry.note,
  };
  if (entry.verdict !== PASS) {
    result.remediation =
      `In ${location}, set ${settings.ui.fieldLabels.authMethod} to ` +
      `${authLabel(settings, auth.recommended)}.`;
  }
  return result;
}

/** Evaluate one `when` object. Absent keys match anything.
 *
 * See $matcherComment in settings.json for the supported keys and for why none
 * of them is a regular expression: this has to behave identically to the
 * Python implementation, and the two regex flavours differ in ways that would
 * surface as a wrong verdict rather than as a failing test.
 */
function matches(when, facts) {
  const host = (facts.host ?? "").toLowerCase();

  for (const key of ["protocol", "direction", "port", "socketType", "authMethod"]) {
    if (key in when && when[key] !== facts[key]) {
      return false;
    }
  }

  if ("hostSuffix" in when && !host.endsWith(when.hostSuffix.toLowerCase())) {
    return false;
  }

  if ("hostNotOneOf" in when) {
    if (when.hostNotOneOf.some((excluded) => excluded.toLowerCase() === host)) {
      return false;
    }
  }

  return true;
}

/** Provider-independent rules, applied even when the provider is unknown. */
function checkRules(settings, facts) {
  return settings.rules
    .filter((rule) => matches(rule.when, facts))
    .map((rule) => ({
      check: "rule",
      rule: rule.id,
      outcome: rule.verdict,
      message: rule.message,
      remediation: rule.remediation,
    }));
}

/** The catalogue: configurations recognisable as specifically broken.
 *
 * Fires alongside the generic mismatch rather than instead of it, and is the
 * only thing that can help when the *hostname* is wrong -- provider detection
 * has nothing to match on by then.
 */
function checkKnownIssues(settings, facts) {
  return settings.knownIssues
    .filter((issue) => matches(issue.when, facts))
    .map((issue) => ({
      check: "knownIssue",
      issue: issue.id,
      outcome: issue.verdict,
      message: issue.message,
      remediation: issue.remediation,
      provenance: issue.provenance,
      // Whether anyone has actually met this in a support case, as opposed to
      // it being derived from a specification.
      observed: issue.observed,
    }));
}

function checkOneServer(settings, provider, protocol, record, role) {
  const host = record.host ?? null;
  const port = record.port ?? null;
  const socketType = record.socketType ?? null;
  const authMethod = record.authMethod ?? null;

  const result = {
    role,
    protocol,
    label: describeServer(protocol, host, port),
    checks: [],
  };

  const facts = {
    protocol,
    direction: role === "outgoing" ? "outgoing" : "incoming",
    host,
    port,
    socketType,
    authMethod,
  };

  if (provider === null) {
    const known = settings.providers.map((p) => p.displayName).join(", ");
    result.checks.push({
      check: "provider",
      outcome: UNKNOWN,
      message:
        `No expected settings are catalogued for ${host || "this server"}. ` +
        `Covered so far: ${known}.`,
    });
    // The catalogue and the rules are provider-independent and run even here.
    // This is where a guessed hostname gets caught: provider detection has
    // just failed *because* the name is wrong, so a catalogue entry is the
    // only thing that can say anything useful.
    result.checks.push(...checkKnownIssues(settings, facts));
    result.checks.push(...checkRules(settings, facts));
    result.outcome = worst(result.checks.map((c) => c.outcome)) ?? UNKNOWN;
    return result;
  }

  const protocolSettings = provider.protocols[protocol];

  if (protocolSettings === undefined) {
    result.checks.push({
      check: "protocol",
      outcome: UNKNOWN,
      message: `${provider.displayName} has no catalogued settings for ${protocol}.`,
    });
  } else if (!protocolSettings.supported) {
    // No correct settings exist to compare against, so per-field mismatches
    // would be nonsense. Say what the user should do instead.
    result.checks.push({
      check: "protocol",
      outcome: protocolSettings.verdict,
      message: protocolSettings.message,
      remediation: protocolSettings.remediation,
      provenance: protocolSettings.provenance,
    });
  } else {
    result.checks.push(
      checkServerSettings(settings, protocolSettings, host, port, socketType),
    );
    result.checks.push(checkAuthMethod(settings, protocolSettings, authMethod));
  }

  // The catalogue explains a mismatch the check above has already reported, so
  // it follows it. Rules come last so the provider-specific finding leads.
  result.checks.push(...checkKnownIssues(settings, facts));
  result.checks.push(...checkRules(settings, facts));

  result.outcome = worst(result.checks.map((c) => c.outcome)) ?? UNKNOWN;
  return result;
}

export function checkAccount(settings, account, position) {
  const incoming = account.incoming ?? null;
  const outgoing = account.outgoing ?? [];

  const result = {
    position,
    key: account.key ?? null,
    provider: null,
    servers: [],
    notes: [],
  };

  const incomingProtocol = incoming ? incoming.protocol : null;
  const incomingHost = incoming ? incoming.host : null;

  if (incoming !== null) {
    result.notes.push(...(incoming.warnings ?? []));
  }

  // Local Folders is not a mail server. Judging it would fire the
  // cleartext-over-plain rule on a mailbox that never touches the network.
  if (incomingProtocol === LOCAL_PROTOCOL && outgoing.length === 0) {
    result.outcome = NOT_APPLICABLE;
    result.label = describeServer(incomingProtocol, incomingHost, null);
    result.notes.push(
      "Local Folders is stored on this computer and has no server settings to check.",
    );
    return result;
  }

  const outgoingHost = outgoing.map((o) => o.host).find(Boolean) ?? null;
  const provider = findProvider(settings, incomingHost, outgoingHost);
  if (provider !== null) {
    result.provider = {
      id: provider.id,
      displayName: provider.displayName,
      verified: provider.verified,
    };
  }

  if (incoming !== null && incomingProtocol !== null) {
    result.servers.push(
      checkOneServer(settings, provider, incomingProtocol, incoming, "incoming"),
    );
  } else if (incoming !== null) {
    // accounts.js emits a placeholder record when reading the incoming server
    // throws. That is a finding about the account, not a parse error.
    result.servers.push({
      role: "incoming",
      protocol: null,
      label: "incoming server unreadable",
      outcome: UNKNOWN,
      checks: [
        {
          check: "account",
          outcome: UNKNOWN,
          message:
            "Thunderbird could not read this account's incoming server, so " +
            "there are no settings to check. That is a fault in the account " +
            "itself rather than in its configuration.",
          remediation:
            "Quit Thunderbird, reopen it, and copy the troubleshooting " +
            "information again. If the account is still listed like this, it " +
            "is damaged and worth removing and adding again.",
        },
      ],
    });
  }

  outgoing.forEach((record, index) => {
    const checked = checkOneServer(
      settings,
      provider,
      OUTGOING_PROTOCOL,
      record,
      "outgoing",
    );
    if (outgoing.length > 1) {
      // An account with several identities has several outgoing servers,
      // routinely the same host and port. The identity name that would tell
      // them apart is private and discarded, so order is the only handle.
      checked.ordinal = index + 1;
    }
    result.servers.push(checked);
  });

  if (outgoing.length > 1) {
    result.notes.push(
      `This account has ${outgoing.length} outgoing servers, listed in the ` +
        `order Thunderbird reports them. They are identified by number ` +
        `because the identity names that would distinguish them are ` +
        `private, and this tool discards them.`,
    );
  }

  result.label = result.servers.length ? result.servers[0].label : "no servers";
  result.outcome =
    worst(result.servers.map((server) => server.outcome)) ??
    (result.servers.length ? UNKNOWN : NOT_APPLICABLE);
  return result;
}

/** Judge every account in a parsed dump.
 *
 * `account` optionally selects one, by key ("account3") or by 1-based position.
 * Which account to check is an input, not an inference.
 */
export function check(parsed, settings, account = null) {
  let results = (parsed.accounts ?? []).map((parsedAccount, index) =>
    checkAccount(settings, parsedAccount, index + 1),
  );

  const warnings = [...(parsed.warnings ?? [])];

  if (account !== null && account !== undefined && account !== "") {
    const selected = results.filter(
      (r) => r.key === account || String(r.position) === String(account),
    );
    if (selected.length === 0) {
      warnings.push(`no account matched '${account}'; showing all accounts`);
    } else {
      results = selected;
    }
  }

  return {
    input: parsed.input ?? {},
    app: parsed.app ?? {},
    accounts: results,
    warnings,
  };
}
