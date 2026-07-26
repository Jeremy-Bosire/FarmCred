"""Connector registry -- the single place a data source is looked up.

Callers ask for a *source name*, never a class::

    from connectors import get_connector
    record = get_connector("kiamis").fetch("F001")

Which implementation they get is decided by ``config.CONNECTOR_MODE[source]``,
which is in turn overridable per-source by ``CONNECTOR_MODE_<SOURCE>`` in the
environment. That indirection is what makes the mock -> live flip a
configuration change rather than a code change.

Registering a live connector
----------------------------
Implement :class:`~connectors.base.BaseConnector`, set ``source`` and
``mode = "live"``, and register it::

    @register
    class LiveKIAMISConnector(BaseConnector):
        source = "kiamis"
        mode = "live"

        def fetch(self, farmer_id): ...

Then set ``CONNECTOR_MODE_KIAMIS=live``. Nothing in :mod:`scoring` changes.
"""

from __future__ import annotations

from typing import Dict, Type

import config

from .base import BaseConnector, ConnectorUnavailableError

#: ``{source: {mode: connector_class}}``
_REGISTRY: Dict[str, Dict[str, Type[BaseConnector]]] = {}


def register(cls: Type[BaseConnector]) -> Type[BaseConnector]:
    """Class decorator that adds a connector to the registry.

    Usable directly as ``@register`` above a connector class.
    """
    source = getattr(cls, "source", "")
    mode = getattr(cls, "mode", "")

    if source not in config.CONNECTOR_SOURCES:
        raise ValueError(
            f"{cls.__name__}.source={source!r} is not a known source; "
            f"expected one of {list(config.CONNECTOR_SOURCES)}"
        )
    if mode not in ("mock", "live"):
        raise ValueError(f"{cls.__name__}.mode={mode!r} must be 'mock' or 'live'")

    existing = _REGISTRY.setdefault(source, {}).get(mode)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"{source!r}/{mode!r} is already registered to {existing.__name__}"
        )

    _REGISTRY[source][mode] = cls
    return cls


def get_connector(source: str) -> BaseConnector:
    """Return a connector instance for ``source`` in its configured mode.

    Raises :class:`LookupError` for an unknown source, and
    :class:`ConnectorUnavailableError` when the source is configured ``live``
    but no live implementation has been registered yet -- which is the honest
    answer, and one the engine already knows how to resolve to a neutral band
    rather than crashing a farmer's session.
    """
    if source not in config.CONNECTOR_SOURCES:
        raise LookupError(
            f"unknown connector source {source!r}; "
            f"expected one of {list(config.CONNECTOR_SOURCES)}"
        )

    mode = config.CONNECTOR_MODE.get(source, "mock")
    by_mode = _REGISTRY.get(source, {})
    cls = by_mode.get(mode)

    if cls is None:
        raise ConnectorUnavailableError(
            source,
            f"no {mode!r} implementation registered "
            f"(available: {sorted(by_mode) or 'none'})",
        )
    return cls()


def get_all_connectors() -> Dict[str, BaseConnector]:
    """Instantiate every known source in its configured mode.

    Sources with no implementation for their configured mode are omitted rather
    than raising, so a partially-live deployment still returns the connectors it
    does have. The engine treats an absent source the same as an unavailable
    one: neutral band, ``raw_sources_used[source] = False``.
    """
    out: Dict[str, BaseConnector] = {}
    for source in config.CONNECTOR_SOURCES:
        try:
            out[source] = get_connector(source)
        except ConnectorUnavailableError:
            continue
    return out


def registered_modes(source: str) -> list[str]:
    """Modes with a registered implementation for ``source``. For diagnostics."""
    return sorted(_REGISTRY.get(source, {}))


# Importing the modules is what triggers their @register decorators.
from . import agrovet_mpesa, climate, cooperative, crb_paygo, kiamis  # noqa: E402,F401

__all__ = [
    "BaseConnector",
    "ConnectorUnavailableError",
    "get_all_connectors",
    "get_connector",
    "register",
    "registered_modes",
]
