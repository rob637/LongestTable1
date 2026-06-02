"""Thin compatibility shim — all logic lives in store.py (dual-backend)."""
import store


def new_order_code():
    return store.new_order_code()


def create_order(**kwargs):
    code = store.create_order(**kwargs)
    return code, None  # historical (code, id) tuple; id is unused


def get_order(order_code: str):
    return store.get_order_full(order_code)


def list_orders(status: str = "", search: str = ""):
    return store.list_orders(status=status, search=search)


def update_order(order_code: str, fields: dict):
    return store.update_order(order_code, fields)


def cancel_order(order_code: str):
    return store.cancel_order(order_code)


def remove_attendee(order_code: str, participant_id):
    return store.remove_attendee(order_code, int(participant_id))


def record_donation(**kwargs):
    return store.record_donation(**kwargs)


def donation_totals():
    return store.donation_totals()


def list_donations(limit: int = 500):
    return store.list_donations(limit=limit)


def list_photos(collection: str = "", featured_only: bool = False):
    return store.list_photos(collection=collection, featured_only=featured_only)


def add_photo(**kwargs):
    return store.add_photo(**kwargs)


def delete_photo(photo_id):
    return store.delete_photo(photo_id)


def get_photo(photo_id):
    return store.get_photo(photo_id)


def dashboard_stats():
    return store.dashboard_stats()


def stats_and_totals():
    return store.stats_and_totals()
