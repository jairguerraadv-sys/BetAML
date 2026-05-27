from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


def _signature(secret: str, body: bytes, timestamp: str | None = None) -> str:
    signed = (timestamp + ".").encode() + body if timestamp else body
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def test_epsilon_webhook_requires_valid_hmac():
    from libs.connectors import ConnectorEpsilon

    body = b'{"version":"1.0","events":[]}'
    connector = ConnectorEpsilon(signing_secret="secret")

    assert connector.validate_auth({"x-epsilon-signature": _signature("secret", body)}, body)
    assert not connector.validate_auth({"x-epsilon-signature": _signature("wrong", body)}, body)


def test_epsilon_webhook_rejects_stale_timestamp_replay():
    from libs.connectors import ConnectorEpsilon

    body = b'{"version":"1.0","events":[]}'
    connector = ConnectorEpsilon(signing_secret="secret")
    old_ts = str(time.time() - 600)

    headers = {
        "x-epsilon-timestamp": old_ts,
        "x-epsilon-signature": _signature("secret", body, old_ts),
    }

    assert not connector.validate_auth(headers, body)


def test_epsilon_webhook_accepts_signed_fresh_timestamp():
    from libs.connectors import ConnectorEpsilon

    body = b'{"version":"1.0","events":[]}'
    connector = ConnectorEpsilon(signing_secret="secret")
    ts = str(time.time())

    headers = {
        "x-epsilon-timestamp": ts,
        "x-epsilon-signature": _signature("secret", body, ts),
    }

    assert connector.validate_auth(headers, body)
