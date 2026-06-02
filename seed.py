"""Load participants from the signups TSV into SQLite."""
import csv
import os
from models import get_db, init_db

TSV_PATH = os.path.join(os.path.dirname(__file__), "data", "signups.tsv")


def seed(tsv_path=TSV_PATH, replace=True):
    init_db()
    conn = get_db()
    if replace:
        conn.execute("DELETE FROM participants")
    with open(tsv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        inserted = 0
        for row in reader:
            is_captain = 1 if (row.get("Are you a Table Captain?", "").strip().lower() == "yes") else 0
            conn.execute(
                """INSERT INTO participants
                (order_id, first_name, last_name, email, buyer_first, buyer_last,
                 buyer_email, phone, age_range, is_captain, accommodations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.get("Order #", "").strip(),
                    row.get("First Name", "").strip(),
                    row.get("Last Name", "").strip(),
                    row.get("Email", "").strip(),
                    row.get("Buyer First Name", "").strip(),
                    row.get("Buyer Last Name", "").strip(),
                    row.get("Buyer Email", "").strip(),
                    row.get("Phone", "").strip(),
                    row.get("Age Range", "").strip(),
                    is_captain,
                    row.get("Do you require any accommodations to participate in this event?", "").strip(),
                ),
            )
            inserted += 1
    conn.commit()
    conn.close()
    print(f"Seeded {inserted} participants from {tsv_path}")


if __name__ == "__main__":
    seed()
