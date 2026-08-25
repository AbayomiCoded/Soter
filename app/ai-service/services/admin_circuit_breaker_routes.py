"""
Admin endpoint for circuit breaker observability (acceptance criteria: "An
admin-only endpoint reports current state and time until retry" + "Manual
reset of a circuit is supported").

Written as a Flask Blueprint since the actual web framework wasn't specified.
If the service runs on FastAPI/Django/Express instead, the two handlers below
translate directly - the logic lives in CircuitBreakerRegistry, this file is
just routing + auth glue.

Wire `require_admin` up to whatever auth/authorization your app already uses
for admin routes (e.g. an existing @admin_required decorator) - the stub
below is NOT sufficient for production on its own.
"""

from functools import wraps
from flask import Blueprint, jsonify, request

from services.circuit_breaker import CircuitBreakerRegistry

admin_circuit_breaker_bp = Blueprint("admin_circuit_breaker", __name__)


def require_admin(fn):
    """Placeholder auth guard - replace with your real admin auth check."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not request.headers.get("X-Admin-Token"):
            return jsonify({"error": "admin authentication required"}), 401
        return fn(*args, **kwargs)

    return wrapper


@admin_circuit_breaker_bp.route("/admin/circuit-breakers", methods=["GET"])
@require_admin
def list_circuit_breakers():
    """Report current state and time-to-retry for every configured provider."""
    return jsonify(CircuitBreakerRegistry.all_states())


@admin_circuit_breaker_bp.route(
    "/admin/circuit-breakers/<provider>", methods=["GET"]
)
@require_admin
def get_circuit_breaker(provider: str):
    breaker = CircuitBreakerRegistry.get(provider)
    if breaker is None:
        return jsonify(
            {"error": f"provider '{provider}' has no configured circuit breaker"}
        ), 404
    return jsonify(breaker.get_state())


@admin_circuit_breaker_bp.route(
    "/admin/circuit-breakers/<provider>/reset", methods=["POST"]
)
@require_admin
def reset_circuit_breaker(provider: str):
    """Manually force a provider's circuit back to CLOSED."""
    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "manual_reset_via_admin_api")
    try:
        state = CircuitBreakerRegistry.reset(provider, reason=reason)
    except KeyError:
        return jsonify(
            {"error": f"provider '{provider}' has no configured circuit breaker"}
        ), 404
    return jsonify(state)