"""PayPal + Venmo deep links and QR code generation.

No payment processor account needed. We build deep links that pre-fill
amount and note (the order code) so reconciliation is one-shot.
"""
import base64
import io
import json
import urllib.parse

import qrcode

import store


def _cents_to_str(cents: int) -> str:
    return f"{(cents or 0) / 100:.2f}"


def paypal_link(amount_cents: int, note: str = "") -> str:
    """Return a paypal.me URL pre-filled with amount.

    Settings:
      paypal_url  — full override (e.g. https://paypal.me/RusticLove)
      paypal_handle — just the handle (with or without @)
    """
    url = (store.get_setting("paypal_url") or "").strip()
    if not url:
        handle = (store.get_setting("paypal_handle") or "").strip().lstrip("@")
        if not handle:
            return ""
        url = f"https://paypal.me/{handle}"
    amount = _cents_to_str(amount_cents)
    sep = "&" if "?" in url else "/"
    # paypal.me supports /AMOUNT/USD path syntax
    if "paypal.me" in url:
        return f"{url.rstrip('/')}/{amount}USD"
    # Fallback: just return base URL with note as query
    q = urllib.parse.urlencode({"amount": amount, "note": note})
    return f"{url}{sep}{q}"


def venmo_link(amount_cents: int, note: str = "") -> str:
    """Return a Venmo deep link pre-filled with amount and note.

    Settings:
      venmo_url    — full override
      venmo_handle — handle like @Rustic-Love (with or without @)
    """
    url = (store.get_setting("venmo_url") or "").strip()
    handle = (store.get_setting("venmo_handle") or "").strip().lstrip("@")
    amount = _cents_to_str(amount_cents)
    note_q = urllib.parse.quote(note or "Longest Table Vienna donation")
    if url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}amount={amount}&note={note_q}"
    if not handle:
        return ""
    # Universal link works on web + mobile and falls back to app
    return (
        f"https://venmo.com/{handle}"
        f"?txn=pay&amount={amount}&note={note_q}"
    )


def qr_png_data_uri(data: str, box_size: int = 8) -> str:
    """Return a base64 data URI for a PNG QR code of `data`."""
    if not data:
        return ""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1f1a17", back_color="#fffaf3")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def donation_payload(amount_cents: int, order_code: str = "") -> dict:
    """Build everything templates need to render the donate screen."""
    note = f"TLTV {order_code}".strip() if order_code else "Longest Table Vienna"
    paypal = paypal_link(amount_cents, note)
    venmo = venmo_link(amount_cents, note)
    return {
        "amount_cents": amount_cents,
        "amount_display": _cents_to_str(amount_cents),
        "note": note,
        "order_code": order_code,
        "paypal_url": paypal,
        "venmo_url": venmo,
        "paypal_qr": qr_png_data_uri(paypal) if paypal else "",
        "venmo_qr": qr_png_data_uri(venmo) if venmo else "",
        "has_paypal": bool(paypal),
        "has_venmo": bool(venmo),
        "tiers": _donation_tiers(),
    }


def _donation_tiers():
    raw = store.get_setting("donation_tiers_json", "") or "[]"
    try:
        tiers = json.loads(raw)
    except (TypeError, ValueError):
        tiers = []
    for t in tiers:
        t["amount_display"] = _cents_to_str(t.get("amount_cents", 0))
    return tiers
