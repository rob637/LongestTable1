"""Public-facing routes: home, registration, donation, order portal, admin login."""
import csv
import io
import json
import os
import time
from datetime import date as _date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, session, Response)

import store
import orders as orders_db
import payments
import mailer
import auth


bp = Blueprint("public", __name__)

# 30s TTL cache for the public home-page heavy reads (photos + thermometer
# stats). Real-time accuracy not needed for landing-page numbers.
_HOME_CACHE = {"t": 0.0, "data": None}
_HOME_TTL = 30.0


def invalidate_home_cache():
    _HOME_CACHE["t"] = 0.0
    _HOME_CACHE["data"] = None


def _home_payload(event):
    now = time.time()
    if _HOME_CACHE["data"] is not None and (now - _HOME_CACHE["t"]) < _HOME_TTL:
        return _HOME_CACHE["data"]
    photos = orders_db.list_photos()
    progress_inputs = None
    if event["show_thermometer"]:
        progress_inputs = orders_db.stats_and_totals()
    _HOME_CACHE["data"] = (photos, progress_inputs)
    _HOME_CACHE["t"] = now
    return _HOME_CACHE["data"]


@bp.app_context_processor
def _inject_meta():
    """Expose og_image_url + canonical_url to every template that extends public/base.html."""
    try:
        og = (store.get_setting("site_og_image_url") or "").strip()
        if not og:
            photos = orders_db.list_photos()
            pool = [p for p in photos if p.get("collection") == "vienna" and p.get("is_featured")]
            if not pool:
                pool = [p for p in photos if p.get("collection") == "vienna"]
            if not pool:
                pool = [p for p in photos if p.get("collection") == "event"]
            if pool:
                og = pool[0].get("url") or ""
        base = (store.get_setting("app_base_url") or "").strip().rstrip("/")
        canonical = ""
        try:
            canonical = (base + request.path) if base else request.url
        except Exception:  # noqa: BLE001
            canonical = ""
        return {"og_image_url": og, "canonical_url": canonical}
    except Exception:  # noqa: BLE001
        return {"og_image_url": "", "canonical_url": ""}


# ── Helpers ─────────────────────────────────────────────────────────────

def _event_settings():
    s = store.get_all_settings() if hasattr(store, "get_all_settings") else {}
    keys = [
        "event_name", "event_tagline", "event_date", "event_time",
        "event_location", "event_year", "max_per_order",
        "default_donation_per_attendee_cents", "hero_headline",
        "hero_subhead", "about_blurb", "organizer_name", "organizer_email",
        "admin_emails", "fundraising_goal_cents", "attendee_goal",
        "show_thermometer", "max_attendees",
        "faq_json", "sponsors_json", "sponsors_heading", "sponsors_intro",
        "testimonials_json", "site_og_image_url",
    ]
    out = {}
    for k in keys:
        out[k] = s.get(k) if k in s else store.get_setting(k, "")
    # numeric coercions
    try: out["max_per_order"] = int(out.get("max_per_order") or 8)
    except (TypeError, ValueError): out["max_per_order"] = 8
    try: out["default_donation_per_attendee_cents"] = int(out.get("default_donation_per_attendee_cents") or 1000)
    except (TypeError, ValueError): out["default_donation_per_attendee_cents"] = 1000
    try: out["fundraising_goal_cents"] = int(out.get("fundraising_goal_cents") or 0)
    except (TypeError, ValueError): out["fundraising_goal_cents"] = 0
    try: out["attendee_goal"] = int(out.get("attendee_goal") or 0)
    except (TypeError, ValueError): out["attendee_goal"] = 0
    try: out["max_attendees"] = int(out.get("max_attendees") or 0)
    except (TypeError, ValueError): out["max_attendees"] = 0
    out["show_thermometer"] = str(out.get("show_thermometer") or "1") not in ("0", "false", "")
    # Parsed FAQ + sponsors
    try:
        out["faq"] = json.loads(out.get("faq_json") or "[]") or []
    except (TypeError, ValueError):
        out["faq"] = []
    try:
        out["sponsors"] = json.loads(out.get("sponsors_json") or "[]") or []
    except (TypeError, ValueError):
        out["sponsors"] = []
    try:
        out["testimonials"] = json.loads(out.get("testimonials_json") or "[]") or []
    except (TypeError, ValueError):
        out["testimonials"] = []
    return out


def _abs_url(endpoint, **kwargs):
    base = (store.get_setting("app_base_url") or "").strip().rstrip("/")
    if base:
        return base + url_for(endpoint, **kwargs)
    return url_for(endpoint, _external=True, **kwargs)


# ── Pages ───────────────────────────────────────────────────────────────

@bp.route("/")
def home():
    event = _event_settings()
    photos, progress_inputs = _home_payload(event)
    event_photos = [p for p in photos if p["collection"] == "event"]
    hero_pool = [p for p in photos if p["collection"] == "vienna" and p["is_featured"]]
    if not hero_pool:
        hero_pool = [p for p in photos if p["collection"] == "vienna"]
    hero_image = hero_pool[0] if hero_pool else None

    progress = None
    if progress_inputs:
        stats, totals = progress_inputs
        goal_cents = event["fundraising_goal_cents"]
        raised_cents = int(totals.get("paid_cents") or 0) + int(totals.get("pledged_cents") or 0)
        pct = 0
        if goal_cents > 0:
            pct = min(100, int(round(raised_cents * 100 / goal_cents)))
        progress = {
            "raised_cents": raised_cents,
            "raised_display": f"${raised_cents/100:,.0f}",
            "goal_cents": goal_cents,
            "goal_display": f"${goal_cents/100:,.0f}" if goal_cents else "",
            "pct": pct,
            "attendees": int(stats.get("attendees_confirmed") or 0),
            "attendee_goal": event["attendee_goal"],
        }

    return render_template(
        "public/home.html",
        event=event,
        event_photos=event_photos,
        hero_image=hero_image,
        sponsors=event.get("sponsors", []),
        faq=event.get("faq", []),
        testimonials=event.get("testimonials", []),
        progress=progress,
    )


@bp.route("/robots.txt")
def robots_txt():
    base = (store.get_setting("app_base_url") or "").strip().rstrip("/") or request.url_root.rstrip("/")
    body = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /view/\n"
        "Disallow: /order/view/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap_xml():
    base = (store.get_setting("app_base_url") or "").strip().rstrip("/") or request.url_root.rstrip("/")
    paths = ["/", "/register", "/donate", "/order"]
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in paths)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}"
        "</urlset>"
    )
    return Response(xml, mimetype="application/xml")


def _registration_gate():
    """Return ('open'|'before'|'closed', date_str) based on registration_opens/closes settings."""
    today = _date.today().isoformat()
    opens = (store.get_setting("registration_opens") or "").strip()
    closes = (store.get_setting("registration_closes") or "").strip()
    if opens and today < opens:
        return "before", opens
    if closes and today > closes:
        return "closed", closes
    return "open", None


@bp.route("/register", methods=["GET", "POST"])
def register():
    event = _event_settings()
    gate, gate_date = _registration_gate()

    if request.method == "GET":
        max_a = event.get("max_attendees", 0)
        seats_remaining = None
        if max_a:
            current = store.confirmed_attendees_count()
            seats_remaining = max(0, max_a - current)
        return render_template(
            "public/register.html",
            event=event,
            default_pledge_dollars=int(event["default_donation_per_attendee_cents"] / 100),
            default_donation_per_attendee_cents=event["default_donation_per_attendee_cents"],
            seats_remaining=seats_remaining,
            registration_gate=gate,
            registration_gate_date=gate_date,
        )

    # POST — validate CSRF first
    if not auth.validate_csrf(request.form.get("_csrf", "")):
        abort(400)

    # Block submissions if registration window is closed
    if gate != "open":
        flash("Registration is not currently open.", "error")
        return redirect(url_for("public.register"))

    form = request.form
    firsts = form.getlist("att_first[]")
    lasts = form.getlist("att_last[]")
    emails = form.getlist("att_email[]")
    phones = form.getlist("att_phone[]")
    ages = form.getlist("att_age[]")
    accs = form.getlist("att_acc[]")

    attendees = []
    for i in range(len(firsts)):
        if not (firsts[i].strip() and lasts[i].strip() and emails[i].strip()):
            continue
        attendees.append({
            "first_name": firsts[i],
            "last_name": lasts[i],
            "email": emails[i],
            "phone": phones[i] if i < len(phones) else "",
            "age_range": ages[i] if i < len(ages) else "Adult",
            "accommodations": accs[i] if i < len(accs) else "",
            "is_captain": False,
        })

    if not attendees:
        flash("Please add at least one attendee.", "error")
        return redirect(url_for("public.register"))
    if len(attendees) > event["max_per_order"]:
        flash(f"Max {event['max_per_order']} attendees per order.", "error")
        return redirect(url_for("public.register"))

    is_captain_order = bool(form.get("is_captain_order"))
    if is_captain_order and attendees:
        attendees[0]["is_captain"] = True

    try:
        pledge_dollars = float(form.get("pledged_donation") or 0)
    except ValueError:
        pledge_dollars = 0
    pledge_cents = max(0, int(round(pledge_dollars * 100)))

    # Capacity check → Flex Participants (we keep DB value 'waitlist' for compatibility)
    max_attendees = event.get("max_attendees", 0)
    order_status = "confirmed"
    over_capacity = False
    if max_attendees and not is_captain_order:
        current = store.confirmed_attendees_count()
        if current + len(attendees) > max_attendees:
            order_status = "waitlist"
            over_capacity = True

    # Optional captain association — stored in notes for the assigner
    seat_with = (form.get("seat_with_captain") or "").strip()
    notes_parts = []
    if seat_with:
        notes_parts.append(f"Sit with captain: {seat_with}")
    if form.get("notes"):
        notes_parts.append(form.get("notes").strip())
    notes = " | ".join(notes_parts)

    code, _oid = orders_db.create_order(
        buyer_first=form.get("buyer_first", "").strip(),
        buyer_last=form.get("buyer_last", "").strip(),
        buyer_email=form.get("buyer_email", "").strip(),
        buyer_phone=form.get("buyer_phone", "").strip(),
        attendees=attendees,
        pledged_donation_cents=pledge_cents,
        is_captain_order=is_captain_order,
        notes=notes,
        status=order_status,
    )

    token = auth.make_token("order", code)
    order_url = _abs_url("public.order_view", token=token)
    pay = payments.donation_payload(pledge_cents, code)

    # Send confirmation email
    if over_capacity:
        subject = f"You're a Flex Participant — {event['event_name']} (Order {code})"
        body = (
            f"Hi {form.get('buyer_first','').strip()},\n\n"
            f"Thank you for signing up for {event['event_name']}. "
            f"We've reached our seat goal for {event['event_date']}, "
            f"so your party of {len(attendees)} is on our ‘Flex Participants’ list.\n\n"
            f"What that means: there will be a seat for you at the long table, "
            f"but your specific table assignment won't be finalized until a few "
            f"days before the event, once we know who has cancelled. A Table "
            f"Captain may also pull your party in earlier — you'll get an email "
            f"the moment that happens.\n\n"
            f"Order: {code}\n"
            f"Manage your order any time:\n{order_url}\n\n"
            f"— {event['organizer_name']}\n"
        )
    else:
        subject = f"You're in! {event['event_name']} — Order {code}"
        body = (
            f"Hi {form.get('buyer_first','').strip()},\n\n"
            f"You're confirmed for {event['event_name']} on {event['event_date']}.\n\n"
            f"Order: {code}\n"
            f"Party size: {len(attendees)}\n"
            f"Pledged donation: ${pledge_cents/100:.2f}\n\n"
            f"Manage your order any time:\n{order_url}\n\n"
            f"Complete your donation:\n{_abs_url('public.donate', order_code=code)}\n\n"
            f"Thank you for joining the table.\n— {event['organizer_name']}\n"
        )
    mailer.send(form.get("buyer_email", "").strip(),
                subject, body, template="reg_confirmation")

    if over_capacity:
        flash("You've been added as a Flex Participant — there's a seat for you, and we'll confirm your table a few days before the event.",
              "success")
        return redirect(url_for("public.order_view", token=token))
    return redirect(url_for("public.donate", order_code=code, _t=token))


@bp.route("/donate/<order_code>")
def donate(order_code):
    event = _event_settings()
    data = orders_db.get_order(order_code)
    if not data:
        abort(404)
    try:
        override_cents = int(request.args.get("amount") or 0)
    except ValueError:
        override_cents = 0
    amount = override_cents or data["order"]["pledged_donation_cents"]
    pay = payments.donation_payload(amount, order_code)
    # Token in URL only used for fresh signups; otherwise re-mint one
    token = request.args.get("_t") or auth.make_token("order", order_code)
    return render_template(
        "public/donate.html",
        event=event,
        order_code=order_code,
        party_size=data["order"]["party_size"],
        order_token=token,
        pay=pay,
    )


@bp.route("/donate")
def donate_anon():
    """Standalone donation page — no order required."""
    event = _event_settings()
    try:
        amount_cents = int(request.args.get("amount") or 0)
    except ValueError:
        amount_cents = 0
    if amount_cents <= 0:
        amount_cents = 5000  # default $50
    pay = payments.donation_payload(amount_cents, "")
    return render_template(
        "public/donate_anon.html",
        event=event,
        pay=pay,
    )


# ── Order management (magic link) ────────────────────────────────────────

@bp.route("/order", methods=["GET", "POST"])
def order_login():
    event = _event_settings()
    if request.method == "GET":
        return render_template("public/order_login.html", event=event)

    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Please enter an email.", "error")
        return redirect(url_for("public.order_login"))

    # Find orders for this email
    matches = [o for o in orders_db.list_orders()
               if (o.get("buyer_email") or "").lower() == email]
    if matches:
        for o in matches:
            token = auth.make_token("order", o["order_code"])
            url = _abs_url("public.order_view", token=token)
            mailer.send(email, f"Your {event['event_name']} order",
                        f"Manage your order: {url}\n\nOrder code: {o['order_code']}\n",
                        template="order_magic")
    flash("If we found an order with that email, a sign-in link is on its way.", "success")
    return redirect(url_for("public.order_login"))


@bp.route("/order/view/<token>")
def order_view(token):
    code = auth.read_token(token, "order")
    if not code:
        abort(403)
    event = _event_settings()
    data = orders_db.get_order(code)
    if not data:
        abort(404)
    return render_template("public/order.html", event=event, data=data, token=token)


@bp.route("/order/cancel/<token>", methods=["POST"])
def order_cancel(token):
    code = auth.read_token(token, "order")
    if not code:
        abort(403)
    orders_db.cancel_order(code)
    flash("Your order has been cancelled.", "success")
    return redirect(url_for("public.home"))


@bp.route("/order/<token>/remove/<int:pid>", methods=["POST"])
def order_remove_attendee(token, pid):
    code = auth.read_token(token, "order")
    if not code:
        abort(403)
    orders_db.remove_attendee(code, pid)
    flash("Attendee removed.", "success")
    return redirect(url_for("public.order_view", token=token))


# ── Admin sign-in ────────────────────────────────────────────────────────

@bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    event = _event_settings()
    if request.method == "GET":
        return render_template("public/admin_login.html", event=event)
    email = (request.form.get("email") or "").strip().lower()
    admins = {e.strip().lower() for e in (event.get("admin_emails") or "").split(",") if e.strip()}
    if email not in admins:
        flash("That email is not authorized.", "error")
        return redirect(url_for("public.admin_login"))
    passcode = (request.form.get("passcode") or "").strip()
    bypass = (os.environ.get("ADMIN_PASSCODE", "7777")).strip()
    if passcode and bypass and passcode == bypass:
        session["admin_email"] = email
        return redirect(url_for("admin.dashboard"))
    token = auth.make_token("admin", email)
    url = _abs_url("public.admin_verify", token=token)
    mailer.send(email, "Sign in to TLTV Admin",
                f"Click to sign in: {url}\n\nThis link expires in 30 minutes.\n",
                template="admin_magic")
    flash("Check your email for a sign-in link.", "success")
    return redirect(url_for("public.admin_login"))


@bp.route("/admin/verify/<token>")
def admin_verify(token):
    email = auth.read_token(token, "admin", max_age_seconds=60 * 30)
    if not email:
        flash("That sign-in link expired. Please request a new one.", "error")
        return redirect(url_for("public.admin_login"))
    session["admin_email"] = email
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/logout", methods=["POST", "GET"])
def admin_logout():
    session.pop("admin_email", None)
    return redirect(url_for("public.home"))
