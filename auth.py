"""Magic-link auth + admin authorization.

Magic links are short-lived signed tokens. We sign them with the
secret_key stored in settings so the secret survives restarts.
"""
import functools
import os

from flask import abort, request, redirect, url_for, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import store


def _serializer():
    secret = (store.get_setting("secret_key") or "").strip()
    if not secret:
        # Defensive fallback — init_db generates one, this shouldn't happen
        secret = os.environ.get("FLASK_SECRET", "longest-table-dev-secret")
    return URLSafeTimedSerializer(secret, salt="tltv-magic")


def make_token(purpose: str, subject: str) -> str:
    return _serializer().dumps({"p": purpose, "s": subject})


def read_token(token: str, purpose: str, max_age_seconds: int = 60 * 60 * 24 * 30):
    """Return subject (str) if token valid for purpose, else None."""
    try:
        data = _serializer().loads(token, max_age=max_age_seconds)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(data, dict) or data.get("p") != purpose:
        return None
    return data.get("s")


# ── Admin auth (lightweight; replace with Google SSO later) ─────────────────

def _admin_emails():
    raw = (store.get_setting("admin_emails") or "").strip()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def current_admin_email():
    email = (session.get("admin_email") or "").strip().lower()
    if email and email in _admin_emails():
        return email
    # Dev escape hatch
    dev = os.environ.get("DEV_ADMIN_EMAIL", "").strip().lower()
    if dev:
        return dev
    return None


def require_admin(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_admin_email():
            return redirect(url_for("public.admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped
