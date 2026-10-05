"""Validate a Freshdesk API key by making one cheap authenticated call."""
import httpx
from common import AuthError, UpstreamError


def verify_freshdesk_key(domain: str, api_key: str,
                         base_template: str = "https://{domain}.freshdesk.com") -> None:
    # `domain` is already validated as a bare DNS label and the host is fixed
    # to freshdesk.com, so a caller cannot point this at an arbitrary host.
    url = f"{base_template.format(domain=domain)}/api/v2/tickets"
    try:
        r = httpx.get(url, params={"per_page": 1}, auth=(api_key, "X"),
                      timeout=10, follow_redirects=False)
    except httpx.HTTPError as e:
        raise UpstreamError(f"Could not reach Freshdesk: {type(e).__name__}") from e
    if r.status_code in (401, 403):
        raise AuthError("Freshdesk rejected the API key (check key and domain)")
    if r.status_code != 200:
        raise UpstreamError(f"Unexpected Freshdesk response during verification: {r.status_code}")
