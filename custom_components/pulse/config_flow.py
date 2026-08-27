"""Config-Flow für Pulse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import PulseApiClient, PulseApiError, PulseAuthError, PulseConnectionError
from .const import (
    CONF_CRITICAL_HOSTS,
    CONF_CRITICAL_HOSTS_MODE,
    CONF_IGNORED_RISK_CODES,
    CONF_ALIAS_MAP,
    CONF_INCLUDE_CONTAINERS,
    CONF_INCLUDE_GUESTS,
    CONF_KNOWN_HOSTS,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    CRITICAL_MODE_ALL,
    CRITICAL_MODE_SELECTED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .coordinator import PulseDataUpdateCoordinator


@dataclass(slots=True)
class ValidationResult:
    title: str
    unique_id: str


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> ValidationResult:
    """Validiert URL, Erreichbarkeit und Token-Scope."""

    data = dict(data)
    data[CONF_HOST] = normalize_url(data[CONF_HOST])
    session = async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True))
    client = PulseApiClient(
        session,
        data[CONF_HOST],
        data[CONF_API_TOKEN],
        verify_ssl=data.get(CONF_VERIFY_SSL, True),
    )
    await client.async_get_health()
    await client.async_get_summary()
    return ValidationResult(title="Pulse", unique_id=entry_unique_id(data[CONF_HOST]))


def normalize_url(value: str) -> str:
    """Normalisiert und validiert die Pulse-Basis-URL."""

    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise vol.Invalid("invalid_url")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise vol.Invalid("invalid_url")
    try:
        port = parts.port
    except ValueError as err:
        raise vol.Invalid("invalid_url") from err
    netloc = parts.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def entry_unique_id(url: str) -> str:
    """Bildet die stabile Config-Entry-ID aus normalisierter URL und Port."""

    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as err:
        raise vol.Invalid("invalid_url") from err
    host = parts.hostname or ""
    return f"{parts.scheme}://{host}:{port}{parts.path.rstrip('/')}"


class PulseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """User- und Reauth-Flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_data: dict[str, Any] | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = self._normalize_user_input(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_url"
            else:
                if data[CONF_HOST].startswith("http://") and not data.get("confirm_http"):
                    self._pending_data = data
                    return await self.async_step_confirm_http()
                if not data[CONF_VERIFY_SSL] and not data.get("confirm_insecure_tls"):
                    self._pending_data = data
                    return await self.async_step_confirm_insecure_tls()
                return await self._async_validate_and_create(data)

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_confirm_http(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None and self._pending_data is not None:
            if user_input.get("confirm_http"):
                data = dict(self._pending_data)
                data["confirm_http"] = True
                return await self._async_validate_and_create(data)
            errors["base"] = "http_not_confirmed"

        return self.async_show_form(
            step_id="confirm_http",
            data_schema=vol.Schema({vol.Required("confirm_http", default=False): bool}),
            errors=errors,
        )

    async def async_step_confirm_insecure_tls(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None and self._pending_data is not None:
            if user_input.get("confirm_insecure_tls"):
                data = dict(self._pending_data)
                data["confirm_insecure_tls"] = True
                return await self._async_validate_and_create(data)
            errors["base"] = "insecure_tls_not_confirmed"

        return self.async_show_form(
            step_id="confirm_insecure_tls",
            data_schema=vol.Schema({vol.Required("confirm_insecure_tls", default=False): bool}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None and self._reauth_entry is not None:
            data = dict(self._reauth_entry.data)
            data[CONF_API_TOKEN] = user_input[CONF_API_TOKEN]
            try:
                await validate_input(self.hass, data)
            except vol.Invalid:
                errors["base"] = "invalid_url"
            except PulseAuthError as err:
                errors["base"] = "insufficient_scope" if err.insufficient_scope else "invalid_auth"
            except PulseConnectionError:
                errors["base"] = "cannot_connect"
            except (PulseApiError, aiohttp.ClientError):
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data=data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def _async_validate_and_create(self, data: dict[str, Any]):
        errors: dict[str, str] = {}
        try:
            result = await validate_input(self.hass, data)
        except vol.Invalid:
            errors["base"] = "invalid_url"
        except PulseAuthError as err:
            errors["base"] = "insufficient_scope" if err.insufficient_scope else "invalid_auth"
        except PulseConnectionError:
            errors["base"] = "cannot_connect"
        except PulseApiError:
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(result.unique_id)
            self._abort_if_unique_id_configured()
            stored = {
                CONF_HOST: data[CONF_HOST],
                CONF_API_TOKEN: data[CONF_API_TOKEN],
                CONF_VERIFY_SSL: data[CONF_VERIFY_SSL],
                CONF_SCAN_INTERVAL: data[CONF_SCAN_INTERVAL],
            }
            return self.async_create_entry(title=result.title, data=stored)

        return self.async_show_form(step_id="user", data_schema=_user_schema(), errors=errors)

    def _normalize_user_input(self, user_input: dict[str, Any]) -> dict[str, Any]:
        data = dict(user_input)
        data[CONF_HOST] = normalize_url(data[CONF_HOST])
        data[CONF_VERIFY_SSL] = bool(data.get(CONF_VERIFY_SSL, True))
        data[CONF_SCAN_INTERVAL] = int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        return data

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return PulseOptionsFlow(config_entry)


class PulseOptionsFlow(config_entries.OptionsFlow):
    """Optionen für Polling, Gäste und kritische Hosts."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            options = dict(user_input)
            if options.get(CONF_CRITICAL_HOSTS_MODE) != CRITICAL_MODE_SELECTED:
                options[CONF_CRITICAL_HOSTS] = []
            for hidden_key in (CONF_KNOWN_HOSTS, CONF_ALIAS_MAP):
                if hidden_key in self._entry.options:
                    options[hidden_key] = self._entry.options[hidden_key]
            if CONF_IGNORED_RISK_CODES not in options and CONF_IGNORED_RISK_CODES in self._entry.options:
                # Ohne Risiko-Gründe im Payload fehlt das Feld im Formular — die
                # Abwahl darf dadurch nicht verloren gehen.
                options[CONF_IGNORED_RISK_CODES] = self._entry.options[CONF_IGNORED_RISK_CODES]
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(),
        )

    def _schema(self) -> vol.Schema:
        options = self._entry.options
        host_options = self._host_options()
        risk_options = self._risk_code_options()
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(CONF_INCLUDE_GUESTS, default=options.get(CONF_INCLUDE_GUESTS, True)): BooleanSelector(),
            vol.Required(
                CONF_INCLUDE_CONTAINERS,
                default=options.get(CONF_INCLUDE_CONTAINERS, False),
            ): BooleanSelector(),
            vol.Required(
                CONF_CRITICAL_HOSTS_MODE,
                default=options.get(CONF_CRITICAL_HOSTS_MODE, CRITICAL_MODE_ALL),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=CRITICAL_MODE_ALL, label="Alle Hosts"),
                        SelectOptionDict(value=CRITICAL_MODE_SELECTED, label="Auswahl"),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
        if host_options:
            schema[
                vol.Optional(
                    CONF_CRITICAL_HOSTS,
                    default=options.get(CONF_CRITICAL_HOSTS, []),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=host_options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        if risk_options:
            schema[
                vol.Optional(
                    CONF_IGNORED_RISK_CODES,
                    default=options.get(CONF_IGNORED_RISK_CODES, []),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=risk_options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        return vol.Schema(schema)

    def _host_options(self) -> list[SelectOptionDict]:
        coordinator = self._entry.runtime_data if hasattr(self._entry, "runtime_data") else None
        if not isinstance(coordinator, PulseDataUpdateCoordinator) or coordinator.data is None:
            return []
        return [
            SelectOptionDict(value=host.canonical_id, label=host.name)
            for host in sorted(coordinator.data.hosts.values(), key=lambda item: item.name)
        ]

    def _risk_code_options(self) -> list[SelectOptionDict]:
        """Auswahl der Risiko-Gründe, die Pulse aktuell meldet.

        Bereits abgewählte Codes bleiben in der Liste, auch wenn Pulse sie
        gerade nicht meldet — sonst verschwände die Auswahl beim nächsten
        Öffnen der Optionen.
        """

        labels = {code: code for code in self._entry.options.get(CONF_IGNORED_RISK_CODES, [])}
        coordinator = self._entry.runtime_data if hasattr(self._entry, "runtime_data") else None
        if isinstance(coordinator, PulseDataUpdateCoordinator) and coordinator.data is not None:
            for storage in coordinator.data.storages.values():
                for reason in storage.risk_reasons:
                    labels[reason.code] = reason.summary or reason.code
        return [SelectOptionDict(value=code, label=label) for code, label in sorted(labels.items())]


def _user_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST): TextSelector(),
            vol.Required(CONF_API_TOKEN): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
            vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
    )
