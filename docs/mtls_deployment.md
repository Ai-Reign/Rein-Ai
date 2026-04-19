# mTLS Deployment Guide

Tripwire's admin API supports mTLS (mutual TLS) client authentication via a
**reverse proxy** (nginx, Envoy, Traefik, Caddy). The proxy terminates TLS,
verifies the client certificate against a trusted CA, and forwards the cert's
subject in a trusted header. Tripwire's app code never touches raw
certificates — that keeps the trust boundary in the proxy layer where it
belongs (standard SOC 2 / ISO 27001 practice).

## Architecture

```
   Client                       Reverse Proxy             Tripwire API
   ──────                       (nginx/Envoy)             (FastAPI)
      │                              │                         │
      │  TLS handshake + client cert │                         │
      │─────────────────────────────▶│                         │
      │                              │  Verify cert vs CA      │
      │                              │  Extract subject        │
      │                              │                         │
      │                              │  HTTP + X-Client-Cert-  │
      │                              │  Subject header         │
      │                              │────────────────────────▶│
      │                              │                         │
      │                              │                   Look up subject
      │                              │                   in META_AUTH_CERT_ROLES
      │                              │                   → grant role(s)
```

## Step 1 — Generate a CA and client certs

```bash
# CA (keep ca.key OFFLINE — anyone with it can mint operator creds)
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout ca.key -out ca.crt \
    -subj "/CN=Tripwire Ops CA/O=YourOrg"

# Server cert for Tripwire
openssl req -newkey rsa:2048 -nodes -keyout server.key \
    -out server.csr -subj "/CN=tripwire.internal"
openssl x509 -req -in server.csr -days 365 -sha256 \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt

# Client cert for an ops team member
openssl req -newkey rsa:2048 -nodes -keyout ops-alice.key \
    -out ops-alice.csr -subj "/CN=alice,OU=ops,O=YourOrg"
openssl x509 -req -in ops-alice.csr -days 365 -sha256 \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out ops-alice.crt
```

## Step 2 — Configure nginx

```nginx
server {
    listen 443 ssl;
    server_name tripwire.internal;

    ssl_certificate     /etc/ssl/tripwire/server.crt;
    ssl_certificate_key /etc/ssl/tripwire/server.key;

    ssl_client_certificate /etc/ssl/tripwire/ca.crt;
    ssl_verify_client on;               # REQUIRE client cert
    ssl_verify_depth  2;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location /tripwire/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host                  $host;
        proxy_set_header X-Real-IP             $remote_addr;

        # THIS is what Tripwire uses for auth
        proxy_set_header X-Client-Cert-Subject $ssl_client_s_dn;

        # Strip any forged header from upstream client
        proxy_set_header X-Forwarded-For       $proxy_add_x_forwarded_for;
    }
}
```

**Critical:** The `ssl_verify_client on` line means nginx rejects any request
without a valid client cert before it ever reaches Tripwire. The header
forwarding is safe because clients cannot set `X-Client-Cert-Subject`
themselves — nginx overwrites whatever they send.

## Step 3 — Configure Tripwire role mapping

```bash
export META_AUTH_CERT_ROLES='{
  "CN=alice,OU=ops,O=YourOrg":      ["operator"],
  "CN=readonly-bot,OU=ops,O=YourOrg": ["reader"],
  "CN=admin,OU=ops,O=YourOrg":        ["admin"]
}'
```

The subject string must match nginx's `$ssl_client_s_dn` exactly. Test with:

```bash
# On the nginx box:
curl -s --cert ops-alice.crt --key ops-alice.key --cacert ca.crt \
    https://tripwire.internal/tripwire/whoami
# Expected: {"subject": "cert:CN=alice,OU=ops,O=YourOrg", ...}
```

## Step 4 — Revoke a cert

Add the cert's serial number to a CRL (certificate revocation list) or use
OCSP stapling. Simplest: generate a CRL and reload nginx:

```bash
openssl ca -gencrl -out crl.pem -config openssl.cnf
# Update nginx:
ssl_crl /etc/ssl/tripwire/crl.pem;
```

Also remove the cert's subject from `META_AUTH_CERT_ROLES` as a defense-in-depth.

## Alternative: Envoy / Traefik

Both support the same pattern with different config syntax:

- **Envoy:** enable `require_client_certificate: true` on the listener,
  extract subject via `set_current_client_cert_details` on a forward header.
- **Traefik:** use `passTLSClientCert.info.subject.commonName` middleware.

## Bearer token fallback

Even with mTLS enforced, you can also configure bearer tokens for service
accounts that can't easily present certs (CI jobs, ephemeral scripts):

```bash
export META_AUTH_TOKENS='{"tok_ci_xxx": ["reader"]}'
```

Bearer takes precedence over mTLS if both are present on a single request.
`/tripwire/whoami` reports which was used.

## Audit trail

Every mutating call is recorded in the audit log with the `by` field set to
the identity (subject or token hash) — enabling per-operator accountability.
Combined with the signed audit chain (see `secure_audit.md`), you have a
tamper-evident, attributable record of every operator action, which satisfies
**SOC 2 CC6.1, CC6.2, and CC7.2**.
