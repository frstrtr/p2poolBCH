"""
Persistent subscription store.

Schema (subscriptions.json):
{
    "<chat_id>": {
        "addr":       "bitcoincash:qp...",  # null if not set
        "connect":    true,
        "disconnect": false,
        "share":      false,
        "block":      true
    },
    ...
}

All reads/writes are protected by a filelock so the aiohttp server and the
PTB polling loop can run in the same process without data races.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

from filelock import FileLock

from .config import SUBSCRIPTIONS_FILE

_LOCK_FILE = SUBSCRIPTIONS_FILE + ".lock"
_lock = FileLock(_LOCK_FILE)

DEFAULT_SUB: dict[str, Any] = {
    "addr": None,
    "connect": True,
    "disconnect": True,
    "share": False,
    "block": True,
}

FLAGS = ("connect", "disconnect", "share", "block")


@contextmanager
def _locked():
    with _lock:
        yield


def _load() -> dict[str, dict]:
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return {}
    with open(SUBSCRIPTIONS_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(SUBSCRIPTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

def get(chat_id: int | str) -> dict | None:
    """Return subscription dict for chat_id, or None if not subscribed."""
    with _locked():
        data = _load()
        return data.get(str(chat_id))


def get_or_default(chat_id: int | str) -> dict:
    """Return existing sub or a copy of DEFAULT_SUB (not persisted yet)."""
    sub = get(chat_id)
    return sub if sub is not None else dict(DEFAULT_SUB)


def upsert(chat_id: int | str, updates: dict) -> dict:
    """Apply updates dict to subscription and persist.  Returns new sub."""
    with _locked():
        data = _load()
        key = str(chat_id)
        sub = data.get(key) or dict(DEFAULT_SUB)
        sub.update(updates)
        data[key] = sub
        _save(data)
        return sub


def delete(chat_id: int | str) -> bool:
    """Remove subscription.  Returns True if it existed."""
    with _locked():
        data = _load()
        key = str(chat_id)
        if key in data:
            del data[key]
            _save(data)
            return True
        return False


def all_subscriptions() -> list[tuple[str, dict]]:
    """Return list of (chat_id_str, sub_dict) for all subscribers."""
    with _locked():
        data = _load()
        return list(data.items())
