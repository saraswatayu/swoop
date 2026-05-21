# Security Policy

Swoop scrapes Google Flights via undocumented RPC endpoints. That means the library handles deeply nested untrusted response data, optional proxy credentials, and TLS fingerprint impersonation. Take the threat model seriously if you embed it in a product.

## Reporting a vulnerability

Preferred: open a private advisory at https://github.com/saraswatayu/swoop/security/advisories/new.

Backup: email saraswatayu@gmail.com.

Please include:
- swoop version
- Python version
- A minimal reproducer
- Impact (what an attacker can do, on whose machine)

Expect an acknowledgment within 72 hours.

## Supported versions

Swoop is pre-1.0. Only the latest minor release receives security fixes. If you're pinned to an older version, upgrade before reporting.

## Scope

In scope:
- Deserialization issues in the response decoder (`decoder.py`, `_booking.py`)
- Injection via flight number, IATA, date, or selector parsing
- SSRF or credential leakage via proxy configuration
- Secrets exposure in logs or error messages

Out of scope:
- Bugs in Google Flights itself
- primp internals (report upstream)
- The proxy server you supply
- Breakage when Google reshapes its RPC response (that's a compatibility bug, not a security bug)

## Known security considerations

- **Untrusted input boundary.** The Python API and CLI accept strings: IATA codes, dates, flight numbers, selectors. The library validates IATA, date, and cabin at the boundary. Selectors are opaque base64 produced by Google and round-tripped by swoop. Treat them as untrusted in your app's threat model. Don't render them as HTML, don't log them as identifiers, don't trust their structure.

- **Proxy configuration.** If you pass a proxy URL with inline credentials (`http://user:pass@host:port`) via `TransportConfig.proxy` or `--proxy`, those credentials can appear in stderr when `--verbose` is on, and in any exception traceback that includes the request URL. Use proxy URLs without inline credentials when sharing logs or filing issues. Prefer environment-scoped credentials managed outside swoop.

- **TLS impersonation is not anonymity.** Swoop sends TLS fingerprints and headers that mimic Chrome (or Firefox/Safari/Edge) via primp. This exists so Google's frontend accepts the request. It is not a privacy feature. Your IP, proxy choice, and request patterns still identify you. Do not rely on impersonation for anonymity or to evade rate limiting at scale.
