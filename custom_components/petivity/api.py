"""Async client for the Purina Petivity cloud API.

Petivity signs users in through a Cognito hosted page (email/password or
Sign in with Apple). The web app's Cognito client is confidential, so the
code-for-token exchange happens on Petivity's own server, which then keeps
the session in two cookies on ``api.petivity.com``:

- ``id_token``: a Cognito ID token (one hour), which the GraphQL API also
  accepts as the ``jwt`` argument of ``authenticate``;
- ``refresh_token``: the Cognito refresh token, which only Petivity's server
  can redeem, because it holds the client secret.

The client therefore stores that cookie pair, sends it with every request,
and adopts whatever fresh cookies the server sets in reply.
"""

from __future__ import annotations

import base64
from http.cookies import SimpleCookie
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.petivity.com/graphql"

# The API sits behind Akamai, which rejects default library user agents
# ("Python/...", "aiohttp/...", "curl/...") with a 403. Any descriptive agent
# is accepted, so identify the integration honestly. Keep it to a bare
# product token: an agent containing a URL is blocked too.
USER_AGENT = "petivity-homeassistant/0.2.2"

# Renew the ID token this many seconds before it expires.
_TOKEN_MARGIN = 300

_EVENT_FIELDS = """
  id
  startTime
  machine { id }
  normalisedClassification {
    isCat
    isPartialCat
    isElimination
    elimType
    catWeight
    isWeightOutlier
    visitDuration
    isMaintenance
    isScooping
    cat { id }
  }
"""

QUERY_AUTHENTICATED = "query PetivityAuthenticated { authenticated }"

QUERY_HOUSEHOLD = """
query PetivityHousehold($jwt: String!) {
  authenticate(jwt: $jwt) {
    myHousehold {
      id
      machines {
        id
        sn
        name
        batteryPercentage
        powerMode
        showBatteryWarning
        mostRecentUploadAt
        mostRecentUploadWarning
        stFirmwareRevision
        stFirmwareUpgradeAvailable
        espFirmwareRevision
        espFirmwareUpgradeAvailable
        hardwareRevision
        wifiRssi
        isFrozen
        # A monitor's own newest events, so its last visit survives midnight.
        latestEvents: events(
          first: 10
          sort: START_TIME_DESC
          filters: { excludeFalseTriggerClassifications: true }
        ) {
          edges { node { %s } }
        }
      }
      cats {
        id
        name
        inactiveAt
        machine { id }
        latestEvents: events(first: 5, sort: START_TIME_DESC) {
          edges { node { %s } }
        }
      }
    }
  }
}
""" % (_EVENT_FIELDS, _EVENT_FIELDS)

QUERY_EVENTS = """
query PetivityEvents(
  $jwt: String!
  $page: Int!
  $perPage: Int!
  $from: DateTime!
  $to: DateTime!
) {
  authenticate(jwt: $jwt) {
    myHousehold {
      events(
        from: $from
        to: $to
        page: $page
        perPage: $perPage
        sort: START_TIME_DESC
        filters: { excludeFalseTriggerClassifications: true }
      ) {
        pageInfo { totalPages currentPage }
        edges { node { %s } }
      }
    }
  }
}
""" % _EVENT_FIELDS

_EVENTS_PER_PAGE = 100
_EVENTS_MAX_PAGES = 10


class PetivityError(Exception):
    """Base error for the Petivity API."""


class PetivityAuthError(PetivityError):
    """The session was rejected; the user must sign in again."""


class PetivityConnectionError(PetivityError):
    """The API could not be reached or returned an unexpected answer."""


def jwt_expiry(token: str) -> float:
    """Return the ``exp`` claim of a JWT, or 0 if it cannot be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return 0.0


def utc_iso(value: datetime) -> str:
    """Format an aware datetime the way the API expects (UTC, ``Z``)."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class PetivityClient:
    """Minimal Petivity API client."""

    def __init__(self, session: aiohttp.ClientSession, id_token: str, refresh_token: str) -> None:
        self._session = session
        self.id_token = id_token.strip()
        self.refresh_token = refresh_token.strip()

    def _cookie_header(self) -> str:
        return f"id_token={self.id_token}; refresh_token={self.refresh_token}"

    def _absorb_cookies(self, resp: aiohttp.ClientResponse) -> None:
        """Adopt the renewed session cookies the server sends back."""
        for header in resp.headers.getall("Set-Cookie", []):
            cookie = SimpleCookie()
            try:
                cookie.load(header)
            except ValueError:
                continue
            for name in ("id_token", "refresh_token"):
                if name in cookie and cookie[name].value:
                    setattr(self, name, cookie[name].value)

    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Cookie": self._cookie_header(),
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    raise PetivityConnectionError(f"GraphQL returned HTTP {resp.status}")
                self._absorb_cookies(resp)
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise PetivityConnectionError(f"GraphQL request failed: {err}") from err

    async def async_renew(self) -> None:
        """Ask Petivity's server to confirm the session, renewing the ID token."""
        body = await self._post(QUERY_AUTHENTICATED, {})
        if not (body.get("data") or {}).get("authenticated"):
            raise PetivityAuthError("Petivity no longer accepts this session")

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run a query inside ``authenticate(jwt:)`` and return that object."""
        if time.time() > jwt_expiry(self.id_token) - _TOKEN_MARGIN:
            await self.async_renew()
        for attempt in (1, 2):
            body = await self._post(query, {**variables, "jwt": self.id_token})
            auth = (body.get("data") or {}).get("authenticate")
            if auth is not None:
                return auth
            messages = "; ".join(e.get("message", "") for e in body.get("errors") or [])
            if attempt == 1:
                _LOGGER.debug("Petivity query failed (%s); renewing the session", messages)
                await self.async_renew()
                continue
            raise PetivityConnectionError(f"GraphQL error: {messages or 'no data'}")
        raise PetivityConnectionError("unreachable")

    async def async_get_household(self) -> dict[str, Any]:
        """Return the household with its monitors and cats."""
        data = await self._query(QUERY_HOUSEHOLD, {})
        household = data.get("myHousehold")
        if not household:
            raise PetivityConnectionError("account has no household")
        return household

    async def async_get_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Return non-false-trigger events between two aware datetimes, newest first."""
        events: list[dict[str, Any]] = []
        page = 1
        while page <= _EVENTS_MAX_PAGES:
            data = await self._query(
                QUERY_EVENTS,
                {"page": page, "perPage": _EVENTS_PER_PAGE, "from": utc_iso(start), "to": utc_iso(end)},
            )
            block = (data.get("myHousehold") or {}).get("events") or {}
            events.extend(edge["node"] for edge in block.get("edges") or [] if edge.get("node"))
            if page >= ((block.get("pageInfo") or {}).get("totalPages") or 1):
                break
            page += 1
        return events
