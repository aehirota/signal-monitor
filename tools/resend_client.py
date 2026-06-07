"""
Resend wrapper for the weekly digest send.

Same email-delivery stack as the blog autopilot — keeping the choice
consistent across projects reduces architectural surface area.

Day-5: implements the single send_digest() call used by digest_compiler.
"""

from __future__ import annotations

import os

import resend


def send_digest(
    *,
    subject: str,
    text: str,
    html: str | None = None,
) -> str:
    """Send the weekly digest email. Returns the Resend message ID.

    Required env: RESEND_API_KEY, DIGEST_FROM_EMAIL, DIGEST_TO_EMAIL.
    Raises RuntimeError if any are missing.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    from_addr = os.environ.get("DIGEST_FROM_EMAIL")
    to_addr = os.environ.get("DIGEST_TO_EMAIL")

    missing = [
        name
        for name, val in [
            ("RESEND_API_KEY", api_key),
            ("DIGEST_FROM_EMAIL", from_addr),
            ("DIGEST_TO_EMAIL", to_addr),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(f"Missing env vars for Resend send: {', '.join(missing)}")

    resend.api_key = api_key

    params: dict = {
        "from": from_addr,
        "to": [to_addr],
        "subject": subject,
        "text": text,
    }
    if html:
        params["html"] = html

    resp = resend.Emails.send(params)
    # resend SDK returns a dict with at least {"id": "..."}; tolerate variants.
    if isinstance(resp, dict):
        return resp.get("id", "unknown")
    return getattr(resp, "id", "unknown")
