"""ERP errors with a stable code, an HTTP status and a message that says what to do.

Codes follow the Hubzoid conventions (lowercase snake_case, one meaning
everywhere). The CLI maps the status to its exit codes: 2 invalid input,
3 denied, 4 conflict, 5 unavailable, 6 an uncertain write that needs
reconciliation.
"""
from __future__ import annotations


class ERPError(Exception):
    """A refused or failed ERP action. `fields` are extra details that are
    safe to show the caller: never a credential, never a document body."""

    def __init__(self, code: str, message: str, *, status: int = 400, **fields):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = {k: v for k, v in fields.items() if v is not None}

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.fields}

    def __reduce__(self):
        # Workflow engines pickle a failed step's exception and raise it again
        # on replay, so the code must survive pickling.
        return (_rebuild, (type(self), self.code, self.message, self.status, self.fields))


def _rebuild(cls, code, message, status, fields):
    obj = cls.__new__(cls)
    Exception.__init__(obj, message)
    obj.code, obj.message, obj.status, obj.fields = code, message, status, dict(fields)
    return obj


class WriteUncertain(ERPError):
    """The request left, and the outcome is unknown: a timeout, a dropped
    connection, a 5xx or an unreadable answer after a write was sent. The
    document may exist. Reconcile by its external reference before any retry."""

    def __init__(self, message: str = "The ERP may have accepted this write. Reconcile before retrying.",
                 **fields):
        super().__init__("erp_write_ambiguous", message, status=503, **fields)


class NotDispatched(ERPError):
    """The write provably never reached the ERP: refused before sending, or a
    connection that never opened. Safe to try again."""


class Refused(ERPError):
    """The ERP definitely refused the write (a validation error, a permission
    error). Nothing was saved."""


EXIT_CODES = {400: 2, 404: 2, 413: 2, 422: 2, 401: 3, 403: 3, 409: 4, 429: 5, 502: 5, 503: 5}


def exit_code(err: ERPError) -> int:
    if err.code == "erp_write_ambiguous":
        return 6
    return EXIT_CODES.get(err.status, 1)


def config_invalid(path: str, message: str, **fields) -> ERPError:
    return ERPError("config_invalid", f"{path}: {message}", status=422, field=path, **fields)


def not_found(what: str = "record") -> ERPError:
    return ERPError("not_found", f"No such {what}, or it is outside what this desk may see.", status=404)


def writes_disabled() -> ERPError:
    return ERPError("erp_writes_disabled",
                    "ERP writes are disabled in connectors/erpnext.yaml. Set writes_enabled: true after doctor "
                    "reports ready_write, and set erp.submit to draft or approve in work-items.yaml.", status=403)
