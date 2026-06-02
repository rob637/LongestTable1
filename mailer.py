"""Email sending — Resend in production, console in dev.

Set RESEND_API_KEY and from_email setting to enable real sending.
Otherwise emails are logged to stdout and persisted to email_log.
"""
import os
import json
import urllib.request
import urllib.error

import store


def _resend_api_key():
    return os.environ.get("RESEND_API_KEY", "").strip()


def send(to: str, subject: str, body: str, *, template: str = "", html: str = ""):
    """Send an email. Returns dict with status."""
    from_email = (store.get_setting("from_email") or "").strip() or "onboarding@resend.dev"
    reply_to = (store.get_setting("reply_to_email") or "").strip()
    api_key = _resend_api_key()

    result = {"to": to, "subject": subject, "status": "queued", "error": None}

    if not api_key:
        # Dev mode: log to stdout
        print("─" * 60)
        print(f"[email:dev] To: {to}")
        print(f"[email:dev] From: {from_email}")
        print(f"[email:dev] Subject: {subject}")
        print(body)
        print("─" * 60, flush=True)
        result["status"] = "logged"
    else:
        payload = {
            "from": from_email,
            "to": [to],
            "subject": subject,
            "text": body,
        }
        if html:
            payload["html"] = html
        if reply_to:
            payload["reply_to"] = reply_to
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
                result["status"] = "sent"
        except urllib.error.HTTPError as e:
            result["status"] = "error"
            result["error"] = f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}"
        except Exception as e:  # noqa: BLE001
            result["status"] = "error"
            result["error"] = str(e)[:300]

    try:
        store.log_email(
            to_email=to,
            subject=subject,
            body=body,
            template=template,
            status=result["status"],
            error=result["error"],
        )
    except Exception:  # noqa: BLE001
        pass

    return result
