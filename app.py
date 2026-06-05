"""Longest Table — Flask web app (SQLite locally, Firestore on Cloud Run)."""
import csv
import io
import os
import secrets
from flask import Flask, jsonify, render_template, request, Response, abort
from flask_cors import CORS

import store
from models import init_db
from assigner import assign_tables
from seed import seed

app = Flask(__name__)
CORS(app)

APP_VERSION = "1.3.0"

# Structured logging + global error handler (Cloud Run-aware)
import logging_setup
logging_setup.configure(app)

# Initialize SQLite schema/settings on import so blueprints can rely on it
if not store.USE_FIRESTORE:
    init_db()

# Session secret for admin login (signed cookies)
app.secret_key = (
    os.environ.get("FLASK_SECRET")
    or store.get_setting("secret_key")
    or secrets.token_urlsafe(32)
)

# Register Phase 1 blueprints (public site + admin shell)
from public_routes import bp as public_bp
from admin_routes import bp as admin_bp
from captain_routes import bp as captain_bp
app.register_blueprint(public_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(captain_bp)

# CSRF token available in all templates
from auth import get_csrf_token
@app.context_processor
def _inject_csrf():
    return {"csrf_token": get_csrf_token, "app_version": APP_VERSION}

# Custom error pages
@app.errorhandler(404)
def _page_not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(403)
def _forbidden(e):
    return render_template("errors/404.html"), 403  # treat 403 same as 404 for UX


def _format_phone(raw):
    """Normalize a phone string to (XXX) XXX-XXXX for US numbers; leave others alone."""
    if not raw:
        return ""
    s = str(raw).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return s


def _participants():
    people = store.get_participants()
    for p in people:
        if p.get("phone"):
            p["phone"] = _format_phone(p["phone"])
    return people


def _split_test_table99_people(all_people):
    test_people = [
        p for p in all_people
        if (p.get("order_id") or "").strip().upper() == "TEST-T99"
    ]
    non_test_people = [
        p for p in all_people
        if (p.get("order_id") or "").strip().upper() != "TEST-T99"
    ]
    return non_test_people, test_people


def _apply_test_table99_override(tables, test_people):
    if not test_people:
        return tables

    parties = {}
    for p in test_people:
        oid = p.get("order_id", "")
        parties.setdefault(oid, {"order_id": oid, "people": []})["people"].append(p)

    table99 = {
        "number": 99,
        "people": test_people,
        "captain": next((p for p in test_people if p.get("is_captain")), None),
        "parties": list(parties.values()),
        "is_singles_table": False,
    }

    cleaned = [t for t in tables if t.get("number") != 99]
    cleaned.append(table99)
    cleaned.sort(key=lambda x: x.get("number", 0))
    return cleaned


def _finalize_result(result, is_locked=False, has_saved_draft=False):
    seats_per_table = int(store.get_setting("seats_per_table", "14") or 14)
    summary = result.get("summary", {})
    summary.update({
        "total_people": sum(len(t.get("people", [])) for t in result.get("tables", [])),
        "num_tables": len(result.get("tables", [])),
        "total_capacity": len(result.get("tables", [])) * seats_per_table,
        "captains_available": sum(1 for t in result.get("tables", []) if t.get("captain")),
        "is_locked": is_locked,
        "has_saved_draft": has_saved_draft,
    })
    result["summary"] = summary
    return result


def _result_from_assignment_map(all_people, assignments, warnings=None, is_locked=False):
    tables_map = {}
    for p in all_people:
        tn = assignments.get(p["id"])
        if tn is None:
            continue
        if tn not in tables_map:
            tables_map[tn] = {
                "number": tn,
                "people": [],
                "parties": {},
                "is_singles_table": False,
            }
        tables_map[tn]["people"].append(p)
        oid = p.get("order_id", "")
        if oid not in tables_map[tn]["parties"]:
            tables_map[tn]["parties"][oid] = {"order_id": oid, "people": []}
        tables_map[tn]["parties"][oid]["people"].append(p)
    tables_list = sorted(tables_map.values(), key=lambda x: x["number"])
    for t in tables_list:
        t["captain"] = next((p for p in t["people"] if p.get("is_captain")), None)
        t["parties"] = list(t["parties"].values())
    return _finalize_result({
        "tables": tables_list,
        "warnings": warnings or [],
        "unplaced": [],
        "summary": {"num_singles": 0, "num_singles_tables": 0},
    }, is_locked=is_locked, has_saved_draft=not is_locked)


def _build_empty_unlocked_result(all_people=None):
    if all_people is None:
        all_people = _participants()
    seats_per_table = int(store.get_setting("seats_per_table", "14") or 14)
    return {
        "tables": [],
        "warnings": ["No saved draft assignments yet. Click Generate Assignments to create tables."],
        "unplaced": [],
        "summary": {
            "total_people": len(all_people),
            "num_tables": 0,
            "num_singles": 0,
            "num_singles_tables": 0,
            "seats_per_table": seats_per_table,
            "total_capacity": 0,
            "captains_available": sum(1 for p in all_people if p.get("is_captain")),
            "is_locked": False,
            "has_saved_draft": False,
        },
    }


def _generate_unlocked_result(all_people=None):
    if all_people is None:
        all_people = _participants()
    non_test_people, test_people = _split_test_table99_people(all_people)
    rules = store.get_rules()
    tc = int(store.get_setting("table_count", "0") or 0)
    result = assign_tables(non_test_people, rules, tc)
    result["tables"] = _apply_test_table99_override(result.get("tables", []), test_people)
    return _finalize_result(result, is_locked=False, has_saved_draft=True)


def _get_unlocked_result(all_people=None):
    if all_people is None:
        all_people = _participants()
    draft_result = store.get_draft_result()
    if draft_result:
        return _finalize_result(draft_result, is_locked=False, has_saved_draft=True)
    return _build_empty_unlocked_result(all_people)


def _save_generated_draft(all_people=None):
    result = _generate_unlocked_result(all_people)
    store.save_draft_result(result)
    return result


def _get_or_create_unlocked_draft(all_people=None):
    if all_people is None:
        all_people = _participants()
    draft_result = store.get_draft_result()
    if draft_result:
        return _finalize_result(draft_result, is_locked=False, has_saved_draft=True)
    return _save_generated_draft(all_people)


def _invalidate_unlocked_draft():
    if store.get_setting("is_locked") != "1":
        store.clear_draft_result()


# ── Pages ─────────────────────────────────────────────────────────────────────
# Note: "/" is served by the public blueprint (public_routes.home).
# The legacy assigner UI is mounted at /admin/tables.


@app.route("/signup/<token>")
def signup_page(token):
    if not store.get_table_for_token(token):
        abort(404)
    return render_template("signup.html", token=token)


# ── Participants ──────────────────────────────────────────────────────────────
@app.route("/api/participants", methods=["GET"])
def api_participants():
    people = _participants()
    is_locked = store.get_setting("is_locked") == "1"

    if is_locked:
        return jsonify(people)


    # Keep Participants tab consistent with Tables tab when unlocked.
    result = _get_unlocked_result(people)
    table_by_pid = {}
    for t in result["tables"]:
        for p in t["people"]:
            table_by_pid[p["id"]] = t["number"]

    for p in people:
        p["table_number"] = table_by_pid.get(p["id"])

    return jsonify(people)


@app.route("/api/participants/<int:pid>", methods=["PATCH"])
def api_update_participant(pid):
    data = request.get_json(force=True)
    allowed = {"first_name", "last_name", "email", "phone", "age_range",
               "is_captain", "accommodations", "order_id", "table_number"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"error": "no valid fields"}), 400
    if "is_captain" in fields:
        fields["is_captain"] = 1 if fields["is_captain"] else 0
    table_number = fields.pop("table_number", None)
    if table_number is not None and store.get_setting("is_locked") == "1":
        store.upsert_assignment(pid, table_number)
    if store.USE_FIRESTORE:
        if fields:
            store._col("participants").document(str(pid)).update(fields)
    else:
        from models import get_db
        conn = get_db()
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE participants SET {sets} WHERE id = ?",
                         list(fields.values()) + [pid])
        conn.commit()
        conn.close()
    _invalidate_unlocked_draft()
    return jsonify({"ok": True})


@app.route("/api/participants/<int:pid>", methods=["DELETE"])
def api_delete_participant(pid):
    store.delete_participant(pid)
    _invalidate_unlocked_draft()
    return jsonify({"ok": True})


@app.route("/api/participants", methods=["POST"])
def api_add_participant():
    store.add_participant(request.get_json(force=True))
    _invalidate_unlocked_draft()
    return jsonify({"ok": True})


# ── Rules ─────────────────────────────────────────────────────────────────────
@app.route("/api/rules", methods=["GET", "POST"])
def api_rules():
    if request.method == "GET":
        return jsonify({
            "rules": store.get_rules(),
            "table_count": int(store.get_setting("table_count", "0") or 0),
        })
    data = request.get_json(force=True)
    rules = data.get("rules", {})
    for k in ("seats_per_table", "min_singles_per_table",
              "min_children_per_table", "min_teens_per_table"):
        if k in rules:
            try:
                rules[k] = int(rules[k])
            except (TypeError, ValueError):
                rules[k] = 0
    for k in ("one_captain_per_table", "keep_groups_together",
              "split_oversize_groups", "spread_evenly", "spread_seniors"):
        if k in rules:
            rules[k] = bool(rules[k])
    store.set_rules(rules)
    if "table_count" in data:
        try:
            store.set_setting("table_count", int(data["table_count"]))
        except (TypeError, ValueError):
            store.set_setting("table_count", 0)
    _invalidate_unlocked_draft()
    return jsonify({"ok": True})


# ── Assignments ───────────────────────────────────────────────────────────────
@app.route("/api/assign", methods=["GET", "POST"])
def api_assign():
    if request.method == "POST":
        data = request.get_json(force=True) if request.data else {}
        action = data.get("action")
        if action == "lock":
            result = store.get_draft_result() or _save_generated_draft(_participants())
            store.save_assignments(result["tables"])
            store.clear_draft_result()
            store.set_setting("is_locked", "1")
            return jsonify({"ok": True, "locked": True})
        elif action == "unlock":
            all_people = _participants()
            locked_result = _result_from_assignment_map(
                all_people,
                store.get_assignments(),
                warnings=["Draft restored from locked assignments."],
                is_locked=False,
            )
            if locked_result["tables"]:
                store.save_draft_result(locked_result)
            else:
                store.clear_draft_result()
            store.set_setting("is_locked", "0")
            return jsonify({"ok": True, "locked": False})
        result = _save_generated_draft(_participants())
        return jsonify(result)

    is_locked = store.get_setting("is_locked") == "1"

    if is_locked:
        return jsonify(_result_from_assignment_map(
            store.get_participants(),
            store.get_assignments(),
            warnings=["🔒 Assignments LOCKED. Use Participants tab to move people."],
            is_locked=True,
        ))

    return jsonify(_get_unlocked_result(_participants()))


@app.route("/api/export.csv")
def api_export():
    is_locked = store.get_setting("is_locked") == "1"
    if is_locked:
        tables = _result_from_assignment_map(_participants(), store.get_assignments(), is_locked=True)["tables"]
    else:
        tables = _get_unlocked_result(_participants())["tables"]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order #", "Table", "First Name", "Last Name",
                "Email", "Phone", "Buyer Last Name", "Buyer First Name",
                "Buyer Email", "Age Range", "Captain"])
    for t in tables:
        # Captain(s) first, then everyone else in stable order
        people_sorted = sorted(
            t["people"],
            key=lambda p: (0 if p.get("is_captain") else 1,
                           (p.get("last_name") or "").lower(),
                           (p.get("first_name") or "").lower()),
        )
        for p in people_sorted:
            email = (p.get("email") or "").strip() or (p.get("buyer_email") or "").strip()
            w.writerow([p.get("order_id", ""), t["number"],
                        p.get("first_name", ""), p.get("last_name", ""),
                        email, p.get("phone", ""),
                        p.get("buyer_last", ""), p.get("buyer_first", ""),
                        p.get("buyer_email", ""), p.get("age_range", ""),
                        "CAPTAIN" if p.get("is_captain") else ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=table_assignments.csv"})


@app.route("/api/reseed", methods=["POST"])
def api_reseed():
    seed()
    store.clear_draft_result()
    return jsonify({"ok": True})


# ── Menu ──────────────────────────────────────────────────────────────────────
@app.route("/api/menu", methods=["GET"])
def api_menu_get():
    return jsonify(store.get_menu())


@app.route("/api/menu", methods=["POST"])
def api_menu_add():
    store.add_menu_category(request.get_json(force=True))
    return jsonify({"ok": True})


@app.route("/api/menu/<int:cat_id>", methods=["PATCH"])
def api_menu_update(cat_id):
    data = request.get_json(force=True)
    fields = {k: v for k, v in data.items() if k in {"name", "per_table_count", "notes", "sort_order"}}
    if not fields:
        return jsonify({"error": "no fields"}), 400
    store.update_menu_category(cat_id, fields)
    return jsonify({"ok": True})


@app.route("/api/menu/<int:cat_id>", methods=["DELETE"])
def api_menu_delete(cat_id):
    store.delete_menu_category(cat_id)
    return jsonify({"ok": True})


# ── Settings ──────────────────────────────────────────────────────────────────
_SETTING_KEYS = ["event_name", "event_date", "event_time", "event_location",
                 "organizer_name", "organizer_email", "app_base_url"]


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify({k: (store.get_setting(k, "") or "") for k in _SETTING_KEYS})
    data = request.get_json(force=True)
    current = {k: (store.get_setting(k, "") or "") for k in _SETTING_KEYS}
    merged = {k: data.get(k, current[k]) for k in _SETTING_KEYS}
    store.set_all_settings(merged)
    return jsonify({"ok": True})


# ── Token helpers ─────────────────────────────────────────────────────────────
def _signup_url(table_number, req=None):
    base = (store.get_setting("app_base_url", "") or "").rstrip("/")
    token = store.get_or_create_token(table_number)
    path = f"/signup/{token}"
    if base:
        return base + path
    if req is not None:
        return req.host_url.rstrip("/") + path
    return path


# ── Signups ───────────────────────────────────────────────────────────────────
@app.route("/api/signup-info/<token>")
def api_signup_info(token):
    table_number = store.get_table_for_token(token)
    if not table_number:
        return jsonify({"error": "invalid token"}), 404
    is_locked = store.get_setting("is_locked") == "1"
    captain = None
    attendees = []
    if is_locked:
        asgn = store.get_assignments()
        all_p = store.get_participants()
        people = [p for p in all_p if asgn.get(p["id"]) == table_number]
        captain = next((p for p in people if p.get("is_captain")), None)
        attendees = [f"{p['first_name']} {p['last_name']}" for p in people]
    else:
        result = _get_unlocked_result(_participants())
        t = next((t for t in result["tables"] if t["number"] == table_number), None)
        if t:
            captain = t["captain"]
            attendees = [f"{p['first_name']} {p['last_name']}" for p in t["people"]]
    cats, sigs = store.get_signup_info(table_number)
    return jsonify({
        "event_name": store.get_setting("event_name", "The Longest Table"),
        "event_date": store.get_setting("event_date", ""),
        "event_time": store.get_setting("event_time", ""),
        "event_location": store.get_setting("event_location", ""),
        "table_number": table_number,
        "captain_name": (f"{captain['first_name']} {captain['last_name']}" if captain else ""),
        "attendees": attendees,
        "categories": cats,
        "signups": sigs,
    })


@app.route("/api/signups", methods=["POST"])
def api_signups_add():
    data = request.get_json(force=True)
    table_number = store.get_table_for_token(data.get("token", ""))
    if not table_number:
        return jsonify({"error": "invalid token"}), 404
    category_id = int(data.get("category_id", 0))
    person_name = (data.get("person_name", "") or "").strip()
    item_description = (data.get("item_description", "") or "").strip()
    if not (category_id and person_name):
        return jsonify({"error": "missing fields"}), 400
    err = store.add_signup(table_number, category_id, person_name, item_description)
    if err == "category not found":
        return jsonify({"error": err}), 404
    if err:
        return jsonify({"error": err}), 409
    return jsonify({"ok": True})


@app.route("/api/signups/<int:signup_id>", methods=["DELETE"])
def api_signups_delete(signup_id):
    store.delete_signup(signup_id)
    return jsonify({"ok": True})


@app.route("/api/signup-progress")
def api_signup_progress():
    _unused_total, filled_map, tokens_map = store.get_signup_progress()

    is_locked = store.get_setting("is_locked") == "1"
    totals_by_table = {}
    if is_locked:
        for tn in store.get_assignments().values():
            totals_by_table[tn] = totals_by_table.get(tn, 0) + 1
    else:
        result = _get_unlocked_result(_participants())
        for t in result["tables"]:
            totals_by_table[t["number"]] = len(t["people"])

    return jsonify({
        "totals_by_table": {str(k): v for k, v in totals_by_table.items()},
        "filled_by_table": {str(k): v for k, v in filled_map.items()},
        "tokens_by_table": {str(k): v for k, v in tokens_map.items()},
    })


@app.route("/api/ensure-tokens", methods=["POST"])
def api_ensure_tokens():
    is_locked = store.get_setting("is_locked") == "1"
    if is_locked:
        table_numbers = set(store.get_assignments().values())
    else:
        result = _get_unlocked_result(_participants())
        table_numbers = {t["number"] for t in result["tables"]}
    return jsonify({tn: store.get_or_create_token(tn) for tn in table_numbers})


@app.route("/api/admin/food-overview")
def api_admin_food_overview():
    is_locked = store.get_setting("is_locked") == "1"
    if is_locked:
        tables = _result_from_assignment_map(_participants(), store.get_assignments(), is_locked=True)["tables"]
    else:
        tables = _get_unlocked_result(_participants())["tables"]

    menu = store.get_menu()
    table_rows = []
    total_missing = 0
    total_claimed = 0
    total_target = 0

    for t in tables:
        _, signups = store.get_signup_info(t["number"])
        by_category = {}
        for s in signups:
            by_category.setdefault(s.get("category_id"), []).append(s)

        categories = []
        missing_items = 0
        for cat in menu:
            claims = by_category.get(cat["id"], [])
            recommended = int(cat.get("per_table_count") or 0)
            claimed = len(claims)
            remaining = max(recommended - claimed, 0)
            missing_items += remaining
            categories.append({
                "id": cat["id"],
                "name": cat.get("name", ""),
                "notes": cat.get("notes", ""),
                "recommended": recommended,
                "claimed": claimed,
                "remaining": remaining,
                "claims": [
                    {
                        "person_name": c.get("person_name", ""),
                        "item_description": c.get("item_description", ""),
                    }
                    for c in claims
                ],
            })

        captain = t.get("captain")
        people_count = len(t.get("people", []))
        claimed_total = len(signups)
        completion_pct = round((claimed_total / people_count) * 100) if people_count > 0 else 0

        table_rows.append({
            "table_number": t["number"],
            "captain_name": (
                f"{captain['first_name']} {captain['last_name']}" if captain else ""
            ),
            "people_count": people_count,
            "claimed_total": claimed_total,
            "target_total": people_count,
            "completion_pct": completion_pct,
            "missing_items": missing_items,
            "signup_link": _signup_url(t["number"], request),
            "categories": categories,
        })

        total_missing += missing_items
        total_claimed += claimed_total
        total_target += people_count

    return jsonify({
        "summary": {
            "table_count": len(table_rows),
            "total_claimed": total_claimed,
            "total_target": total_target,
            "total_missing_items": total_missing,
            "is_locked": is_locked,
        },
        "tables": table_rows,
    })


# ── Email drafts ──────────────────────────────────────────────────────────────
@app.route("/api/email-templates", methods=["GET"])
def api_email_templates_get():
    mode = (request.args.get("mode") or "").strip().lower() or None
    if mode and mode not in {"invite", "reminder", "dayof"}:
        return jsonify({"error": "invalid mode"}), 400
    return jsonify(store.get_email_templates(mode))


@app.route("/api/email-templates/<mode>/<int:tid>", methods=["PUT"])
def api_email_templates_update(mode, tid):
    if mode not in {"invite", "reminder", "dayof"}:
        return jsonify({"error": "invalid mode"}), 400
    if tid not in (1, 2):
        return jsonify({"error": "invalid template id"}), 400
    data = request.get_json(force=True)
    store.update_email_template(mode, tid, data.get("subject", ""), data.get("body", ""))
    return jsonify({"ok": True})


def _render_tpl(text, ctx):
    for k, v in ctx.items():
        text = text.replace("{" + k + "}", str(v))
    return text


@app.route("/api/email-drafts")
def api_email_drafts():
    mode = (request.args.get("mode") or "invite").strip().lower()
    if mode not in {"invite", "reminder", "dayof"}:
        mode = "invite"

    is_locked = store.get_setting("is_locked") == "1"
    if is_locked:
        tables = _result_from_assignment_map(_participants(), store.get_assignments(), is_locked=True)["tables"]
    else:
        tables = _get_or_create_unlocked_draft(_participants())["tables"]

    templates = {t["id"]: t for t in store.get_email_templates(mode)}
    # Fall back to invite templates if a per-mode template is missing.
    invite_templates = {t["id"]: t for t in store.get_email_templates("invite")}
    for tid in (1, 2):
        templates.setdefault(tid, invite_templates.get(tid, {"subject": "", "body": ""}))
    menu = store.get_menu()
    event_name = store.get_setting("event_name", "The Longest Table")
    event_date = store.get_setting("event_date", "")
    event_time = store.get_setting("event_time", "")
    event_location = store.get_setting("event_location", "")
    organizer_name = store.get_setting("organizer_name", "") or "The Event Team"
    organizer_email = store.get_setting("organizer_email", "")
    menu_summary = "\n".join(
        f"  - {m['name']}: {m['per_table_count']} needed"
        + (f" ({m['notes']})" if m.get("notes") else "")
        for m in menu
    ) or "  (menu not configured yet)"

    drafts = []
    for t in tables:
        cap = t.get("captain")
        _, table_signups = store.get_signup_info(t["number"])
        menu_by_id = {m["id"]: m for m in menu}
        claimed_food_lines = []
        for s in table_signups:
            cat = menu_by_id.get(s.get("category_id"), {})
            cat_name = cat.get("name", "Other")
            who = s.get("person_name", "Someone")
            item = (s.get("item_description") or "").strip()
            line = f"  - {cat_name}: {who}"
            if item:
                line += f" - {item}"
            claimed_food_lines.append(line)
        claimed_food_list = "\n".join(claimed_food_lines) if claimed_food_lines else "  (no items claimed yet)"

        attendees_text = "\n".join(
            f"  - {p['first_name']} {p['last_name']}"
            + (f" <{p['email']}>" if p.get("email") else "")
            for p in t["people"]
        )
        captain_email = cap.get("email", "") if cap else ""
        captain_email_key = captain_email.strip().lower()
        guest_email_map = {}
        for p in t["people"]:
            email = (p.get("email") or "").strip()
            if not email:
                continue
            email_key = email.lower()
            if email_key == captain_email_key:
                continue
            guest_email_map.setdefault(email_key, email)
        guest_emails = sorted(guest_email_map.values(), key=lambda value: value.lower())
        guest_email_lines = "\n".join(f"  - {email}" for email in guest_emails)
        guest_email_csv = ", ".join(guest_emails)
        signup_link = _signup_url(t["number"], request)
        ctx = {
            "event_name": event_name, "event_date": event_date,
            "event_time": event_time, "event_location": event_location,
            "organizer_name": organizer_name, "table_number": t["number"],
            "captain_first": cap["first_name"] if cap else "(no captain)",
            "captain_last": cap["last_name"] if cap else "",
            "attendee_list": attendees_text, "signup_link": signup_link,
            "menu_summary": menu_summary,
            "guest_email_list": guest_email_lines or "  (no guest emails on file)",
            "guest_email_csv": guest_email_csv,
            "claimed_food_list": claimed_food_list,
        }
        tpl1 = templates.get(1, {"subject": "", "body": ""})
        tpl2 = templates.get(2, {"subject": "", "body": ""})
        captain_subject = _render_tpl(tpl2["subject"], ctx)
        captain_body = _render_tpl(tpl2["body"], ctx)
        if "{guest_email_list}" not in tpl2["body"] and "{guest_email_csv}" not in tpl2["body"]:
            captain_body = (
                f"{captain_body.rstrip()}\n\n"
                "Guest email addresses for this table (copy/paste into To or Bcc):\n"
                f"{ctx['guest_email_list']}"
            )

        drafts.append({
            "campaign_mode": mode,
            "table_number": t["number"],
            "is_singles_table": t.get("is_singles_table", False),
            "captain_name": (f"{cap['first_name']} {cap['last_name']}" if cap else ""),
            "captain_email": captain_email,
            "guest_emails": guest_emails,
            "guest_email_csv": guest_email_csv,
            "claimed_food_list": claimed_food_list,
            "signup_link": signup_link,
            "organizer_to_captain": {
                "subject": _render_tpl(tpl1["subject"], ctx),
                "body": _render_tpl(tpl1["body"], ctx),
                "to": captain_email, "from": organizer_email,
            },
            "captain_to_guests": {
                "subject": captain_subject,
                "body": captain_body,
                "to": captain_email, "from": captain_email,
            },
        })
    return jsonify(drafts)


# ── Startup ───────────────────────────────────────────────────────────────────
def ensure_seeded():
    if store.USE_FIRESTORE:
        docs = list(store._col("participants").limit(1).stream())
        if not docs:
            tsv = os.path.join(os.path.dirname(__file__), "data", "signups.tsv")
            if os.path.exists(tsv):
                seed()
        return
    init_db()
    from models import get_db
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) AS c FROM participants").fetchone()
    conn.close()
    if row["c"] == 0:
        tsv = os.path.join(os.path.dirname(__file__), "data", "signups.tsv")
        if os.path.exists(tsv):
            seed()


ensure_seeded()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5055)), debug=True)
