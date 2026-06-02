"""
store.py — Unified data layer.

Locally: uses SQLite (models.py / get_db()).
On Cloud Run (K_SERVICE env var set) or if USE_FIRESTORE=1:
  uses Google Cloud Firestore.

All app.py routes call functions in this module rather than
touching sqlite directly.
"""
import os
import json
import secrets as _secrets

# ── Determine which backend to use ──────────────────────────────────────────
# USE_FIRESTORE=1 forces Firestore. USE_FIRESTORE=0 forces SQLite even on Cloud Run.
# If unset, auto-enable on Cloud Run (K_SERVICE).
_use_fs_env = os.environ.get("USE_FIRESTORE")
if _use_fs_env is not None:
    USE_FIRESTORE = _use_fs_env.strip() == "1"
else:
    USE_FIRESTORE = bool(os.environ.get("K_SERVICE"))

if USE_FIRESTORE:
    from google.cloud import firestore as _fs_lib
    _db = None

    def _fs():
        global _db
        if _db is None:
            _db = _fs_lib.Client()
        return _db

    def _col(name):
        return _fs().collection(name)

else:
    from models import get_db as _get_db

# ── Participants ─────────────────────────────────────────────────────────────

def get_participants(ordered=True):
    if USE_FIRESTORE:
        docs = _col("participants").stream()
        rows = []
        for d in docs:
            r = d.to_dict()
            r["id"] = int(d.id)
            rows.append(r)
        # Assignments (locked table numbers)
        asgn = {int(d.id): d.to_dict() for d in _col("assignments").stream()}
        for r in rows:
            r["table_number"] = asgn.get(r["id"], {}).get("table_number")
        if ordered:
            rows.sort(key=lambda x: (
                x.get("order_id") or "",
                -(x.get("is_captain") or 0),
                x.get("last_name") or "",
                x.get("first_name") or "",
            ))
        return rows
    else:
        is_locked = get_setting("is_locked") == "1"
        conn = _get_db()
        if is_locked:
            rows = conn.execute(
                """SELECT p.*, a.table_number
                   FROM participants p
                   LEFT JOIN assignments a ON p.id = a.participant_id
                   ORDER BY a.table_number, p.order_id,
                            p.is_captain DESC, p.last_name, p.first_name"""
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT *, NULL as table_number FROM participants "
                "ORDER BY order_id, is_captain DESC, last_name, first_name"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def update_participant(pid, fields):
    if USE_FIRESTORE:
        # Handle assignment table_number separately
        if "table_number" in fields:
            tn = fields.pop("table_number")
            ref = _col("assignments").document(str(pid))
            if tn:
                ref.set({"table_number": int(tn), "is_locked": 1})
            else:
                ref.delete()
        if "is_captain" in fields:
            fields["is_captain"] = 1 if fields["is_captain"] else 0
        if fields:
            _col("participants").document(str(pid)).update(fields)
    else:
        from app import update_participant as _up  # handled inline in app.py
        raise NotImplementedError("call update_participant directly via get_db()")


def delete_participant(pid):
    if USE_FIRESTORE:
        _col("participants").document(str(pid)).delete()
        _col("assignments").document(str(pid)).delete()
    else:
        conn = _get_db()
        conn.execute("DELETE FROM participants WHERE id = ?", (pid,))
        conn.commit()
        conn.close()


def add_participant(data):
    if USE_FIRESTORE:
        # Get next id
        docs = list(_col("participants").stream())
        next_id = max((int(d.id) for d in docs), default=0) + 1
        data["is_captain"] = 1 if data.get("is_captain") else 0
        _col("participants").document(str(next_id)).set(data)
        return next_id
    else:
        conn = _get_db()
        cur = conn.execute(
            """INSERT INTO participants
               (order_id, first_name, last_name, email, buyer_first, buyer_last,
                buyer_email, phone, age_range, is_captain, accommodations)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("order_id", ""), data.get("first_name", ""),
             data.get("last_name", ""), data.get("email", ""),
             data.get("buyer_first", ""), data.get("buyer_last", ""),
             data.get("buyer_email", ""), data.get("phone", ""),
             data.get("age_range", "Adult"),
             1 if data.get("is_captain") else 0,
             data.get("accommodations", "No"))
        )
        conn.commit()
        conn.close()
        return cur.lastrowid

# ── Rules ────────────────────────────────────────────────────────────────────

from models import DEFAULT_RULES


def get_rules():
    if USE_FIRESTORE:
        doc = _col("configs").document("rules").get()
        if not doc.exists:
            return dict(DEFAULT_RULES)
        cfg = doc.to_dict() or {}
        for k, v in DEFAULT_RULES.items():
            cfg.setdefault(k, v)
        return cfg
    else:
        from models import get_rules as _gr
        return _gr()


def set_rules(cfg):
    if USE_FIRESTORE:
        _col("configs").document("rules").set(cfg)
    else:
        from models import set_rules as _sr
        _sr(cfg)

# ── Settings ─────────────────────────────────────────────────────────────────

from models import DEFAULT_SETTINGS


# Tiny in-process TTL cache for the merged settings dict. Writes invalidate.
# Across multiple Cloud Run instances, staleness is bounded by the TTL.
_SETTINGS_TTL_SECONDS = 60
_settings_cache = {"ts": 0.0, "data": None}


def _invalidate_settings_cache():
    _settings_cache["ts"] = 0.0
    _settings_cache["data"] = None


def _load_settings_dict():
    """Merged settings dict (DEFAULT_SETTINGS + stored), cached for TTL."""
    import time as _t
    now = _t.time()
    if _settings_cache["data"] is not None and (now - _settings_cache["ts"]) < _SETTINGS_TTL_SECONDS:
        return _settings_cache["data"]
    merged = dict(DEFAULT_SETTINGS)
    if USE_FIRESTORE:
        doc = _col("configs").document("settings").get()
        if doc.exists:
            merged.update(doc.to_dict() or {})
    else:
        from models import get_db
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        for r in rows:
            merged[r["key"]] = r["value"]
    _settings_cache["data"] = merged
    _settings_cache["ts"] = now
    return merged


def get_setting(key, default=None):
    val = _load_settings_dict().get(key)
    if val is None or val == "":
        return DEFAULT_SETTINGS.get(key, default if default is not None else "")
    return val


def set_setting(key, value):
    if USE_FIRESTORE:
        _col("configs").document("settings").set({key: str(value)}, merge=True)
    else:
        from models import set_setting as _ss
        _ss(key, value)
    _invalidate_settings_cache()


def get_all_settings():
    """Full merged settings dict — single read per TTL window."""
    return dict(_load_settings_dict())


def set_all_settings(data):
    if USE_FIRESTORE:
        _col("configs").document("settings").set(
            {k: v for k, v in data.items()}, merge=True
        )
    else:
        from models import set_setting as _ss
        for k, v in data.items():
            _ss(k, v or "")
    _invalidate_settings_cache()


def get_draft_result():
    raw = get_setting("draft_result_json", "") or ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def save_draft_result(result):
    set_setting("draft_result_json", json.dumps(result))


def clear_draft_result():
    set_setting("draft_result_json", "")

# ── Menu categories ──────────────────────────────────────────────────────────

def get_menu():
    if USE_FIRESTORE:
        docs = list(_col("menu_categories").stream())
        rows = []
        for d in docs:
            r = d.to_dict()
            r["id"] = int(d.id)
            rows.append(r)
        return sorted(rows, key=lambda x: (x.get("sort_order", 0), x.get("id", 0)))
    else:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM menu_categories ORDER BY sort_order, id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def add_menu_category(data):
    if USE_FIRESTORE:
        docs = list(_col("menu_categories").stream())
        next_id = max((int(d.id) for d in docs), default=0) + 1
        _col("menu_categories").document(str(next_id)).set({
            "name": data.get("name", "Untitled").strip() or "Untitled",
            "per_table_count": int(data.get("per_table_count", 1) or 1),
            "notes": data.get("notes", ""),
            "sort_order": int(data.get("sort_order", 999) or 999),
        })
    else:
        conn = _get_db()
        conn.execute(
            "INSERT INTO menu_categories (name, per_table_count, notes, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (data.get("name", "Untitled").strip() or "Untitled",
             int(data.get("per_table_count", 1) or 1),
             data.get("notes", ""),
             int(data.get("sort_order", 999) or 999)),
        )
        conn.commit()
        conn.close()


def update_menu_category(cat_id, fields):
    if "per_table_count" in fields:
        fields["per_table_count"] = int(fields["per_table_count"] or 0)
    if "sort_order" in fields:
        fields["sort_order"] = int(fields["sort_order"] or 0)
    if USE_FIRESTORE:
        _col("menu_categories").document(str(cat_id)).update(fields)
    else:
        conn = _get_db()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE menu_categories SET {sets} WHERE id = ?",
                     list(fields.values()) + [cat_id])
        conn.commit()
        conn.close()


def delete_menu_category(cat_id):
    if USE_FIRESTORE:
        _col("menu_categories").document(str(cat_id)).delete()
        # Delete associated signups
        sigs = _col("signups").where("category_id", "==", cat_id).stream()
        for s in sigs:
            s.reference.delete()
    else:
        conn = _get_db()
        conn.execute("DELETE FROM menu_categories WHERE id = ?", (cat_id,))
        conn.execute("DELETE FROM signups WHERE category_id = ?", (cat_id,))
        conn.commit()
        conn.close()

# ── Signups ──────────────────────────────────────────────────────────────────

def get_signup_info(table_number):
    """Returns categories + signups for a table."""
    cats = get_menu()
    if USE_FIRESTORE:
        docs = _col("signups").where("table_number", "==", table_number).stream()
        sigs = []
        for d in docs:
            r = d.to_dict()
            r["id"] = int(d.id)
            sigs.append(r)
    else:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM signups WHERE table_number = ? ORDER BY created_at",
            (table_number,),
        ).fetchall()
        conn.close()
        sigs = [dict(r) for r in rows]
    return cats, sigs


def add_signup(table_number, category_id, person_name, item_description):
    # Validate category id
    cats = {c["id"]: c for c in get_menu()}
    cat = cats.get(category_id)
    if not cat:
        return "category not found"
    if USE_FIRESTORE:
        docs = list(_col("signups").stream())
        next_id = max((int(d.id) for d in docs), default=0) + 1
        import datetime
        _col("signups").document(str(next_id)).set({
            "table_number": table_number,
            "category_id": category_id,
            "person_name": person_name,
            "item_description": item_description,
            "created_at": datetime.datetime.utcnow().isoformat(),
        })
    else:
        conn = _get_db()
        conn.execute(
            "INSERT INTO signups (table_number, category_id, person_name, item_description) "
            "VALUES (?, ?, ?, ?)",
            (table_number, category_id, person_name, item_description),
        )
        conn.commit()
        conn.close()
    return None  # no error


def delete_signup(signup_id):
    if USE_FIRESTORE:
        _col("signups").document(str(signup_id)).delete()
    else:
        conn = _get_db()
        conn.execute("DELETE FROM signups WHERE id = ?", (signup_id,))
        conn.commit()
        conn.close()


def get_signup_progress():
    cats = get_menu()
    total = sum(c["per_table_count"] for c in cats)
    if USE_FIRESTORE:
        docs = _col("signups").stream()
        filled_map = {}
        tokens_map = {}
        for d in docs:
            r = d.to_dict()
            tn = r.get("table_number")
            filled_map[tn] = filled_map.get(tn, 0) + 1
        for d in _col("table_tokens").stream():
            r = d.to_dict()
            tokens_map[int(d.id)] = r.get("token")
    else:
        conn = _get_db()
        rows = conn.execute(
            "SELECT table_number, COUNT(*) AS c FROM signups GROUP BY table_number"
        ).fetchall()
        filled_map = {r["table_number"]: r["c"] for r in rows}
        rows = conn.execute("SELECT table_number, token FROM table_tokens").fetchall()
        tokens_map = {r["table_number"]: r["token"] for r in rows}
        conn.close()
    return total, filled_map, tokens_map

# ── Table tokens ─────────────────────────────────────────────────────────────

def get_or_create_token(table_number):
    if USE_FIRESTORE:
        doc = _col("table_tokens").document(str(table_number)).get()
        if doc.exists:
            return doc.to_dict()["token"]
        token = _secrets.token_urlsafe(10)
        _col("table_tokens").document(str(table_number)).set({"token": token})
        return token
    else:
        conn = _get_db()
        row = conn.execute(
            "SELECT token FROM table_tokens WHERE table_number = ?", (table_number,)
        ).fetchone()
        if row:
            conn.close()
            return row["token"]
        token = _secrets.token_urlsafe(10)
        conn.execute(
            "INSERT INTO table_tokens (table_number, token) VALUES (?, ?)",
            (table_number, token),
        )
        conn.commit()
        conn.close()
        return token


def get_table_for_token(token):
    if USE_FIRESTORE:
        docs = list(_col("table_tokens").where("token", "==", token).stream())
        if not docs:
            return None
        return int(docs[0].id)
    else:
        conn = _get_db()
        row = conn.execute(
            "SELECT table_number FROM table_tokens WHERE token = ?", (token,)
        ).fetchone()
        conn.close()
        return row["table_number"] if row else None

# ── Email templates ──────────────────────────────────────────────────────────

EMAIL_MODES = ("invite", "reminder", "dayof")


def _doc_id(mode, tid):
    return f"{mode}_{int(tid)}"


def _ensure_firestore_email_templates_seeded():
    """Seed Firestore email_templates with per-mode defaults + migrate legacy docs."""
    from models import DEFAULT_EMAIL_TEMPLATES
    coll = _col("email_templates")
    existing = {d.id: d.to_dict() for d in coll.stream()}
    # Migrate legacy docs ("1" / "2") → ("invite_1" / "invite_2")
    for legacy_id in ("1", "2"):
        if legacy_id in existing and _doc_id("invite", legacy_id) not in existing:
            data = existing[legacy_id]
            coll.document(_doc_id("invite", legacy_id)).set({
                "mode": "invite",
                "tid": int(legacy_id),
                "subject": data.get("subject", ""),
                "body": data.get("body", ""),
            })
            coll.document(legacy_id).delete()
            existing.pop(legacy_id, None)
            existing[_doc_id("invite", legacy_id)] = data
    # Seed any missing per-mode defaults.
    for mode, tpls in DEFAULT_EMAIL_TEMPLATES.items():
        for tid, tpl in tpls.items():
            doc_id = _doc_id(mode, tid)
            if doc_id not in existing:
                coll.document(doc_id).set({
                    "mode": mode,
                    "tid": int(tid),
                    "subject": tpl["subject"],
                    "body": tpl["body"],
                })


def get_email_templates(mode=None):
    """Return list of email templates. If mode is given, filter to that mode."""
    if USE_FIRESTORE:
        _ensure_firestore_email_templates_seeded()
        result = []
        for d in _col("email_templates").stream():
            r = d.to_dict() or {}
            r_mode = r.get("mode", "invite")
            tid = int(r.get("tid", r.get("id", 0)) or 0)
            if not tid:
                continue
            if mode and r_mode != mode:
                continue
            result.append({
                "mode": r_mode,
                "id": tid,
                "subject": r.get("subject", ""),
                "body": r.get("body", ""),
            })
        result.sort(key=lambda x: (EMAIL_MODES.index(x["mode"]) if x["mode"] in EMAIL_MODES else 99, x["id"]))
        return result
    else:
        conn = _get_db()
        if mode:
            rows = conn.execute(
                "SELECT mode, id, subject, body FROM email_templates "
                "WHERE mode = ? ORDER BY id", (mode,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT mode, id, subject, body FROM email_templates ORDER BY mode, id"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def update_email_template(mode, tid, subject, body):
    if mode not in EMAIL_MODES:
        raise ValueError(f"invalid mode: {mode}")
    if int(tid) not in (1, 2):
        raise ValueError(f"invalid template id: {tid}")
    if USE_FIRESTORE:
        _col("email_templates").document(_doc_id(mode, tid)).set({
            "mode": mode,
            "tid": int(tid),
            "subject": subject,
            "body": body,
        }, merge=True)
    else:
        conn = _get_db()
        conn.execute(
            "INSERT INTO email_templates (mode, id, subject, body) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(mode, id) DO UPDATE SET subject = excluded.subject, body = excluded.body",
            (mode, int(tid), subject, body),
        )
        conn.commit()
        conn.close()

# ── Assignments ──────────────────────────────────────────────────────────────

def save_assignments(tables):
    """Persist generated table assignments (called when locking)."""
    if USE_FIRESTORE:
        batch = _fs().batch()
        # Clear existing
        for d in _col("assignments").stream():
            batch.delete(d.reference)
        batch.commit()
        # Write new
        batch2 = _fs().batch()
        for t in tables:
            for p in t["people"]:
                ref = _col("assignments").document(str(p["id"]))
                batch2.set(ref, {
                    "table_number": t["number"],
                    "is_locked": 1,
                })
        batch2.commit()
    else:
        conn = _get_db()
        conn.execute("DELETE FROM assignments")
        for t in tables:
            for p in t["people"]:
                conn.execute(
                    "INSERT INTO assignments (participant_id, table_number, is_locked) "
                    "VALUES (?, ?, 1)",
                    (p["id"], t["number"])
                )
        conn.commit()
        conn.close()


def get_assignments():
    """Returns {participant_id: table_number}."""
    if USE_FIRESTORE:
        return {int(d.id): d.to_dict()["table_number"]
                for d in _col("assignments").stream()}
    else:
        conn = _get_db()
        rows = conn.execute("SELECT participant_id, table_number FROM assignments").fetchall()
        conn.close()
        return {r["participant_id"]: r["table_number"] for r in rows}


def upsert_assignment(participant_id, table_number):
    if USE_FIRESTORE:
        if table_number:
            _col("assignments").document(str(participant_id)).set(
                {"table_number": int(table_number), "is_locked": 1}
            )
        else:
            _col("assignments").document(str(participant_id)).delete()
    else:
        conn = _get_db()
        if table_number:
            conn.execute(
                "INSERT INTO assignments (participant_id, table_number, is_locked) "
                "VALUES (?, ?, 1) ON CONFLICT(participant_id) DO UPDATE SET table_number = excluded.table_number",
                (participant_id, int(table_number))
            )
        else:
            conn.execute("DELETE FROM assignments WHERE participant_id = ?", (participant_id,))
        conn.commit()
        conn.close()


# ════════════════════════════════════════════════════════════════════════════
# Phase 1 Sprint A — dual-backend orders / donations / photos / tokens / email_log
# ════════════════════════════════════════════════════════════════════════════

import datetime as _dt
import string as _string


_CODE_ALPHABET = _string.ascii_uppercase + _string.digits
_CODE_AMBIG = set("O0I1")


def _now_iso():
    return _dt.datetime.utcnow().isoformat(timespec="seconds")


def _next_int_id(collection: str) -> int:
    """Get next sequential numeric ID for a Firestore collection."""
    docs = list(_col(collection).stream())
    return max((int(d.id) for d in docs if d.id.isdigit()), default=0) + 1


# ── Orders ──────────────────────────────────────────────────────────────────

def new_order_code() -> str:
    while True:
        code = "".join(_secrets.choice(_CODE_ALPHABET) for _ in range(6))
        if any(c in _CODE_AMBIG for c in code):
            continue
        if USE_FIRESTORE:
            if not _col("orders").document(code).get().exists:
                return code
        else:
            conn = _get_db()
            row = conn.execute("SELECT 1 FROM orders WHERE order_code = ?", (code,)).fetchone()
            conn.close()
            if not row:
                return code


def create_order(*, buyer_first, buyer_last, buyer_email, buyer_phone,
                 attendees, pledged_donation_cents=0, is_captain_order=False,
                 notes="", status="confirmed"):
    """Create order + participants. Returns order_code."""
    code = new_order_code()
    now = _now_iso()
    order_doc = {
        "order_code": code,
        "buyer_first": buyer_first, "buyer_last": buyer_last,
        "buyer_email": buyer_email, "buyer_phone": buyer_phone or "",
        "party_size": len(attendees),
        "pledged_donation_cents": int(pledged_donation_cents or 0),
        "paid_donation_cents": 0,
        "donation_method": "",
        "status": status or "confirmed",
        "is_captain_order": 1 if is_captain_order else 0,
        "notes": notes or "",
        "created_at": now, "updated_at": now,
    }

    if USE_FIRESTORE:
        _col("orders").document(code).set(order_doc)
        for a in attendees:
            pid = _next_int_id("participants")
            _col("participants").document(str(pid)).set({
                "order_id": code,
                "first_name": (a.get("first_name") or "").strip(),
                "last_name": (a.get("last_name") or "").strip(),
                "email": (a.get("email") or "").strip(),
                "buyer_first": buyer_first, "buyer_last": buyer_last,
                "buyer_email": buyer_email,
                "phone": (a.get("phone") or buyer_phone or "").strip(),
                "age_range": (a.get("age_range") or "Adult").strip(),
                "is_captain": 1 if a.get("is_captain") else 0,
                "accommodations": (a.get("accommodations") or "").strip(),
            })
    else:
        conn = _get_db()
        conn.execute(
            """INSERT INTO orders (order_code, buyer_first, buyer_last, buyer_email,
                buyer_phone, party_size, pledged_donation_cents, is_captain_order, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, buyer_first, buyer_last, buyer_email, buyer_phone or "",
             len(attendees), int(pledged_donation_cents or 0),
             1 if is_captain_order else 0, notes or ""),
        )
        for a in attendees:
            conn.execute(
                """INSERT INTO participants (order_id, first_name, last_name, email,
                    buyer_first, buyer_last, buyer_email, phone, age_range,
                    is_captain, accommodations)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (code, a.get("first_name", "").strip(), a.get("last_name", "").strip(),
                 a.get("email", "").strip(), buyer_first, buyer_last, buyer_email,
                 (a.get("phone") or buyer_phone or "").strip(),
                 a.get("age_range", "Adult").strip(),
                 1 if a.get("is_captain") else 0,
                 a.get("accommodations", "").strip()),
            )
        # status defaults to 'confirmed' in schema; override only if needed
        if status and status != "confirmed":
            conn.execute("UPDATE orders SET status = ? WHERE order_code = ?", (status, code))
        conn.commit()
        conn.close()
    return code


def get_order_full(order_code: str):
    if USE_FIRESTORE:
        doc = _col("orders").document(order_code).get()
        if not doc.exists:
            return None
        order = doc.to_dict()
        attendees = [
            {**d.to_dict(), "id": int(d.id)}
            for d in _col("participants").where("order_id", "==", order_code).stream()
        ]
        donations = [
            {**d.to_dict(), "id": int(d.id) if d.id.isdigit() else d.id}
            for d in _col("donations").where("order_code", "==", order_code).stream()
        ]
    else:
        conn = _get_db()
        row = conn.execute("SELECT * FROM orders WHERE order_code = ?", (order_code,)).fetchone()
        if not row:
            conn.close()
            return None
        order = dict(row)
        attendees = [dict(r) for r in conn.execute(
            "SELECT * FROM participants WHERE order_id = ? ORDER BY is_captain DESC, last_name, first_name",
            (order_code,)).fetchall()]
        donations = [dict(r) for r in conn.execute(
            "SELECT * FROM donations WHERE order_code = ? ORDER BY received_at DESC",
            (order_code,)).fetchall()]
        conn.close()
    attendees.sort(key=lambda a: (-(a.get("is_captain") or 0),
                                  (a.get("last_name") or "").lower(),
                                  (a.get("first_name") or "").lower()))
    donations.sort(key=lambda d: d.get("received_at") or "", reverse=True)
    return {
        "order": order, "attendees": attendees, "donations": donations,
        "paid_total_cents": sum(int(d.get("amount_cents") or 0) for d in donations),
    }


def list_orders(status: str = "", search: str = ""):
    if USE_FIRESTORE:
        docs = [d.to_dict() for d in _col("orders").stream()]
    else:
        conn = _get_db()
        docs = [dict(r) for r in conn.execute("SELECT * FROM orders").fetchall()]
        conn.close()
    if status:
        docs = [d for d in docs if d.get("status") == status]
    if search:
        s = search.strip().lower()
        docs = [d for d in docs if s in (d.get("buyer_email") or "").lower()
                or s in (d.get("buyer_last") or "").lower()
                or s in (d.get("buyer_first") or "").lower()
                or s in (d.get("order_code") or "").lower()]
    docs.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return docs


def update_order(order_code: str, fields: dict):
    allowed = {"buyer_first", "buyer_last", "buyer_email", "buyer_phone",
               "status", "notes", "pledged_donation_cents", "donation_method",
               "paid_donation_cents", "party_size"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    fields["updated_at"] = _now_iso()
    if USE_FIRESTORE:
        _col("orders").document(order_code).update(fields)
    else:
        conn = _get_db()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE orders SET {sets} WHERE order_code = ?",
                     list(fields.values()) + [order_code])
        conn.commit()
        conn.close()
    return True


def cancel_order(order_code: str):
    if USE_FIRESTORE:
        _col("orders").document(order_code).update(
            {"status": "cancelled", "updated_at": _now_iso()})
        for d in _col("participants").where("order_id", "==", order_code).stream():
            d.reference.delete()
    else:
        conn = _get_db()
        conn.execute("UPDATE orders SET status='cancelled', updated_at=datetime('now') WHERE order_code = ?",
                     (order_code,))
        conn.execute("DELETE FROM participants WHERE order_id = ?", (order_code,))
        conn.commit()
        conn.close()


def remove_attendee(order_code: str, participant_id: int):
    if USE_FIRESTORE:
        _col("participants").document(str(participant_id)).delete()
        remaining = list(_col("participants").where("order_id", "==", order_code).stream())
        n = len(remaining)
        upd = {"party_size": n, "updated_at": _now_iso()}
        if n == 0:
            upd["status"] = "cancelled"
        _col("orders").document(order_code).update(upd)
    else:
        conn = _get_db()
        conn.execute("DELETE FROM participants WHERE id = ? AND order_id = ?",
                     (participant_id, order_code))
        n = conn.execute("SELECT COUNT(*) AS c FROM participants WHERE order_id = ?",
                         (order_code,)).fetchone()["c"]
        conn.execute("UPDATE orders SET party_size = ?, updated_at = datetime('now') WHERE order_code = ?",
                     (n, order_code))
        if n == 0:
            conn.execute("UPDATE orders SET status='cancelled' WHERE order_code = ?",
                         (order_code,))
        conn.commit()
        conn.close()


# ── Donations ───────────────────────────────────────────────────────────────

def record_donation(*, order_code, amount_cents, source,
                    transaction_id="", donor_name="", donor_email="", note=""):
    now = _now_iso()
    doc = {
        "order_code": order_code or "",
        "amount_cents": int(amount_cents),
        "source": source,
        "transaction_id": transaction_id or "",
        "donor_name": donor_name or "",
        "donor_email": donor_email or "",
        "note": note or "",
        "received_at": now, "matched_at": now,
    }
    if USE_FIRESTORE:
        did = _next_int_id("donations")
        _col("donations").document(str(did)).set(doc)
        if order_code:
            total = sum(int((d.to_dict() or {}).get("amount_cents") or 0)
                        for d in _col("donations").where("order_code", "==", order_code).stream())
            _col("orders").document(order_code).update({
                "paid_donation_cents": total, "donation_method": source,
                "updated_at": now,
            })
    else:
        conn = _get_db()
        conn.execute(
            """INSERT INTO donations (order_code, amount_cents, source, transaction_id,
               donor_name, donor_email, note, matched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (order_code or "", int(amount_cents), source, transaction_id or "",
             donor_name or "", donor_email or "", note or ""),
        )
        if order_code:
            total = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) AS t FROM donations WHERE order_code = ?",
                (order_code,)).fetchone()["t"]
            conn.execute(
                "UPDATE orders SET paid_donation_cents = ?, donation_method = ?, "
                "updated_at = datetime('now') WHERE order_code = ?",
                (total, source, order_code))
        conn.commit()
        conn.close()


def list_donations(limit: int = 500):
    if USE_FIRESTORE:
        rows = []
        for d in _col("donations").stream():
            r = d.to_dict() or {}
            r["id"] = int(d.id) if d.id.isdigit() else d.id
            rows.append(r)
    else:
        conn = _get_db()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM donations ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()]
        conn.close()
    rows.sort(key=lambda d: d.get("received_at") or "", reverse=True)
    return rows[:limit]


def donation_totals():
    if USE_FIRESTORE:
        paid = sum(int((d.to_dict() or {}).get("amount_cents") or 0)
                   for d in _col("donations").stream())
        pledged = sum(int((d.to_dict() or {}).get("pledged_donation_cents") or 0)
                      for d in _col("orders").stream()
                      if (d.to_dict() or {}).get("status") != "cancelled")
    else:
        conn = _get_db()
        paid = conn.execute("SELECT COALESCE(SUM(amount_cents),0) AS t FROM donations").fetchone()["t"]
        pledged = conn.execute(
            "SELECT COALESCE(SUM(pledged_donation_cents),0) AS t FROM orders WHERE status != 'cancelled'"
        ).fetchone()["t"]
        conn.close()
    return {"paid_cents": paid, "pledged_cents": pledged}


def _stats_from(orders_all, people):
    active_codes = {o.get("order_code") for o in orders_all if o.get("status") == "confirmed"}
    ages = {}
    for p in people:
        if p.get("order_id") in active_codes:
            ages[p.get("age_range", "Adult")] = ages.get(p.get("age_range", "Adult"), 0) + 1
    return {
        "orders_total": len(orders_all),
        "attendees_confirmed": sum(int(o.get("party_size") or 0)
                                   for o in orders_all if o.get("status") == "confirmed"),
        "attendees_waitlist": sum(int(o.get("party_size") or 0)
                                  for o in orders_all if o.get("status") == "waitlist"),
        "orders_cancelled": sum(1 for o in orders_all if o.get("status") == "cancelled"),
        "captains": sum(1 for o in orders_all
                        if o.get("is_captain_order") and o.get("status") == "confirmed"),
        "by_age": ages,
    }


def stats_and_totals():
    """Combined stats + donation totals. Streams orders once instead of twice."""
    if USE_FIRESTORE:
        orders_all = [d.to_dict() for d in _col("orders").stream()]
        people = [d.to_dict() for d in _col("participants").stream()]
        paid = sum(int((d.to_dict() or {}).get("amount_cents") or 0)
                   for d in _col("donations").stream())
        pledged = sum(int(o.get("pledged_donation_cents") or 0)
                      for o in orders_all if o.get("status") != "cancelled")
        return _stats_from(orders_all, people), {"paid_cents": paid, "pledged_cents": pledged}
    return dashboard_stats(), donation_totals()


def dashboard_stats():
    if USE_FIRESTORE:
        orders_all = [d.to_dict() for d in _col("orders").stream()]
        people = [d.to_dict() for d in _col("participants").stream()]
        return _stats_from(orders_all, people)
    else:
        conn = _get_db()
        counts = conn.execute(
            """SELECT
                 COUNT(*) AS orders_total,
                 SUM(CASE WHEN status='confirmed' THEN party_size ELSE 0 END) AS attendees_confirmed,
                 SUM(CASE WHEN status='waitlist' THEN party_size ELSE 0 END) AS attendees_waitlist,
                 SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS orders_cancelled,
                 SUM(CASE WHEN is_captain_order=1 AND status='confirmed' THEN 1 ELSE 0 END) AS captains
               FROM orders"""
        ).fetchone()
        ages = conn.execute(
            """SELECT age_range, COUNT(*) AS c FROM participants
               WHERE order_id IN (SELECT order_code FROM orders WHERE status='confirmed')
               GROUP BY age_range"""
        ).fetchall()
        conn.close()
        return {
            "orders_total": counts["orders_total"] or 0,
            "attendees_confirmed": counts["attendees_confirmed"] or 0,
            "attendees_waitlist": counts["attendees_waitlist"] or 0,
            "orders_cancelled": counts["orders_cancelled"] or 0,
            "captains": counts["captains"] or 0,
            "by_age": {r["age_range"]: r["c"] for r in ages},
        }


# ── Photos ──────────────────────────────────────────────────────────────────

def list_photos(collection: str = "", featured_only: bool = False):
    if USE_FIRESTORE:
        rows = [{**d.to_dict(), "id": int(d.id) if d.id.isdigit() else d.id}
                for d in _col("photos").stream()]
    else:
        conn = _get_db()
        rows = [dict(r) for r in conn.execute("SELECT * FROM photos").fetchall()]
        conn.close()
    if collection:
        rows = [r for r in rows if r.get("collection") == collection]
    if featured_only:
        rows = [r for r in rows if r.get("is_featured")]
    rows.sort(key=lambda r: (r.get("sort_order") or 0, -int(r.get("id") or 0) if str(r.get("id","")).isdigit() else 0))
    return rows


def add_photo(*, collection, filename, url, thumb_url="", caption="", alt_text="",
              credit="", year=None, is_featured=False, sort_order=0):
    doc = {
        "collection": collection, "filename": filename, "url": url,
        "thumb_url": thumb_url or url,
        "caption": caption or "", "alt_text": alt_text or "", "credit": credit or "",
        "year": int(year) if year else None,
        "is_featured": 1 if is_featured else 0,
        "sort_order": int(sort_order or 0),
        "created_at": _now_iso(),
    }
    if USE_FIRESTORE:
        pid = _next_int_id("photos")
        _col("photos").document(str(pid)).set(doc)
        return pid
    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO photos (collection, filename, url, thumb_url, caption, alt_text,
            credit, year, is_featured, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (collection, filename, url, thumb_url or url, caption, alt_text, credit,
         year, 1 if is_featured else 0, int(sort_order or 0)))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def delete_photo(photo_id):
    if USE_FIRESTORE:
        _col("photos").document(str(photo_id)).delete()
    else:
        conn = _get_db()
        conn.execute("DELETE FROM photos WHERE id = ?", (int(photo_id),))
        conn.commit()
        conn.close()


def get_photo(photo_id):
    if USE_FIRESTORE:
        doc = _col("photos").document(str(photo_id)).get()
        return doc.to_dict() if doc.exists else None
    conn = _get_db()
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (int(photo_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Email log ───────────────────────────────────────────────────────────────

def log_email(*, to_email, subject, body, template="", status="", error=None):
    doc = {
        "to_email": to_email, "subject": subject, "body": body,
        "template": template, "status": status, "error": error,
        "sent_at": _now_iso(),
    }
    if USE_FIRESTORE:
        eid = _next_int_id("email_log")
        _col("email_log").document(str(eid)).set(doc)
    else:
        conn = _get_db()
        conn.execute(
            """INSERT INTO email_log (to_email, subject, body, template, status, error)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (to_email, subject, body, template, status, error))
        conn.commit()
        conn.close()


# ── Captain helpers ─────────────────────────────────────────────────────────

def find_captain_orders_by_email(email: str):
    """Return all confirmed captain orders whose buyer_email matches (case-insensitive)."""
    if not email:
        return []
    e = email.strip().lower()
    out = []
    for o in list_orders():
        if (o.get("buyer_email") or "").lower() != e:
            continue
        if not o.get("is_captain_order"):
            continue
        if o.get("status") == "cancelled":
            continue
        out.append(o)
    return out


def get_table_roster(table_number: int):
    """Return {"attendees": [...], "orders": [{order_code, buyer_first, buyer_last,
    buyer_email, buyer_phone, people: [...]}]} for everyone at the given table.
    Single-pass: one read for assignments, one for participants, one for orders."""
    table_number = int(table_number)
    assignments = get_assignments()
    pids = {int(pid) for pid, tn in assignments.items() if int(tn) == table_number}
    if not pids:
        return {"attendees": [], "orders": [], "count": 0}

    # Bulk-load participants + orders in one pass each (avoids N+1).
    if USE_FIRESTORE:
        all_people = [{**d.to_dict(), "id": int(d.id)}
                      for d in _col("participants").stream()
                      if d.id.isdigit() and int(d.id) in pids]
        order_codes = {p.get("order_id") for p in all_people if p.get("order_id")}
        orders_by_code = {}
        if order_codes:
            for code in order_codes:
                doc = _col("orders").document(code).get()
                if doc.exists:
                    orders_by_code[code] = doc.to_dict()
    else:
        conn = _get_db()
        placeholders = ",".join("?" * len(pids))
        all_people = [dict(r) for r in conn.execute(
            f"SELECT * FROM participants WHERE id IN ({placeholders})",
            tuple(pids)
        ).fetchall()]
        order_codes = {p.get("order_id") for p in all_people if p.get("order_id")}
        orders_by_code = {}
        if order_codes:
            oph = ",".join("?" * len(order_codes))
            for r in conn.execute(
                f"SELECT * FROM orders WHERE order_code IN ({oph})",
                tuple(order_codes)
            ).fetchall():
                orders_by_code[r["order_code"]] = dict(r)
        conn.close()

    by_order = {}
    for p in all_people:
        code = p.get("order_id")
        o = orders_by_code.get(code)
        if not o or o.get("status") == "cancelled":
            continue
        grp = by_order.setdefault(code, {
            "order_code": code,
            "buyer_first": o.get("buyer_first", ""),
            "buyer_last": o.get("buyer_last", ""),
            "buyer_email": o.get("buyer_email", ""),
            "buyer_phone": o.get("buyer_phone", ""),
            "is_captain_order": bool(o.get("is_captain_order")),
            "people": [],
        })
        grp["people"].append(p)

    flat = []
    for grp in by_order.values():
        grp["people"].sort(key=lambda a: (-(a.get("is_captain") or 0),
                                          (a.get("last_name") or "").lower(),
                                          (a.get("first_name") or "").lower()))
        flat.extend(grp["people"])
    orders = sorted(by_order.values(),
                    key=lambda g: (not g["is_captain_order"],
                                   g["buyer_last"].lower(),
                                   g["buyer_first"].lower()))
    return {"attendees": flat, "orders": orders, "count": len(flat)}


def find_table_for_order(order_code: str):
    """Return the table number for the captain of this order, or None."""
    full = get_order_full(order_code)
    if not full:
        return None
    captain = next((a for a in full["attendees"] if a.get("is_captain")), None)
    if not captain:
        captain = full["attendees"][0] if full["attendees"] else None
    if not captain:
        return None
    assignments = get_assignments()
    return assignments.get(int(captain["id"]))


def confirmed_attendees_count() -> int:
    """Count attendees across all non-cancelled orders (excludes waitlist)."""
    return sum(int(o.get("party_size") or 0)
               for o in list_orders()
               if o.get("status") == "confirmed")


# ── Sponsor prospects (CRM-lite) ─────────────────────────────────────────────

SPONSOR_STAGES = ("prospect", "contacted", "committed", "paid", "declined")

_SPONSOR_FIELDS = (
    "name", "contact_name", "contact_email", "contact_phone",
    "level", "amount_cents", "stage", "assigned_to",
    "last_contact_at", "notes", "url", "logo_url", "on_public_wall",
)


def _coerce_sponsor(fields: dict) -> dict:
    out = {}
    for k in _SPONSOR_FIELDS:
        if k in fields:
            out[k] = fields[k]
    if "amount_cents" in out:
        try:
            out["amount_cents"] = int(out["amount_cents"] or 0)
        except (TypeError, ValueError):
            out["amount_cents"] = 0
    if "on_public_wall" in out:
        out["on_public_wall"] = 1 if out["on_public_wall"] else 0
    if "stage" in out and out["stage"] not in SPONSOR_STAGES:
        out["stage"] = "prospect"
    return out


def list_sponsor_prospects():
    if USE_FIRESTORE:
        rows = []
        for d in _col("sponsor_prospects").stream():
            r = d.to_dict()
            r["id"] = int(d.id) if d.id.isdigit() else d.id
            rows.append(r)
    else:
        conn = _get_db()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM sponsor_prospects ORDER BY "
            "CASE stage WHEN 'paid' THEN 0 WHEN 'committed' THEN 1 "
            "WHEN 'contacted' THEN 2 WHEN 'prospect' THEN 3 ELSE 4 END, "
            "amount_cents DESC, name"
        ).fetchall()]
        conn.close()
    # Stage sort for Firestore
    if USE_FIRESTORE:
        order = {s: i for i, s in enumerate(("paid", "committed", "contacted", "prospect", "declined"))}
        rows.sort(key=lambda r: (order.get(r.get("stage"), 99),
                                  -int(r.get("amount_cents") or 0),
                                  (r.get("name") or "").lower()))
    return rows


def create_sponsor_prospect(fields: dict) -> int:
    data = _coerce_sponsor(fields)
    data.setdefault("name", "Untitled")
    data.setdefault("stage", "prospect")
    data.setdefault("amount_cents", 0)
    data.setdefault("on_public_wall", 0)
    now = _now_iso()
    data["created_at"] = now
    data["updated_at"] = now
    if USE_FIRESTORE:
        sid = _next_int_id("sponsor_prospects")
        _col("sponsor_prospects").document(str(sid)).set(data)
        return sid
    else:
        conn = _get_db()
        cols = ", ".join(data.keys())
        qmarks = ", ".join("?" for _ in data)
        cur = conn.execute(
            f"INSERT INTO sponsor_prospects ({cols}) VALUES ({qmarks})",
            list(data.values()),
        )
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        return sid


def update_sponsor_prospect(sid, fields: dict):
    data = _coerce_sponsor(fields)
    if not data:
        return
    data["updated_at"] = _now_iso()
    if USE_FIRESTORE:
        _col("sponsor_prospects").document(str(sid)).update(data)
    else:
        conn = _get_db()
        sets = ", ".join(f"{k} = ?" for k in data)
        conn.execute(
            f"UPDATE sponsor_prospects SET {sets} WHERE id = ?",
            list(data.values()) + [sid],
        )
        conn.commit()
        conn.close()


def delete_sponsor_prospect(sid):
    if USE_FIRESTORE:
        _col("sponsor_prospects").document(str(sid)).delete()
    else:
        conn = _get_db()
        conn.execute("DELETE FROM sponsor_prospects WHERE id = ?", (sid,))
        conn.commit()
        conn.close()


def get_sponsor_prospect(sid):
    if USE_FIRESTORE:
        doc = _col("sponsor_prospects").document(str(sid)).get()
        if not doc.exists:
            return None
        r = doc.to_dict()
        r["id"] = int(doc.id) if doc.id.isdigit() else doc.id
        return r
    else:
        conn = _get_db()
        row = conn.execute("SELECT * FROM sponsor_prospects WHERE id = ?", (sid,)).fetchone()
        conn.close()
        return dict(row) if row else None


def sponsor_pipeline_summary():
    rows = list_sponsor_prospects()
    out = {s: {"count": 0, "amount_cents": 0} for s in SPONSOR_STAGES}
    for r in rows:
        s = r.get("stage") or "prospect"
        if s not in out:
            out[s] = {"count": 0, "amount_cents": 0}
        out[s]["count"] += 1
        out[s]["amount_cents"] += int(r.get("amount_cents") or 0)
    out["_total"] = {
        "count": sum(v["count"] for v in out.values()),
        "amount_cents": sum(v["amount_cents"] for v in out.values()),
    }
    return out


# ── Donation reassignment / unmatched queue ──────────────────────────────────

def _recalc_order_paid_total(order_code: str):
    if not order_code:
        return 0
    if USE_FIRESTORE:
        total = sum(int((d.to_dict() or {}).get("amount_cents") or 0)
                    for d in _col("donations").where("order_code", "==", order_code).stream())
        _col("orders").document(order_code).update({
            "paid_donation_cents": total, "updated_at": _now_iso(),
        })
        return total
    else:
        conn = _get_db()
        total = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS t FROM donations WHERE order_code = ?",
            (order_code,)).fetchone()["t"]
        conn.execute(
            "UPDATE orders SET paid_donation_cents = ?, updated_at = datetime('now') "
            "WHERE order_code = ?", (total, order_code))
        conn.commit()
        conn.close()
        return total


def reassign_donation(donation_id, new_order_code: str):
    """Detach donation from its old order (if any) and attach to new (or unmatch)."""
    new_code = (new_order_code or "").strip().upper()
    if USE_FIRESTORE:
        ref = _col("donations").document(str(donation_id))
        snap = ref.get()
        if not snap.exists:
            return False
        old_code = (snap.to_dict() or {}).get("order_code") or ""
        ref.update({"order_code": new_code, "matched_at": _now_iso() if new_code else None})
    else:
        conn = _get_db()
        row = conn.execute("SELECT order_code FROM donations WHERE id = ?", (donation_id,)).fetchone()
        if not row:
            conn.close()
            return False
        old_code = row["order_code"] or ""
        if new_code:
            conn.execute(
                "UPDATE donations SET order_code = ?, matched_at = datetime('now') WHERE id = ?",
                (new_code, donation_id))
        else:
            conn.execute(
                "UPDATE donations SET order_code = '', matched_at = NULL WHERE id = ?",
                (donation_id,))
        conn.commit()
        conn.close()
    if old_code and old_code != new_code:
        _recalc_order_paid_total(old_code)
    if new_code:
        _recalc_order_paid_total(new_code)
    return True


def delete_donation(donation_id):
    if USE_FIRESTORE:
        ref = _col("donations").document(str(donation_id))
        snap = ref.get()
        if not snap.exists:
            return False
        old_code = (snap.to_dict() or {}).get("order_code") or ""
        ref.delete()
    else:
        conn = _get_db()
        row = conn.execute("SELECT order_code FROM donations WHERE id = ?", (donation_id,)).fetchone()
        if not row:
            conn.close()
            return False
        old_code = row["order_code"] or ""
        conn.execute("DELETE FROM donations WHERE id = ?", (donation_id,))
        conn.commit()
        conn.close()
    if old_code:
        _recalc_order_paid_total(old_code)
    return True


def list_unmatched_donations():
    rows = list_donations(limit=2000)
    return [d for d in rows if not (d.get("order_code") or "").strip()]


def reconciliation_summary():
    """Return totals + per-order pledge-vs-paid gaps."""
    orders_ = list_orders()
    pledged = paid = 0
    over = []  # paid > pledged
    under = []  # paid < pledged and not cancelled
    fully = []
    for o in orders_:
        if o.get("status") == "cancelled":
            continue
        p = int(o.get("pledged_donation_cents") or 0)
        d = int(o.get("paid_donation_cents") or 0)
        pledged += p
        paid += d
        rec = {
            "order_code": o["order_code"],
            "buyer": f"{o.get('buyer_first','')} {o.get('buyer_last','')}".strip(),
            "buyer_email": o.get("buyer_email") or "",
            "pledged_cents": p,
            "paid_cents": d,
            "delta_cents": d - p,
            "status": o.get("status"),
        }
        if d == 0 and p == 0:
            continue
        if d >= p and p > 0:
            fully.append(rec)
        if d > p:
            over.append(rec)
        if d < p:
            under.append(rec)
    unmatched = list_unmatched_donations()
    unmatched_total = sum(int(d.get("amount_cents") or 0) for d in unmatched)
    return {
        "pledged_cents": pledged,
        "paid_cents": paid,
        "unmatched": unmatched,
        "unmatched_cents": unmatched_total,
        "under": under,
        "over": over,
        "fully_paid_count": len(fully),
    }


# ── Captain roster mutations ─────────────────────────────────────────────────

_ATTENDEE_EDITABLE = {"first_name", "last_name", "email", "phone", "age_range", "accommodations"}


def update_attendee(participant_id: int, fields: dict):
    """Patch an attendee row. Returns True if any field was applied."""
    pid = int(participant_id)
    clean = {k: (str(v).strip() if v is not None else "")
             for k, v in fields.items() if k in _ATTENDEE_EDITABLE}
    if not clean:
        return False
    if USE_FIRESTORE:
        _col("participants").document(str(pid)).update(clean)
    else:
        conn = _get_db()
        sets = ", ".join(f"{k} = ?" for k in clean)
        conn.execute(f"UPDATE participants SET {sets} WHERE id = ?",
                     list(clean.values()) + [pid])
        conn.commit()
        conn.close()
    return True


def add_attendee_to_order(order_code: str, attendee: dict, table_number: int = None):
    """Append a new attendee to an existing order. Bumps party_size; if
    table_number is provided, also writes an assignment row so the new person
    sits at that table."""
    order_code = (order_code or "").strip()
    if not order_code:
        return None
    data = {
        "order_id": order_code,
        "first_name": (attendee.get("first_name") or "").strip(),
        "last_name": (attendee.get("last_name") or "").strip(),
        "email": (attendee.get("email") or "").strip(),
        "phone": (attendee.get("phone") or "").strip(),
        "age_range": (attendee.get("age_range") or "Adult").strip(),
        "accommodations": (attendee.get("accommodations") or "").strip(),
        "is_captain": 0,
    }
    pid = add_participant(data)
    # Recount party_size from actual rows so we're authoritative.
    full = get_order_full(order_code)
    n = len(full["attendees"]) if full else 0
    if USE_FIRESTORE:
        _col("orders").document(order_code).update(
            {"party_size": n, "updated_at": _now_iso()})
    else:
        conn = _get_db()
        conn.execute("UPDATE orders SET party_size = ?, updated_at = datetime('now') "
                     "WHERE order_code = ?", (n, order_code))
        conn.commit()
        conn.close()
    if table_number:
        upsert_assignment(pid, int(table_number))
    return pid


def pull_flex_to_table(order_code: str, table_number: int):
    """Convert a Flex/waitlist order to confirmed and seat every attendee at
    table_number. Returns the number of attendees seated."""
    order_code = (order_code or "").strip()
    if not order_code or not table_number:
        return 0
    update_order(order_code, {"status": "confirmed"})
    full = get_order_full(order_code)
    if not full:
        return 0
    seated = 0
    for a in full["attendees"]:
        pid = int(a.get("id") or 0)
        if pid:
            upsert_assignment(pid, int(table_number))
            seated += 1
    return seated


def list_flex_orders():
    """Return waitlist (Flex Participant) orders, newest first."""
    rows = [o for o in list_orders() if o.get("status") == "waitlist"]
    rows.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    return rows
