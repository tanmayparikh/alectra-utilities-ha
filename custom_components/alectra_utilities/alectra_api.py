"""Alectra Utilities API client.

Uses curl_cffi to impersonate Chrome's TLS fingerprint (JA3/JA3S), which is
required to bypass Cloudflare Bot Management. Plain aiohttp and cloudscraper
both fail because their TLS stack differs from Chrome's on Linux.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from curl_cffi.requests import AsyncSession

_LOGGER = logging.getLogger(__name__)

_BASE = "https://alectra-svc.smartcmobile.link"
_DEVICE_ID = "||Python||HA||"
_IMPERSONATE = "chrome124"

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://myalectra.alectrautilities.com",
    "Referer": "https://myalectra.alectrautilities.com/",
    "uid": "1",
    "pt": "1",
    "st": "PL",
}


class AlectraAuthError(Exception):
    """Invalid credentials or token expired and re-auth failed."""


class AlectraApiError(Exception):
    """API returned an error or network failure."""


@dataclass
class UsageRecord:
    read_date: datetime
    consumption: float
    amount: float
    tier_tou: str
    uom: str


class AlectraClient:
    """
    Async client for the Alectra / Smart Energy Water (SCM) portal API.

    Uses curl_cffi AsyncSession with Chrome TLS impersonation to pass
    Cloudflare Bot Management checks on any OS/Python version.
    """

    def __init__(
        self,
        username: str,
        password: str,
        account_number: str,
        customer_number: str = "",
        meter_number: str = "",
        ip_address: str = "0.0.0.0",
    ) -> None:
        self._username = username
        self._password = password
        self._account = account_number
        self._customer = customer_number
        self._meter = meter_number
        self._ip = ip_address

        self._session: AsyncSession | None = None
        self._token: str | None = None
        self._uuid: str | None = None
        self._token_exp: int = 0

    def _get_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate=_IMPERSONATE)
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """Obtain a JWT access token. Raises AlectraAuthError on failure."""
        payload = {
            "username": self._username,
            "password": self._password,
            "guestToken": "",
            "customattributes": {
                "ip": self._ip,
                "client": "Web",
                "version": "10",
                "deviceId": _DEVICE_ID,
                "deviceName": _DEVICE_ID,
                "deviceType": 0,
                "os": "Linux",
            },
        }
        data = await self._request(
            "POST",
            "/UsermanagementAPI/api/1/Login/auth",
            json=payload,
            authenticated=False,
        )
        self._token = data["accessToken"]
        self._uuid = data["user"]["uuid"]
        self._token_exp = _decode_exp(self._token)
        _LOGGER.debug("Alectra login OK, uuid=%s", self._uuid)

    def _expired(self) -> bool:
        return not self._token or time.time() > self._token_exp - 60

    async def _ensure_auth(self) -> None:
        if self._expired():
            await self.login()

    # ------------------------------------------------------------------
    # Data endpoints
    # ------------------------------------------------------------------

    async def discover_account(self) -> dict[str, str]:
        """
        Discover customer_number and meter_number from the API.

        Requires only account_number + valid login. Returns a dict with
        'customer_number' and 'meter_number' for persistence in config entry.
        """
        await self._ensure_auth()
        accounts = await self._request(
            "POST",
            "/apiservices/api/1/account/GetAccountPaperLess",
            json={
                "accountNumber": self._account,
                "customerNumber": "",
                "uuid": self._uuid,
            },
        )
        if not accounts:
            raise AlectraApiError(
                f"No account data returned for account {self._account}"
            )
        customer_number = accounts[0].get("customerNumber", "")
        self._customer = customer_number

        rate_plans = await self._request(
            "POST",
            "/RatePlanAnalysisapi/api/1/rate/rateplan",
            json={
                "accountData": [
                    {"accountNumber": self._account, "meterNumber": ""}
                ]
            },
        )
        meter_number = rate_plans[0].get("meterNumber", "") if rate_plans else ""
        self._meter = meter_number

        return {"customer_number": customer_number, "meter_number": meter_number}

    async def get_account_info(self) -> list[dict]:
        """Return paperless/address info for the configured account."""
        await self._ensure_auth()
        return await self._request(
            "POST",
            "/apiservices/api/1/account/GetAccountPaperLess",
            json={
                "accountNumber": self._account,
                "customerNumber": self._customer,
                "uuid": self._uuid,
            },
        )

    async def get_rate_plan(self) -> list[dict]:
        """Return rate plan for the account."""
        await self._ensure_auth()
        return await self._request(
            "POST",
            "/RatePlanAnalysisapi/api/1/rate/rateplan",
            json={
                "accountData": [
                    {"accountNumber": self._account, "meterNumber": self._meter}
                ]
            },
        )

    async def get_usage(
        self,
        from_date: date,
        to_date: date,
        uom: str = "kWh",
        periodicity: str = "DA",
    ) -> list[UsageRecord]:
        """
        Fetch usage records.

        For HH periodicity the API's readDate timestamps are unreliable.
        Timestamps are computed sequentially: from_date 00:00 + 1h per record.
        Records at or beyond to_date 00:00 are dropped (API boundary overlap).

        Returns records sorted by read_date ascending.
        """
        await self._ensure_auth()
        raw = await self._request(
            "GET",
            "/UsageAPI/api/V1/Electric",
            params={
                "AccountNumber": self._account,
                "MeterNumber": self._meter,
                "From": from_date.isoformat(),
                "To": to_date.isoformat(),
                "Uom": uom,
                "Periodicity": periodicity,
            },
            result_key="Result",
        )
        items = raw.get("electricUsages", [])
        records: list[UsageRecord] = []

        if periodicity == "HH":
            # Ignore API readDate — compute hourly slots from from_date 00:00
            slot_start = datetime(from_date.year, from_date.month, from_date.day)
            cutoff = datetime(to_date.year, to_date.month, to_date.day)
            for i, item in enumerate(items):
                slot_dt = slot_start + timedelta(hours=i)
                if slot_dt >= cutoff:
                    break
                try:
                    records.append(
                        UsageRecord(
                            read_date=slot_dt,
                            consumption=float(item.get("consumption") or 0),
                            amount=float(item.get("amount") or 0),
                            tier_tou=item.get("tierTou", ""),
                            uom=item.get("uom", uom),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    _LOGGER.warning("Skipping malformed HH record index %d: %s", i, exc)
        else:
            for item in items:
                try:
                    records.append(
                        UsageRecord(
                            read_date=datetime.fromisoformat(item["readDate"]),
                            consumption=float(item.get("consumption") or 0),
                            amount=float(item.get("amount") or 0),
                            tier_tou=item.get("tierTou", ""),
                            uom=item.get("uom", uom),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    _LOGGER.warning("Skipping malformed DA record: %s (%s)", item, exc)
            records.sort(key=lambda r: r.read_date)

        return records

    # ------------------------------------------------------------------
    # HTTP core
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        result_key: str = "data",
        **kwargs: Any,
    ) -> Any:
        session = self._get_session()
        headers = dict(_HEADERS)
        if method in ("POST", "PUT", "PATCH"):
            headers["Content-Type"] = "application/json;charset=UTF-8"
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        url = _BASE + path
        _LOGGER.debug("Request: %s %s", method, url)
        try:
            resp = await session.request(
                method, url, headers=headers, timeout=20, **kwargs
            )
        except Exception as exc:
            _LOGGER.error("Network error on %s %s: %s", method, url, exc)
            raise AlectraApiError(f"Network error on {path}: {exc}") from exc

        _LOGGER.debug("Response: %s %s → %s", method, path, resp.status_code)

        if not resp.ok:
            snippet = resp.text[:300] if resp.text else ""
            _LOGGER.error(
                "Alectra HTTP %s on %s — body: %s", resp.status_code, path, snippet
            )
            if resp.status_code == 401:
                raise AlectraAuthError(f"401 on {path}")
            if resp.status_code == 403:
                if "cloudflare" in snippet.lower() or "attention required" in snippet.lower():
                    raise AlectraApiError(f"Cloudflare blocked request on {path}")
                raise AlectraAuthError(f"403 on {path}")
            raise AlectraApiError(f"HTTP {resp.status_code} on {path}")

        try:
            body = resp.json()
        except ValueError as exc:
            _LOGGER.error("Non-JSON response on %s: %s", path, resp.text[:300])
            raise AlectraApiError(f"Non-JSON response on {path}") from exc

        status = body.get("status") or {}
        code = status.get("code") or status.get("StatusCode")
        if status.get("error") or (isinstance(code, int) and code >= 400):
            _LOGGER.error("API error on %s: %s", path, body)
            raise AlectraApiError(
                f"API error on {path}: {status.get('message')} [{code}]"
            )

        return body.get(result_key, body)


def _decode_exp(token: str) -> int:
    """Extract JWT exp claim without verifying signature."""
    try:
        parts = token.split(".")
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.b64decode(padded).decode())
        return int(claims.get("exp", 0))
    except Exception:
        return 0
