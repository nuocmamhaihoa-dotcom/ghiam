"""Connector plugin registry."""
from __future__ import annotations

from typing import Iterable

from integrations.base import ConnectorPlugin, SyncResult


class ConnectorRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ConnectorPlugin] = {}

    def register(self, plugin: ConnectorPlugin) -> None:
        self._plugins[plugin.key] = plugin

    def get(self, key: str) -> ConnectorPlugin | None:
        return self._plugins.get(key)

    def list(self) -> list[ConnectorPlugin]:
        return list(self._plugins.values())

    def keys(self) -> list[str]:
        return sorted(self._plugins)

    def sync(self, key: str, payload: dict | None = None) -> SyncResult:
        plugin = self.get(key)
        if plugin is None:
            return SyncResult(connector=key, ok=False, message="unknown connector", errors=["unknown connector"])
        return plugin.sync(payload)

    def sync_all(self, payload: dict | None = None) -> list[SyncResult]:
        return [p.sync(payload) for p in self._plugins.values()]

    def health(self) -> dict[str, dict]:
        return {k: p.health() for k, p in self._plugins.items()}


def load_builtin_connectors(registry: ConnectorRegistry | None = None) -> ConnectorRegistry:
    from integrations.connectors.asterisk import AsteriskConnector
    from integrations.connectors.bitrix24 import Bitrix24Connector
    from integrations.connectors.callio import CallioConnector
    from integrations.connectors.facebook_lead_ads import FacebookLeadAdsConnector
    from integrations.connectors.google_calendar import GoogleCalendarConnector
    from integrations.connectors.google_sheets import GoogleSheetsConnector
    from integrations.connectors.hubspot import HubspotConnector
    from integrations.connectors.salesforce import SalesforceConnector
    from integrations.connectors.stringee import StringeeConnector
    from integrations.connectors.threecx import ThreeCXConnector
    from integrations.connectors.twilio import TwilioConnector
    from integrations.connectors.zalo_oa import ZaloOaConnector
    from integrations.connectors.zoho import ZohoConnector

    reg = registry or ConnectorRegistry()
    plugins: Iterable[ConnectorPlugin] = (
        HubspotConnector(),
        SalesforceConnector(),
        Bitrix24Connector(),
        ZohoConnector(),
        AsteriskConnector(),
        ThreeCXConnector(),
        CallioConnector(),
        StringeeConnector(),
        TwilioConnector(),
        FacebookLeadAdsConnector(),
        ZaloOaConnector(),
        GoogleSheetsConnector(),
        GoogleCalendarConnector(),
    )
    for plugin in plugins:
        reg.register(plugin)
    return reg


_REGISTRY: ConnectorRegistry | None = None


def get_registry() -> ConnectorRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_builtin_connectors()
    return _REGISTRY


__all__ = ["ConnectorRegistry", "get_registry", "load_builtin_connectors"]
