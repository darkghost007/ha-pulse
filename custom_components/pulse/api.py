"""API-Client für Pulse."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .const import REQUEST_TIMEOUT


class PulseApiError(Exception):
    """Pulse hat eine unerwartete oder nicht verarbeitbare Antwort geliefert."""


class PulseAuthError(PulseApiError):
    """Der API-Token ist ungültig oder hat nicht genügend Rechte."""

    def __init__(self, message: str, *, insufficient_scope: bool = False) -> None:
        super().__init__(message)
        self.insufficient_scope = insufficient_scope


class PulseConnectionError(PulseApiError):
    """Pulse ist nicht erreichbar oder hat nicht rechtzeitig geantwortet."""


class PulseFeatureUnavailable(PulseApiError):
    """Ein optionales Pulse-Feature ist für diese Instanz nicht verfügbar."""


class PulseApiClient:
    """Kleiner aiohttp-Client für die Pulse-API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_token: str | None = None,
        *,
        verify_ssl: bool = True,
        timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/") + "/"
        self._api_token = api_token
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self.feature_available: dict[str, bool] = {}

    async def async_get_health(self) -> dict[str, Any]:
        """Liefert den öffentlichen Health-Endpunkt."""

        return await self._request("GET", "api/health", authenticated=False)

    async def async_get_summary(self) -> dict[str, Any]:
        """Liefert die leichtgewichtige Status-Zusammenfassung."""

        return await self._request("GET", "api/state/summary")

    async def async_get_version(self) -> dict[str, Any]:
        """Liefert Versionsinformationen."""

        return await self._request("GET", "api/version", authenticated=False)

    async def async_get_state(self) -> dict[str, Any]:
        """Liefert den vollständigen Pulse-Zustand."""

        return await self._request("GET", "api/state")

    async def async_get_resources(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Iteriert den paginierten Ressourcen-Endpunkt vollständig.

        Der Live-Pfad nutzt `/api/state`; diese Methode bleibt bewusst testbar,
        weil die offizielle API-Doku hier eine stille Pagination-Falle enthält.
        """

        page = 1
        resources: list[dict[str, Any]] = []
        page_limit = min(max(limit, 1), 100)

        while True:
            payload = await self._request(
                "GET",
                f"api/resources?page={page}&limit={page_limit}",
            )
            data = payload.get("data", payload.get("resources", payload))
            if not isinstance(data, list):
                raise PulseApiError("Pulse-Ressourcenantwort ist keine Liste")
            resources.extend(item for item in data if isinstance(item, dict))

            meta = payload.get("meta") if isinstance(payload, dict) else None
            total = meta.get("total") if isinstance(meta, dict) else None
            if isinstance(total, int):
                if len(resources) >= total:
                    break
            elif len(data) < page_limit:
                break
            page += 1

        return resources

    async def async_get_feature_payload(self, endpoint: str, feature: str) -> dict[str, Any] | None:
        """Liefert optionale Feature-Daten oder `None` bei HTTP 402."""

        try:
            payload = await self._request("GET", endpoint.lstrip("/"))
        except PulseFeatureUnavailable:
            self.feature_available[feature] = False
            return None
        self.feature_available[feature] = True
        return payload

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        """Führt einen einzelnen Request aus und kapselt aiohttp-Details."""

        headers = {}
        if authenticated and self._api_token:
            headers["X-API-Token"] = self._api_token

        url = urljoin(self._base_url, endpoint)
        connection_error: PulseConnectionError | None = None
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    allow_redirects=False,
                    ssl=self._verify_ssl,
                )
                async with response:
                    if HTTPStatus.MULTIPLE_CHOICES <= response.status < HTTPStatus.BAD_REQUEST:
                        raise PulseApiError("Pulse-Endpunkt lieferte eine Weiterleitung")
                    if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                        text = await _safe_text(response)
                        raise PulseAuthError(
                            _auth_message(response.status, text),
                            insufficient_scope="scope" in text.lower(),
                        )
                    if response.status == HTTPStatus.PAYMENT_REQUIRED:
                        raise PulseFeatureUnavailable("Pulse-Feature ist nicht verfügbar")
                    if response.status >= HTTPStatus.BAD_REQUEST:
                        raise PulseApiError(f"Pulse-Endpunkt lieferte HTTP {response.status}")
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        raise PulseApiError("Pulse-Antwort ist kein gültiges JSON") from None
        except PulseApiError:
            raise
        except TimeoutError:
            connection_error = PulseConnectionError("Zeitüberschreitung beim Verbinden mit Pulse")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            connection_error = PulseConnectionError("Pulse ist nicht erreichbar")

        if connection_error is not None:
            raise connection_error from None

        if not isinstance(payload, dict):
            raise PulseApiError("Pulse-Antwort ist kein JSON-Objekt")
        return payload


async def _safe_text(response: aiohttp.ClientResponse) -> str:
    """Liest eine kurze Fehlerantwort ohne Request-Metadaten zu loggen."""

    try:
        return (await response.text())[:500]
    except aiohttp.ClientError:
        return ""


def _auth_message(status: int, text: str) -> str:
    if status == HTTPStatus.UNAUTHORIZED:
        return "Pulse API-Token wurde abgelehnt"
    if "scope" in text.lower():
        return "Pulse API-Token hat nicht den nötigen Scope"
    return "Pulse Zugriff wurde verweigert"
