"""SQLite schema + connection helpers for Longest Table."""
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "longest_table.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    buyer_first TEXT,
    buyer_last TEXT,
    buyer_email TEXT,
    phone TEXT,
    age_range TEXT,
    is_captain INTEGER DEFAULT 0,
    accommodations TEXT,
    table_number INTEGER
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    config TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS menu_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    per_table_count INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_number INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    person_name TEXT NOT NULL,
    item_description TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES menu_categories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS table_tokens (
    table_number INTEGER PRIMARY KEY,
    token TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    participant_id INTEGER PRIMARY KEY,
    table_number INTEGER NOT NULL,
    is_locked INTEGER DEFAULT 0,
    FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS email_templates (
    mode TEXT NOT NULL DEFAULT 'invite',
    id INTEGER NOT NULL CHECK (id IN (1, 2)),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (mode, id)
);

-- ── Phase 1: public registration, donations, photos ──────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_code TEXT UNIQUE NOT NULL,
    buyer_first TEXT NOT NULL,
    buyer_last TEXT NOT NULL,
    buyer_email TEXT NOT NULL,
    buyer_phone TEXT,
    party_size INTEGER NOT NULL DEFAULT 1,
    pledged_donation_cents INTEGER NOT NULL DEFAULT 0,
    paid_donation_cents INTEGER NOT NULL DEFAULT 0,
    donation_method TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | waitlist | cancelled
    is_captain_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_code TEXT,
    amount_cents INTEGER NOT NULL,
    source TEXT NOT NULL,           -- venmo | paypal | cash | check | other
    transaction_id TEXT,
    donor_name TEXT,
    donor_email TEXT,
    note TEXT,
    received_at TEXT DEFAULT (datetime('now')),
    matched_at TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,       -- vienna | event
    filename TEXT NOT NULL,
    url TEXT NOT NULL,
    thumb_url TEXT,
    caption TEXT,
    alt_text TEXT,
    credit TEXT,
    year INTEGER,
    is_featured INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS magic_tokens (
    token TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,          -- order | captain | admin
    subject TEXT NOT NULL,          -- order_code or email
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS email_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT,
    template TEXT,
    status TEXT,
    error TEXT,
    sent_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sponsor_prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    level TEXT,
    amount_cents INTEGER DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'prospect',
    assigned_to TEXT,
    last_contact_at TEXT,
    notes TEXT,
    url TEXT,
    logo_url TEXT,
    on_public_wall INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

EMAIL_MODES = ("invite", "reminder", "dayof")

DEFAULT_RULES = {
    "seats_per_table": 14,
    "min_singles_per_table": 3,
    "min_children_per_table": 2,
    "min_teens_per_table": 2,
    "spread_seniors": True,
    "one_captain_per_table": True,
    "keep_groups_together": True,
    "split_oversize_groups": True,
    "spread_evenly": True,
}

DEFAULT_SETTINGS = {
    "table_count": "0",  # 0 = auto
    "is_locked": "0",    # 1 = assignments are locked
    "event_name": "The Longest Table Vienna",
    "event_tagline": "One table. One town. One cause.",
    "event_date": "Saturday, May 8, 2027",
    "event_time": "12:00 PM - 2:00 PM",
    "event_location": "Mill Street SE, next to the Vienna Town Green",
    "event_year": "2027",
    "registration_opens": "",      # ISO date for public registration open
    "registration_closes": "",     # ISO date for registration close
    "captain_registration_opens": "",
    "captain_registration_closes": "",
    "max_attendees": "0",          # 0 = unlimited
    "max_per_order": "8",
    "default_donation_per_attendee_cents": "1000",  # $10
    "organizer_name": "The Longest Table Vienna Committee",
    "organizer_email": "",
    "reply_to_email": "",
    "from_email": "",
    "app_base_url": "",  # public URL, used for magic links / QR notes
    "paypal_handle": "",            # e.g. "@RusticLove" or paypal.me handle
    "paypal_url": "",               # full https://paypal.me/handle URL override
    "venmo_handle": "",             # e.g. "@Rustic-Love"
    "venmo_url": "",                # full venmo://paycharge or https URL override
    "hero_headline": "The Longest Table Vienna",
    "hero_subhead": "One table. One town. One cause.",
    "about_blurb": (
        "The Longest Table Vienna is a community potluck supper held each spring on "
        "Mill Street, with one long, shared table stretching down our closed-off block. "
        "Neighbors and newcomers, families and seniors gather over food brought from "
        "their own kitchens — hosted by volunteer Table Captains — to share a meal "
        "that benefits Rustic Love and the local school food pantries they support. "
        "The event is free to attend; every dollar donated goes directly to feeding "
        "food-insecure families in our community."
    ),
    "donation_tiers_json": json.dumps([
        {"name": "Angel", "amount_cents": 50000},
        {"name": "Beloved", "amount_cents": 25000},
        {"name": "Sweetheart", "amount_cents": 10000},
        {"name": "Admirer", "amount_cents": 5000},
        {"name": "Darling", "amount_cents": 2500},
        {"name": "Friends of Rustic Love", "amount_cents": 1000},
    ]),
    "secret_key": "",  # generated on first init for magic-link signing
    "admin_emails": "rob@sagecg.com",
    "fundraising_goal_cents": "1500000",   # $15,000 default
    "attendee_goal": "300",
    "show_thermometer": "1",
    "sponsor_levels_json": "",   # JSON array of {name, amount_display, benefits:[], featured?}
    "faq_json": json.dumps([
        {"q": "Is the event free?",
         "a": "Yes. Registration is free. We do ask each attendee to consider a $10 (or more) donation to Rustic Love — every dollar goes to food-insecure families in our area."},
        {"q": "What should I bring?",
         "a": "Your Table Captain will coordinate the potluck for your table and let you know what to bring (a side, dessert, drinks, etc.). Water is provided."},
        {"q": "What if it rains?",
         "a": "There is no rain date. Your Captain may move your table to a nearby home or restaurant — they will let you know."},
        {"q": "Can I bring my kids?",
         "a": "Absolutely. Families are the heart of this event. There are kids' games on the Town Green throughout the meal."},
        {"q": "I need accessibility accommodations.",
         "a": "Please let us know on the registration form — we reserve table sections near the end of the row for wheelchair access and caregivers."},
    ]),
    "sponsors_json": json.dumps([]),
    "sponsors_heading": "Our 2027 sponsors.",
    "sponsors_intro": "Interested in sponsoring? Email us.",
    "testimonials_json": json.dumps([
        {"quote": "It was the most Vienna thing I've ever been to — neighbors I'd waved at for years suddenly felt like friends.",
         "author": "Sarah K.", "role": "Vienna resident, 2025"},
        {"quote": "Our table brought enough food for an army. We left full, and the Rustic Love team did, too.",
         "author": "The Patel family", "role": "Table 14, 2025"},
        {"quote": "I came alone and left with a dinner club. That's the magic of one long table.",
         "author": "Marco D.", "role": "First-time captain, 2025"},
    ]),
    "site_og_image_url": "",
}

DEFAULT_MENU = [
    ("Main Course", 2, "Serves ~8 people", 10),
    ("Side Dish", 3, "", 20),
    ("Dessert", 2, "", 30),
    ("Drinks", 2, "Non-alcoholic only", 40),
    ("Appetizer", 2, "", 50),
]

DEFAULT_EMAIL_ORGANIZER_TO_CAPTAIN = {
    "subject": "You're a Table Captain for {event_name}!",
    "body": """Hi {captain_first},

Thank you for volunteering as Table Captain for {event_name}!

We are so excited to have you join us on {event_date} from {event_time} at {event_location}. 

You are Table {table_number}. Here are the folks seated at your table:

{attendee_list}

A few things we'd love you to do:
1. Send a welcome email to your table (a draft is below).
2. Share your signup link so folks can sign up to bring food/drinks:
   {signup_link}
3. Remind guests about parking details on {event_location}.

Please reply if you have any questions.

Thanks,
{organizer_name}
""",
}

DEFAULT_EMAIL_CAPTAIN_TO_GUESTS = {
    "subject": "Welcome to Table {table_number} at {event_name}!",
    "body": """Hi everyone,

I'm {captain_first}, and I'll be your Table Captain at {event_name} on {event_date}. So glad you'll be joining us!

We'll be meeting at {event_location} starting at {event_time}. 

Here's who will be at Table {table_number}:
{attendee_list}

To make our meal special, please sign up for what you'd like to bring:
{signup_link}

Categories we're filling for our table:
{menu_summary}

        Guest email addresses for this table (copy/paste into To or Bcc):
        {guest_email_list}

Can't wait to share a meal with you!

{captain_first}
""",
}


# ── Reminder mode (T-5 to T-3) ──
DEFAULT_EMAIL_REMINDER_ORG_TO_CAPTAIN = {
    "subject": "Reminder: Table {table_number} captain check-in for {event_name}",
    "body": """Hi {captain_first},

Friendly reminder that {event_name} is coming up on {event_date} at {event_time}.

You're our Captain for Table {table_number}. A quick checklist:
1. Send the reminder note below to your guests so they know what to bring.
2. Check the food signup so we can fill any gaps:
   {signup_link}

Currently claimed for your table:
{claimed_food_list}

Thanks for hosting!
{organizer_name}
""",
}

DEFAULT_EMAIL_REMINDER_CAPTAIN_TO_GUESTS = {
    "subject": "Reminder: Table {table_number} at {event_name}",
    "body": """Hi everyone,

Quick reminder that {event_name} is happening on {event_date} from {event_time} at {event_location}.

Please sign up for what you'll bring (or update what you already signed up for):
{signup_link}

Food currently claimed for our table:
{claimed_food_list}

Categories we're still filling:
{menu_summary}

Guest email addresses for this table (copy/paste into To or Bcc):
{guest_email_list}

Thanks,
{captain_first}
""",
}


# ── Day-of mode (T-1 / morning of) ──
DEFAULT_EMAIL_DAYOF_ORG_TO_CAPTAIN = {
    "subject": "Today: Table {table_number} at {event_name}",
    "body": """Hi {captain_first},

Today's the day! {event_name} starts at {event_time} at {event_location}.

You're hosting Table {table_number}. A few last-minute notes:
1. Send the day-of note (below) to your guests.
2. Plan to arrive a few minutes early to greet folks.

Food claimed for your table:
{claimed_food_list}

See you there!
{organizer_name}
""",
}

DEFAULT_EMAIL_DAYOF_CAPTAIN_TO_GUESTS = {
    "subject": "Today: Table {table_number} at {event_name}",
    "body": """Hi everyone,

Today is {event_name}!
We're meeting at {event_location} from {event_time}.

Table {table_number} food plan (claimed so far):
{claimed_food_list}

If your plan changed, please reply so we can fill any gaps.

Guest email addresses for this table:
{guest_email_list}

See you soon,
{captain_first}
""",
}


DEFAULT_EMAIL_TEMPLATES = {
    "invite": {
        1: DEFAULT_EMAIL_ORGANIZER_TO_CAPTAIN,
        2: DEFAULT_EMAIL_CAPTAIN_TO_GUESTS,
    },
    "reminder": {
        1: DEFAULT_EMAIL_REMINDER_ORG_TO_CAPTAIN,
        2: DEFAULT_EMAIL_REMINDER_CAPTAIN_TO_GUESTS,
    },
    "dayof": {
        1: DEFAULT_EMAIL_DAYOF_ORG_TO_CAPTAIN,
        2: DEFAULT_EMAIL_DAYOF_CAPTAIN_TO_GUESTS,
    },
}


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    cur = conn.execute("SELECT 1 FROM rules WHERE id = 1")
    if not cur.fetchone():
        conn.execute(
            "INSERT INTO rules (id, config) VALUES (1, ?)",
            (json.dumps(DEFAULT_RULES),),
        )
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    # Generate a stable signing secret on first boot
    cur = conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
    if not cur or not (cur["value"] or "").strip():
        import secrets as _s
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('secret_key', ?)",
            (_s.token_urlsafe(48),),
        )
    # Seed default menu if empty
    cur = conn.execute("SELECT COUNT(*) AS c FROM menu_categories").fetchone()
    if cur["c"] == 0:
        for name, cnt, notes, order in DEFAULT_MENU:
            conn.execute(
                "INSERT INTO menu_categories (name, per_table_count, notes, sort_order) "
                "VALUES (?, ?, ?, ?)", (name, cnt, notes, order)
            )
    # Seed email templates (per mode + template id)
    # Migrate legacy schema (no `mode` column) if present.
    # Migration: add thumb_url column to photos if missing (older DBs)
    photo_cols = [r["name"] for r in conn.execute("PRAGMA table_info(photos)").fetchall()]
    if photo_cols and "thumb_url" not in photo_cols:
        conn.execute("ALTER TABLE photos ADD COLUMN thumb_url TEXT")
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(email_templates)").fetchall()]
    if "mode" not in cols:
        # Old schema: id PRIMARY KEY, no mode. Migrate by recreating with mode.
        conn.execute("ALTER TABLE email_templates RENAME TO email_templates_old")
        conn.execute("""CREATE TABLE email_templates (
            mode TEXT NOT NULL DEFAULT 'invite',
            id INTEGER NOT NULL CHECK (id IN (1, 2)),
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            PRIMARY KEY (mode, id)
        )""")
        conn.execute(
            "INSERT INTO email_templates (mode, id, subject, body) "
            "SELECT 'invite', id, subject, body FROM email_templates_old"
        )
        conn.execute("DROP TABLE email_templates_old")
    for mode, tpls in DEFAULT_EMAIL_TEMPLATES.items():
        for tid, tpl in tpls.items():
            conn.execute(
                "INSERT OR IGNORE INTO email_templates (mode, id, subject, body) "
                "VALUES (?, ?, ?, ?)",
                (mode, tid, tpl["subject"], tpl["body"]),
            )
    conn.commit()
    conn.close()


def get_rules():
    conn = get_db()
    row = conn.execute("SELECT config FROM rules WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return dict(DEFAULT_RULES)
    cfg = json.loads(row["config"])
    # Fill in any missing defaults
    for k, v in DEFAULT_RULES.items():
        cfg.setdefault(k, v)
    return cfg


def set_rules(cfg):
    conn = get_db()
    conn.execute(
        "UPDATE rules SET config = ? WHERE id = 1", (json.dumps(cfg),)
    )
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()
