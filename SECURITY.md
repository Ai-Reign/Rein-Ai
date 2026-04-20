# Security Policy

Rein is a security/governance library. Vulnerabilities in Rein can put every downstream user at risk.

## Reporting a vulnerability

**Do not open a public GitHub issue.**

Email **security@reinai.io** with:

- A description of the vulnerability
- Steps to reproduce
- The Rein version affected
- Any proof-of-concept code (optional but helpful)
- Your preferred disclosure timeline

You should receive an acknowledgment within **72 hours**. We aim to publish a fix within **30 days** of confirmed reports for high-severity issues.

## Scope

**In scope:**

- Authentication or authorization bypass in `/rein/*` admin endpoints
- Audit-log tampering or integrity bypass
- Gate-decision bypass (causing `gate()` to return `allowed=True` when it should not)
- Rate-limiter or circuit-breaker bypass
- Information disclosure (state file, audit log, or config)
- Dependency vulnerabilities that materially affect Rein's security guarantees

**Out of scope:**

- Denial of service against your own Rein instance (you control your rate limits)
- Issues requiring privileged access to the machine running Rein
- Social engineering

## Coordinated disclosure

We follow a 90-day coordinated disclosure window by default. If you need a different timeline, mention it in your initial email.

## Recognition

We will credit reporters in the [CHANGELOG](CHANGELOG.md) release notes unless you prefer anonymity.
