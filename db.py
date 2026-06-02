"""
db.py — Unified database layer.
In production (Cloud Run), GOOGLE_APPLICATION_CREDENTIALS or ADC is set,
so we use Firestore. Locally, we fall back to SQLite.
"""
import os

USE_FIRESTORE = bool(os.environ.get("USE_FIRESTORE") or
                     os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or
                     os.environ.get("K_SERVICE"))  # K_SERVICE is set on Cloud Run

if USE_FIRESTORE:
    from google.cloud import firestore as _firestore
    _client = None

    def _fs():
        global _client
        if _client is None:
            _client = _firestore.Client()
        return _client
