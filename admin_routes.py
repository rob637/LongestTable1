"""Admin blueprint: dashboard, orders, donations, photos, settings."""
import csv
import io
import json
import os
import secrets

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response, abort, current_app)
from werkzeug.utils import secure_filename

import store
import orders as orders_db
import photo_storage
from auth import require_admin, current_admin_email
from models import DEFAULT_SETTINGS


bp = Blueprint("admin", __name__, url_prefix="/admin")


_ALLOWED_IMG = {"png", "jpg", "jpeg", "gif", "webp"}


def _event():
    out = {}
    for k, v in DEFAULT_SETTINGS.items():
        out[k] = store.get_setting(k, v)
    return out


@bp.route("/")
@require_admin
def dashboard():
    stats, totals = orders_db.stats_and_totals()
    return render_template(
        "admin/dashboard.html",
        active="dashboard",
        admin_email=current_admin_email(),
        event=_event(),
        stats=stats,
        totals=totals,
    )


@bp.route("/orders")
@require_admin
def orders():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    rows = orders_db.list_orders(status=status, search=q)
    return render_template(
        "admin/orders.html",
        active="orders",
        admin_email=current_admin_email(),
        event=_event(),
        orders=rows, q=q, status=status,
    )


@bp.route("/orders/export.csv")
@require_admin
def orders_export():
    rows = orders_db.list_orders()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order_code", "buyer_first", "buyer_last", "buyer_email",
                "buyer_phone", "party_size", "pledged_$", "paid_$",
                "status", "is_captain", "created_at"])
    for o in rows:
        w.writerow([o["order_code"], o["buyer_first"], o["buyer_last"],
                    o["buyer_email"], o["buyer_phone"], o["party_size"],
                    f"{o['pledged_donation_cents']/100:.2f}",
                    f"{o['paid_donation_cents']/100:.2f}",
                    o["status"], o["is_captain_order"], o["created_at"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=orders.csv"})


@bp.route("/donations")
@require_admin
def donations():
    return render_template(
        "admin/donations.html",
        active="donations",
        admin_email=current_admin_email(),
        event=_event(),
        donations=orders_db.list_donations(),
        summary=store.reconciliation_summary(),
    )


@bp.route("/donations/create", methods=["POST"])
@require_admin
def donations_create():
    f = request.form
    try:
        amount_cents = int(round(float(f.get("amount") or 0) * 100))
    except ValueError:
        amount_cents = 0
    if amount_cents <= 0:
        flash("Amount must be greater than zero.", "error")
        return redirect(url_for("admin.donations"))
    orders_db.record_donation(
        order_code=(f.get("order_code") or "").strip().upper(),
        amount_cents=amount_cents,
        source=(f.get("source") or "other").strip(),
        transaction_id=(f.get("transaction_id") or "").strip(),
        donor_name=(f.get("donor_name") or "").strip(),
        donor_email=(f.get("donor_email") or "").strip(),
        note=(f.get("note") or "").strip(),
    )
    flash("Donation recorded.", "success")
    return redirect(url_for("admin.donations"))


@bp.route("/donations/<int:did>/assign", methods=["POST"])
@require_admin
def donations_assign(did):
    new_code = (request.form.get("order_code") or "").strip().upper()
    if store.reassign_donation(did, new_code):
        flash("Donation " + ("attached to " + new_code if new_code else "unmatched") + ".", "success")
    else:
        flash("Donation not found.", "error")
    return redirect(url_for("admin.donations"))


@bp.route("/donations/<int:did>/delete", methods=["POST"])
@require_admin
def donations_delete(did):
    if store.delete_donation(did):
        flash("Donation deleted.", "success")
    else:
        flash("Donation not found.", "error")
    return redirect(url_for("admin.donations"))


@bp.route("/donations/report.csv")
@require_admin
def donations_report():
    """End-of-event donor report for Rustic Love."""
    summary = store.reconciliation_summary()
    donations = orders_db.list_donations(limit=10000)
    orders_by_code = {o["order_code"]: o for o in orders_db.list_orders()}

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["received_at", "amount_usd", "source", "transaction_id",
                "donor_name", "donor_email", "order_code", "order_buyer",
                "order_pledged_usd", "note"])
    for d in donations:
        code = d.get("order_code") or ""
        o = orders_by_code.get(code) or {}
        w.writerow([
            d.get("received_at", ""),
            "%.2f" % ((int(d.get("amount_cents") or 0)) / 100),
            d.get("source", ""),
            d.get("transaction_id", ""),
            d.get("donor_name", ""),
            d.get("donor_email", ""),
            code,
            (f"{o.get('buyer_first','')} {o.get('buyer_last','')}".strip()),
            "%.2f" % ((int(o.get("pledged_donation_cents") or 0)) / 100) if o else "",
            d.get("note", ""),
        ])
    w.writerow([])
    w.writerow(["TOTAL PAID", "%.2f" % (summary["paid_cents"] / 100)])
    w.writerow(["TOTAL PLEDGED", "%.2f" % (summary["pledged_cents"] / 100)])
    w.writerow(["UNMATCHED", "%.2f" % (summary["unmatched_cents"] / 100)])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=donation_report.csv"},
    )


# ── Photos ───────────────────────────────────────────────────────────────

@bp.route("/photos")
@require_admin
def photos():
    return render_template(
        "admin/photos.html",
        active="photos",
        admin_email=current_admin_email(),
        event=_event(),
        photos=orders_db.list_photos(),
    )


@bp.route("/photos/upload", methods=["POST"])
@require_admin
def photos_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file selected.", "error")
        return redirect(url_for("admin.photos"))
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_IMG:
        flash("Image must be PNG, JPG, GIF, or WebP.", "error")
        return redirect(url_for("admin.photos"))
    try:
        year = int(request.form.get("year") or 0) or None
    except ValueError:
        year = None
    filename, url, thumb_url = photo_storage.upload(f, ext=ext)
    orders_db.add_photo(
        collection=request.form.get("collection") or "event",
        filename=filename,
        url=url,
        thumb_url=thumb_url,
        caption=request.form.get("caption", ""),
        alt_text=request.form.get("alt_text", ""),
        credit=request.form.get("credit", ""),
        year=year,
        is_featured=bool(request.form.get("is_featured")),
    )
    flash("Photo uploaded.", "success")
    return redirect(url_for("admin.photos"))


@bp.route("/photos/<int:photo_id>/delete", methods=["POST"])
@require_admin
def photos_delete(photo_id):
    photo = orders_db.get_photo(photo_id)
    if photo and photo.get("filename"):
        photo_storage.delete(photo["filename"])
    orders_db.delete_photo(photo_id)
    flash("Photo deleted.", "success")
    return redirect(url_for("admin.photos"))


# ── Content (FAQ + sponsors + sponsor logo upload) ──────────────────────

@bp.route("/content")
@require_admin
def content():
    raw_faq = store.get_setting("faq_json", "[]") or "[]"
    raw_sponsors = store.get_setting("sponsors_json", "[]") or "[]"
    try:
        faq = json.loads(raw_faq)
    except (TypeError, ValueError):
        faq = []
    try:
        sponsors = json.loads(raw_sponsors)
    except (TypeError, ValueError):
        sponsors = []
    return render_template(
        "admin/content.html",
        active="content",
        admin_email=current_admin_email(),
        event=_event(),
        faq=faq,
        sponsors=sponsors,
        sponsors_heading=store.get_setting("sponsors_heading", ""),
        sponsors_intro=store.get_setting("sponsors_intro", ""),
    )


@bp.route("/content/save", methods=["POST"])
@require_admin
def content_save():
    f = request.form
    # FAQ
    faq_q = f.getlist("faq_q[]")
    faq_a = f.getlist("faq_a[]")
    faq = []
    for q, a in zip(faq_q, faq_a):
        q, a = q.strip(), a.strip()
        if q or a:
            faq.append({"q": q, "a": a})
    store.set_setting("faq_json", json.dumps(faq))
    # Sponsors
    s_name = f.getlist("s_name[]")
    s_tier = f.getlist("s_tier[]")
    s_url = f.getlist("s_url[]")
    s_logo = f.getlist("s_logo_url[]")
    sponsors = []
    for i, name in enumerate(s_name):
        name = name.strip()
        if not name:
            continue
        sponsors.append({
            "name": name,
            "tier": (s_tier[i] if i < len(s_tier) else "").strip(),
            "url": (s_url[i] if i < len(s_url) else "").strip(),
            "logo_url": (s_logo[i] if i < len(s_logo) else "").strip(),
        })
    store.set_setting("sponsors_json", json.dumps(sponsors))
    store.set_setting("sponsors_heading", (f.get("sponsors_heading") or "").strip())
    store.set_setting("sponsors_intro", (f.get("sponsors_intro") or "").strip())
    flash("Content saved.", "success")
    return redirect(url_for("admin.content"))


@bp.route("/content/sponsor-logo", methods=["POST"])
@require_admin
def content_sponsor_logo():
    """Upload a sponsor logo image, return JSON {url} for client to put into the row."""
    from flask import jsonify
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in _ALLOWED_IMG:
        return jsonify({"error": "Unsupported image type"}), 400
    _, url, _thumb = photo_storage.upload(f, ext=ext)
    return jsonify({"url": url})


# ── Settings ─────────────────────────────────────────────────────────────

@bp.route("/settings")
@require_admin
def settings():
    s = {k: store.get_setting(k, v) for k, v in DEFAULT_SETTINGS.items()}
    return render_template(
        "admin/settings.html",
        active="settings",
        admin_email=current_admin_email(),
        event=_event(),
        s=s,
    )


@bp.route("/settings/save", methods=["POST"])
@require_admin
def settings_save():
    editable = {
        "event_name", "event_tagline", "event_date", "event_time",
        "event_location", "event_year", "hero_headline", "hero_subhead",
        "about_blurb", "max_per_order", "max_attendees",
        "default_donation_per_attendee_cents", "paypal_handle", "paypal_url",
        "venmo_handle", "venmo_url", "organizer_name", "organizer_email",
        "from_email", "reply_to_email", "app_base_url", "admin_emails",
        "fundraising_goal_cents", "attendee_goal",
    }
    for k in editable:
        if k in request.form:
            store.set_setting(k, request.form.get(k, "").strip())
    # Checkbox: present = "1", absent = "0"
    store.set_setting("show_thermometer", "1" if request.form.get("show_thermometer") else "0")
    flash("Settings saved.", "success")
    return redirect(url_for("admin.settings"))


# ── Potluck signup categories ───────────────────────────────────────────

@bp.route("/potluck")
@require_admin
def potluck():
    return render_template(
        "admin/potluck.html",
        active="potluck",
        admin_email=current_admin_email(),
        event=_event(),
        categories=store.get_menu(),
    )


@bp.route("/potluck/save", methods=["POST"])
@require_admin
def potluck_save():
    # Update existing
    existing = {c["id"]: c for c in store.get_menu()}
    submitted_ids = set()
    names = request.form.getlist("cat_name[]")
    counts = request.form.getlist("cat_count[]")
    notes = request.form.getlist("cat_notes[]")
    orders_ = request.form.getlist("cat_sort[]")
    ids = request.form.getlist("cat_id[]")
    for i, name in enumerate(names):
        name = (name or "").strip()
        if not name:
            continue
        per = int(counts[i] or 1) if i < len(counts) else 1
        note = (notes[i] if i < len(notes) else "").strip()
        sort_order = int(orders_[i] or 0) if i < len(orders_) else 0
        cid_raw = ids[i] if i < len(ids) else ""
        try:
            cid = int(cid_raw)
        except (ValueError, TypeError):
            cid = 0
        if cid and cid in existing:
            store.update_menu_category(cid, {
                "name": name, "per_table_count": per,
                "notes": note, "sort_order": sort_order,
            })
            submitted_ids.add(cid)
        else:
            store.add_menu_category({
                "name": name, "per_table_count": per,
                "notes": note, "sort_order": sort_order,
            })
    # Delete categories not resubmitted
    for cid in existing:
        if cid not in submitted_ids:
            store.delete_menu_category(cid)
    flash("Potluck categories saved.", "success")
    return redirect(url_for("admin.potluck"))


# ── Sponsor prospects CRM ───────────────────────────────────────────────

@bp.route("/sponsors-crm")
@require_admin
def sponsors_crm():
    prospects = store.list_sponsor_prospects()
    summary = store.sponsor_pipeline_summary()
    return render_template(
        "admin/sponsors_crm.html",
        active="sponsors_crm",
        admin_email=current_admin_email(),
        event=_event(),
        prospects=prospects,
        summary=summary,
        stages=store.SPONSOR_STAGES,
    )


@bp.route("/sponsors-crm/save", methods=["POST"])
@require_admin
def sponsors_crm_save():
    sid_raw = request.form.get("id", "")
    fields = {
        "name": (request.form.get("name") or "").strip(),
        "contact_name": (request.form.get("contact_name") or "").strip(),
        "contact_email": (request.form.get("contact_email") or "").strip(),
        "contact_phone": (request.form.get("contact_phone") or "").strip(),
        "level": (request.form.get("level") or "").strip(),
        "amount_cents": int(round(float(request.form.get("amount") or 0) * 100)),
        "stage": (request.form.get("stage") or "prospect").strip(),
        "assigned_to": (request.form.get("assigned_to") or "").strip(),
        "last_contact_at": (request.form.get("last_contact_at") or "").strip(),
        "notes": (request.form.get("notes") or "").strip(),
        "url": (request.form.get("url") or "").strip(),
        "logo_url": (request.form.get("logo_url") or "").strip(),
    }
    if not fields["name"]:
        flash("Sponsor name is required.", "error")
        return redirect(url_for("admin.sponsors_crm"))
    if sid_raw:
        try:
            sid = int(sid_raw)
            store.update_sponsor_prospect(sid, fields)
            flash(f"Saved {fields['name']}.", "success")
        except (ValueError, TypeError):
            flash("Invalid id.", "error")
    else:
        store.create_sponsor_prospect(fields)
        flash(f"Added {fields['name']}.", "success")
    return redirect(url_for("admin.sponsors_crm"))


@bp.route("/sponsors-crm/<int:sid>/delete", methods=["POST"])
@require_admin
def sponsors_crm_delete(sid):
    store.delete_sponsor_prospect(sid)
    flash("Sponsor removed.", "success")
    return redirect(url_for("admin.sponsors_crm"))


@bp.route("/sponsors-crm/<int:sid>/promote", methods=["POST"])
@require_admin
def sponsors_crm_promote(sid):
    """Copy a CRM sponsor onto the public sponsor wall (sponsors_json setting)."""
    s = store.get_sponsor_prospect(sid)
    if not s:
        flash("Sponsor not found.", "error")
        return redirect(url_for("admin.sponsors_crm"))
    try:
        wall = json.loads(store.get_setting("sponsors_json") or "[]")
    except Exception:
        wall = []
    # Replace if name already on wall, else append
    new_entry = {
        "name": s.get("name") or "",
        "tier": s.get("level") or "",
        "url": s.get("url") or "",
        "logo_url": s.get("logo_url") or "",
    }
    replaced = False
    for i, w in enumerate(wall):
        if (w.get("name") or "").strip().lower() == new_entry["name"].strip().lower():
            wall[i] = new_entry
            replaced = True
            break
    if not replaced:
        wall.append(new_entry)
    store.set_setting("sponsors_json", json.dumps(wall))
    store.update_sponsor_prospect(sid, {"on_public_wall": 1})
    flash(f"{new_entry['name']} {'updated on' if replaced else 'added to'} the public wall.", "success")
    return redirect(url_for("admin.sponsors_crm"))


# ── Bridge to the existing table-assigner UI ────────────────────────────

@bp.route("/tables")
@require_admin
def assigner():
    # Old index.html is the rich tab UI for the assigner — preserve it
    return render_template("index.html")
