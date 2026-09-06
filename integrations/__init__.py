"""Sales OS external system connectors (plugin architecture)."""
from integrations.base import ConnectorPlugin, SyncResult
from integrations.registry import ConnectorRegistry, get_registry, load_builtin_connectors

__all__ = [
    "ConnectorPlugin",
    "SyncResult",
    "ConnectorRegistry",
    "get_registry",
    "load_builtin_connectors",
]
