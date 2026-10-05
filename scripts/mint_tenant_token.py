#!/usr/bin/env python3
"""Mint a bearer token for a tenant.

    python scripts/mint_tenant_token.py acme

Put the TOKEN into Agent Studio's connector auth (shown once; we never store it).
Append the TENANT_TOKENS entry to the mcp-server environment.
"""
import hashlib, re, secrets, sys

if len(sys.argv) != 2 or not re.match(r"^[A-Za-z0-9_-]{1,64}$", sys.argv[1]):
    sys.exit("usage: mint_tenant_token.py <tenant_id>   (letters, digits, - _ ; max 64)")
tenant = sys.argv[1]
token = "fdc_" + secrets.token_urlsafe(32)
print(f"TOKEN (give to Agent Studio): {token}")
print(f"TENANT_TOKENS entry         : {hashlib.sha256(token.encode()).hexdigest()}:{tenant}")
