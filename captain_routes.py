"""Captain portal — magic-link login + table roster view."""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

import store
import orders as orders_db
import payments
import mailer
import auth


bp = Blueprint("captain", __name__, url_prefix="/captain")


def _event_settings():
    keys = [
        "event_name", "event_tagline", "event_date", "event_time",
        "event_location", "event_year", "hero_headline", "hero_subhead",
        "organizer_name", "organizer_email",
    ]
    return {k: store.get_setting(k, "") for k in keys}


def _abs_url(endpoint, **kwargs):
    base = (store.get_setting("app_base_url") or "").strip().rstrip("/")
    if base:
        return base + url_for(endpoint, **kwargs)
    return url_for(endpoint, _external=True, **kwargs)


@bp.route("/", methods=["GET", "POST"])
def login():
    event = _event_settings()
    if request.method == "GET":
        return render_template("public/captain_login.html", event=event)

    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Please enter your email.", "error")
        return redirect(url_for("captain.login"))

    matches = store.find_captain_orders_by_email(email)
    if matches:
        for o in matches:
            token = auth.make_token("captain", o["order_code"])
            url = _abs_url("captain.dashboard", token=token)
            mailer.send(
                email,
                f"Your Table Captain dashboard — {event['event_name']}",
                (f"Hi {o.get('buyer_first') or 'Captain'},\n\n"
                 f"Sign in to your Table Captain dashboard:\n{url}\n\n"
                 f"Order: {o['order_code']}\n"
                 f"This link is good for 30 days.\n"),
                template="captain_magic",
            )
    flash("If we found a captain order with that email, a sign-in link is on its way.",
          "success")
    return redirect(url_for("captain.login"))


@bp.route("/view/<token>")
def dashboard(token):
    code = auth.read_token(token, "captain")
    if not code:
        abort(403)
    event = _event_settings()
    data = orders_db.get_order(code)
    if not data:
        abort(404)
    if not data["order"].get("is_captain_order"):
        abort(403)

    is_locked = str(store.get_setting("is_locked") or "0") == "1"
    table_number = store.find_table_for_order(code) if is_locked else None
    roster = store.get_table_roster(table_number) if table_number else None

    # Potluck board — only meaningful once the table is assigned
    categories, signups = ([], [])
    if table_number:
        categories, signups = store.get_signup_info(table_number)
    sigs_by_cat = {}
    for s in signups:
        sigs_by_cat.setdefault(s["category_id"], []).append(s)

    # Magic link for sharing the registration page (suggested signup link)
    invite_url = _abs_url("public.register")

    # Pre-built donate link for captain themselves
    donate_token = auth.make_token("order", code)

    # Flex Participants (waitlist) available to pull in — only when locked
    flex_orders = store.list_flex_orders() if (is_locked and table_number) else []

    return render_template(
        "public/captain.html",
        event=event,
        data=data,
        token=token,
        is_locked=is_locked,
        table_number=table_number,
        roster=roster,
        invite_url=invite_url,
        donate_token=donate_token,
        categories=categories,
        sigs_by_cat=sigs_by_cat,
        flex_orders=flex_orders,
    )


def _captain_table(token):
    code = auth.read_token(token, "captain")
    if not code:
        abort(403)
    data = orders_db.get_order(code)
    if not data or not data["order"].get("is_captain_order"):
        abort(403)
    tn = store.find_table_for_order(code)
    if not tn:
        abort(400)
    return code, tn


@bp.route("/view/<token>/potluck/add", methods=["POST"])
def potluck_add(token):
    code, table_number = _captain_table(token)
    try:
        cat_id = int(request.form.get("category_id") or 0)
    except ValueError:
        cat_id = 0
    person = (request.form.get("person_name") or "").strip()
    item = (request.form.get("item_description") or "").strip()
    if not cat_id or not person:
        flash("Please pick a category and enter a name.", "error")
    else:
        err = store.add_signup(table_number, cat_id, person, item)
        if err:
            flash(err, "error")
        else:
            flash("Added to the potluck board.", "success")
    return redirect(url_for("captain.dashboard", token=token))


@bp.route("/view/<token>/potluck/delete/<int:sig_id>", methods=["POST"])
def potluck_delete(token, sig_id):
    _captain_table(token)
    store.delete_signup(sig_id)
    flash("Removed from the potluck board.", "success")
    return redirect(url_for("captain.dashboard", token=token))


# ── Roster management (Captain-as-manager, per Steve's spec) ─────────────────

def _roster_pids(table_number):
    roster = store.get_table_roster(table_number) or {}
    return {int(a.get("id") or 0) for a in roster.get("attendees", [])}


@bp.route("/view/<token>/attendee/<int:pid>/save", methods=["POST"])
def attendee_save(token, pid):
    code, table_number = _captain_table(token)
    if pid not in _roster_pids(table_number):
        abort(403)
    fields = {
        "first_name": request.form.get("first_name", ""),
        "last_name": request.form.get("last_name", ""),
        "email": request.form.get("email", ""),
        "phone": request.form.get("phone", ""),
        "age_range": request.form.get("age_range", "Adult"),
    }
    store.update_attendee(pid, fields)
    flash("Attendee updated.", "success")
    return redirect(url_for("captain.dashboard", token=token))


@bp.route("/view/<token>/attendee/<int:pid>/remove", methods=["POST"])
def attendee_remove(token, pid):
    code, table_number = _captain_table(token)
    if pid not in _roster_pids(table_number):
        abort(403)
    # Locate the order this attendee belongs to (within this table).
    roster = store.get_table_roster(table_number)
    target_code = None
    for grp in roster.get("orders", []):
        if any(int(p.get("id") or 0) == pid for p in grp["people"]):
            target_code = grp["order_code"]
            break
    if not target_code:
        abort(404)
    orders_db.remove_attendee(target_code, pid)
    store.upsert_assignment(pid, None)
    flash("Attendee removed.", "success")
    return redirect(url_for("captain.dashboard", token=token))


@bp.route("/view/<token>/recruit/add", methods=["POST"])
def recruit_add(token):
    code, table_number = _captain_table(token)
    first = (request.form.get("first_name") or "").strip()
    last = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip()
    if not (first and last and email):
        flash("First name, last name, and email are required.", "error")
        return redirect(url_for("captain.dashboard", token=token))
    store.add_attendee_to_order(
        code,
        {
            "first_name": first, "last_name": last, "email": email,
            "phone": request.form.get("phone", ""),
            "age_range": request.form.get("age_range", "Adult"),
        },
        table_number=table_number,
    )
    flash(f"{first} {last} added to Table {table_number}.", "success")
    return redirect(url_for("captain.dashboard", token=token))


@bp.route("/view/<token>/flex/<flex_code>/pull", methods=["POST"])
def flex_pull(token, flex_code):
    code, table_number = _captain_table(token)
    flex_code = (flex_code or "").strip().upper()
    flex = orders_db.get_order(flex_code)
    if not flex or flex["order"].get("status") != "waitlist":
        flash("That Flex Participant is no longer available.", "error")
        return redirect(url_for("captain.dashboard", token=token))
    seated = store.pull_flex_to_table(flex_code, table_number)
    event = _event_settings()
    if seated and flex["order"].get("buyer_email"):
        order_token = auth.make_token("order", flex_code)
        order_url = _abs_url("public.order_view", token=order_token)
        mailer.send(
            flex["order"]["buyer_email"],
            f"You're in! Table {table_number} at {event['event_name']}",
            (f"Hi {flex['order'].get('buyer_first') or ''},\n\n"
             f"Great news — a Table Captain has pulled your Flex Participant "
             f"registration to Table {table_number}. You're confirmed for "
             f"{event['event_name']} on {event['event_date']}.\n\n"
             f"Manage your order: {order_url}\n\n"
             f"— {event['organizer_name']}\n"),
            template="flex_pulled",
        )
    flash(f"Pulled {seated} guest{'s' if seated != 1 else ''} from Flex to Table {table_number}.",
          "success")
    return redirect(url_for("captain.dashboard", token=token))
