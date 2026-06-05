"""Cloud Run-friendly structured JSON logging + Flask error reporter.

Emits one JSON object per log record on stdout/stderr so Cloud Logging picks up
severity, message, request trace, and exception info automatically.
"""
import json
import logging
import os
import sys
import time
import traceback
import uuid

from flask import g, request, render_template


_LEVEL_TO_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, project_id: str = ""):
        super().__init__()
        self.project_id = project_id

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = {
            "severity": _LEVEL_TO_SEVERITY.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                    + f".{int(record.msecs):03d}Z",
        }
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        # Attach trace + request id when available
        trace = getattr(record, "trace", None)
        if trace and self.project_id:
            payload["logging.googleapis.com/trace"] = f"projects/{self.project_id}/traces/{trace}"
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        for key in ("path", "method", "status", "remote_ip"):
            v = getattr(record, key, None)
            if v is not None:
                payload[key] = v
        return json.dumps(payload, default=str)


class RequestContextFilter(logging.Filter):
    """Inject Flask request context (trace, request_id, path) onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            if request:
                tc = request.headers.get("X-Cloud-Trace-Context", "")
                if tc:
                    record.trace = tc.split("/", 1)[0]
                record.request_id = getattr(g, "request_id", None) or tc or ""
                record.path = request.path
                record.method = request.method
                record.remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        except Exception:  # noqa: BLE001
            pass
        return True


def configure(app):
    """Wire structured logging + a global error handler onto the Flask app."""
    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    project_id = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or ""
    )

    root = logging.getLogger()
    # Reset existing handlers to avoid duplicate/plain logs from Flask/Gunicorn
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if on_cloud_run:
        handler.setFormatter(JsonFormatter(project_id=project_id))
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s — %(message)s"
        ))
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Quiet noisy libraries
    for noisy in ("werkzeug", "google.auth.transport", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    @app.before_request
    def _assign_request_id():
        g.request_id = (
            request.headers.get("X-Request-Id")
            or request.headers.get("X-Cloud-Trace-Context", "").split("/", 1)[0]
            or uuid.uuid4().hex
        )

    @app.after_request
    def _log_request(resp):
        # Only log non-static, non-favicon paths to keep volume sane
        if not request.path.startswith("/static/") and request.path not in ("/favicon.ico", "/robots.txt"):
            logging.getLogger("request").info(
                "%s %s -> %s",
                request.method, request.path, resp.status_code,
                extra={"status": resp.status_code},
            )
        return resp

    @app.errorhandler(Exception)
    def _on_error(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        logging.getLogger("app").exception("Unhandled exception: %s", e)
        try:
            return render_template("errors/500.html"), 500
        except Exception:  # noqa: BLE001
            return ("Internal Server Error", 500)

    logging.getLogger("app").info(
        "Logging configured (cloud_run=%s, project=%s)", on_cloud_run, project_id or "-"
    )
