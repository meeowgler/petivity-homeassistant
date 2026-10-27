"""Async client for the Purina Petivity cloud GraphQL API.

The API wraps every query in ``authenticate(jwt:)`` and accepts the ID token
that Purina's Auth0 tenant issues for the mobile app's client. Obtaining and
renewing that token from an email and password lives in ``purina_auth``; this
module only runs the GraphQL queries, asking the auth provider for a fresh
token and retrying once if the API reports the token as no longer valid.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from .purina_auth import PurinaAuth

_LOGGER = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.petivity.com/graphql"

# The API sits behind Akamai, which rejects default library user agents
# ("Python/...", "aiohttp/...", "curl/...") with a 403. Any descriptive agent
# is accepted, so identify the integration honestly. Keep it to a bare
# product token: an agent containing a URL is blocked too.
USER_AGENT = "petivity-homeassistant/0.3.0"

# Petivity's gateway times out now and then (HTTP 504), and a connection or
# DNS lookup can fail once. One retry after a pause rides out those blips
# without hiding a real outage, which still fails the second attempt.
_RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_DELAY = 5

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


class _PetivityTransientError(PetivityConnectionError):
    """A failure worth one retry: a gateway error, a timeout or a lost connection."""


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
    """Minimal Petivity GraphQL client, authenticated by a token provider."""

    def __init__(self, session: aiohttp.ClientSession, auth: PurinaAuth) -> None:
        self._session = session
        self._auth = auth

    async def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._post_once(query, variables)
        except _PetivityTransientError as err:
            _LOGGER.debug("Petivity request failed (%s); retrying in %s s", err, _RETRY_DELAY)
        await asyncio.sleep(_RETRY_DELAY)
        return await self._post_once(query, variables)

    async def _post_once(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status in _RETRY_STATUSES:
                    raise _PetivityTransientError(f"GraphQL returned HTTP {resp.status}")
                if resp.status != 200:
                    raise PetivityConnectionError(f"GraphQL returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientConnectionError, TimeoutError) as err:
            raise _PetivityTransientError(f"GraphQL request failed: {err}") from err
        except (aiohttp.ClientError, ValueError) as err:
            raise PetivityConnectionError(f"GraphQL request failed: {err}") from err

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run a query inside ``authenticate(jwt:)`` and return that object."""
        # ``authenticate`` returns null for a token the API will not accept, so
        # a null answer earns one retry with a freshly forced token before it
        # is treated as an error.
        body: dict[str, Any] = {}
        for force in (False, True):
            token = await self._auth.async_id_token(force=force)
            body = await self._post(query, {**variables, "jwt": token})
            auth = (body.get("data") or {}).get("authenticate")
            if auth is not None:
                return auth
        messages = "; ".join(e.get("message", "") for e in body.get("errors") or [])
        raise PetivityConnectionError(f"GraphQL error: {messages or 'no data'}")

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
