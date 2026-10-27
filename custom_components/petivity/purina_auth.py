"""Obtain a Petivity session from an email and password.

The Petivity app signs in against Purina's Auth0 tenant (``auth.purina.com``)
with the OAuth Authorization Code flow: PKCE, the app's public client (no
secret), and DPoP-bound tokens. It then calls the Petivity GraphQL API with
the resulting ID token. This module reproduces that sign-in so Home Assistant
can obtain and renew the session on its own, instead of the user pasting the
cookies a browser session leaves behind.

Only ``auth.purina.com`` is contacted here; the GraphQL calls stay in
``api.py``. The DPoP key is generated once at sign-in and must be stored,
because Auth0 binds the refresh token to it: a lost key forces a new sign-in.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import PetivityAuthError, PetivityConnectionError, jwt_expiry

_LOGGER = logging.getLogger(__name__)

AUTH0 = "https://auth.purina.com"
# The Petivity app's public Auth0 client. It has no secret, so the
# code-for-token exchange runs from here with PKCE, exactly as the app does.
CLIENT_ID = "ZFugl7HTXN8jwpQRKqBMfdXTa72v1ucm"
REDIRECT_URI = "arbuckle://auth0-login"
SCOPE = "openid profile email offline_access"
AUDIENCE = "https://api.services.purina.com/remember-me"

# The hosted login pages are content-negotiated for browsers; identify as one.
_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
_HTML_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Renew the ID token this many seconds before it expires.
_TOKEN_MARGIN = 300
_MAX_REDIRECTS = 10


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "crv": "P-256",
        "kty": "EC",
        "x": _b64url(numbers.x.to_bytes(32, "big")),
        "y": _b64url(numbers.y.to_bytes(32, "big")),
    }


def _thumbprint(key: ec.EllipticCurvePrivateKey) -> str:
    """RFC 7638 JWK thumbprint, the ``dpop_jkt`` sent to ``/authorize``."""
    canonical = json.dumps(_jwk(key), separators=(",", ":"), sort_keys=True)
    return _b64url(hashlib.sha256(canonical.encode()).digest())


def _dpop_proof(key: ec.EllipticCurvePrivateKey, url: str, nonce: str | None) -> str:
    """Build a DPoP proof JWT for a POST to ``url``."""
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": _jwk(key)}
    payload: dict[str, object] = {
        "jti": secrets.token_urlsafe(16),
        "htm": "POST",
        "htu": url,
        "iat": int(time.time()),
    }
    if nonce:
        payload["nonce"] = nonce
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    signature = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"{signing_input}.{signature}"


def _dump_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _load_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("stored DPoP key is not an EC key")
    return key


def _login_context(html: str) -> dict:
    """Extract Auth0's base64 ``universal_login_context`` from a login page."""
    match = re.search(
        r'universal_login_context=JSON\.parse\(new TextDecoder\([^)]*\)'
        r'\.decode\(Uint8Array\.from\(atob\("([^"]+)"\)',
        html,
    )
    if not match:
        raise PetivityConnectionError("unexpected Purina login page")
    try:
        return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as err:
        raise PetivityConnectionError("could not read the Purina login page") from err


def _transaction_state(context: dict) -> str:
    state = (context.get("transaction") or {}).get("state")
    if not state:
        raise PetivityConnectionError("Purina login page carried no transaction state")
    return state


def _json_or_empty(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class PurinaAuth:
    """Obtain and renew a Petivity session from an email and password.

    The ID token (24 hours) is what the GraphQL API accepts. It is renewed
    from the refresh token, and when that is rejected the password is used to
    sign in again, so the integration heals itself without a reauth prompt
    unless the password itself stops working.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        id_token: str | None = None,
        refresh_token: str | None = None,
        dpop_key_pem: str | None = None,
    ) -> None:
        self._hass = hass
        # A cookie-free session for the token endpoint (DPoP carries the
        # binding; no cookies are needed there). Sign-in uses its own
        # cookie-jar session, created per attempt below.
        self._session = session
        self._email = email
        self._password = password
        self.id_token = (id_token or "").strip() or None
        self.refresh_token = (refresh_token or "").strip() or None
        self._key = _load_key(dpop_key_pem) if dpop_key_pem else None
        self._lock = asyncio.Lock()

    @property
    def dpop_key_pem(self) -> str | None:
        """The DPoP private key, to persist alongside the refresh token."""
        return _dump_key(self._key) if self._key is not None else None

    async def async_login(self) -> None:
        """Sign in with the email and password (used by the config flow)."""
        async with self._lock:
            await self._login()

    async def async_id_token(self, *, force: bool = False) -> str:
        """Return a usable ID token, renewing or signing in again as needed."""
        async with self._lock:
            if (
                not force
                and self.id_token
                and time.time() < jwt_expiry(self.id_token) - _TOKEN_MARGIN
            ):
                return self.id_token
            await self._renew_or_login()
            assert self.id_token is not None  # _renew_or_login sets it or raises
            return self.id_token

    async def _renew_or_login(self) -> None:
        if self.refresh_token and self._key is not None:
            try:
                await self._refresh()
                return
            except PetivityAuthError:
                _LOGGER.debug("Petivity refresh token was rejected; signing in again")
        await self._login()

    @asynccontextmanager
    async def _sign_in_session(self) -> AsyncIterator[aiohttp.ClientSession]:
        # Auth0's hosted login carries its state in cookies between the
        # /authorize, identifier and password steps, so this needs a real
        # cookie jar (unlike the GraphQL session). Detach, never close, a
        # helper-created session.
        session = async_create_clientsession(
            self._hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
        )
        try:
            yield session
        finally:
            session.detach()

    async def _login(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "audience": AUDIENCE,
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(24),
            "prompt": "login",
            "screen_hint": "login",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "dpop_jkt": _thumbprint(key),
            "auth0Client": _b64url(
                json.dumps(
                    {"name": "Auth0.Android", "env": {"android": "36"}, "version": "3.12.0"}
                ).encode()
            ),
        }
        async with self._sign_in_session() as session:
            html, _ = await self._navigate(session, "GET", f"{AUTH0}/authorize?{urlencode(params)}")
            state = _transaction_state(_login_context(html))
            html, _ = await self._navigate(
                session,
                "POST",
                f"{AUTH0}/u/login/identifier?state={state}",
                form={
                    "state": state,
                    "username": self._email,
                    "js-available": "true",
                    "webauthn-available": "true",
                    "is-brave": "false",
                    "webauthn-platform-available": "false",
                    "action": "default",
                },
            )
            state = _transaction_state(_login_context(html))
            _, code = await self._navigate(
                session,
                "POST",
                f"{AUTH0}/u/login/password?state={state}",
                form={
                    "state": state,
                    "username": self._email,
                    "password": self._password,
                    "action": "default",
                },
            )
            if not code:
                raise PetivityAuthError("Purina did not accept the email or password")
            tokens = await self._token(
                session,
                key,
                {
                    "client_id": CLIENT_ID,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": verifier,
                },
            )
        self._key = key
        self._apply(tokens)

    async def _refresh(self) -> None:
        assert self._key is not None
        tokens = await self._token(
            self._session,
            self._key,
            {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        self._apply(tokens)

    def _apply(self, tokens: dict) -> None:
        id_token = tokens.get("id_token")
        if not id_token:
            raise PetivityConnectionError("Purina returned no ID token")
        self.id_token = id_token
        # Auth0 rotates the refresh token on every use; keep the newest one.
        if tokens.get("refresh_token"):
            self.refresh_token = tokens["refresh_token"]

    async def _navigate(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        form: dict[str, str] | None = None,
    ) -> tuple[str, str | None]:
        """Issue one request and follow HTTP redirects by hand.

        aiohttp cannot follow a redirect to the app's ``arbuckle://`` scheme,
        so redirects are chased manually and the loop stops at the first page
        it lands on or at that callback. Returns ``(page_html, code)``; the
        code is set only when the callback carried one.
        """
        body: bytes | None = urlencode(form).encode() if form is not None else None
        headers = dict(_HTML_HEADERS)
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Origin"] = AUTH0
        for _ in range(_MAX_REDIRECTS):
            async with session.request(
                method,
                url,
                data=body,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if location.startswith("arbuckle:"):
                        query = parse_qs(urlsplit(location).query)
                        return "", (query.get("code") or [None])[0]
                    url = urljoin(str(resp.url), location)
                    if resp.status in (301, 302, 303):
                        method, body = "GET", None
                        headers.pop("Content-Type", None)
                    continue
                text = await resp.text()
                if resp.status != 200:
                    raise PetivityConnectionError(f"Purina login returned HTTP {resp.status}")
                return text, None
        raise PetivityConnectionError("too many redirects during Purina sign-in")

    async def _token(
        self, session: aiohttp.ClientSession, key: ec.EllipticCurvePrivateKey, body: dict
    ) -> dict:
        """POST to ``/oauth/token`` with a DPoP proof, retrying once for the nonce."""
        url = f"{AUTH0}/oauth/token"
        nonce: str | None = None
        for _ in range(2):
            try:
                async with session.post(
                    url,
                    json=body,
                    headers={
                        "User-Agent": _UA,
                        "Content-Type": "application/json",
                        "DPoP": _dpop_proof(key, url, nonce),
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        return _json_or_empty(text)
                    data = _json_or_empty(text)
                    # Auth0 requires a server-issued nonce in the DPoP proof;
                    # the first try is told which one to use. Retry once.
                    if data.get("error") == "use_dpop_nonce" and resp.headers.get("DPoP-Nonce"):
                        nonce = resp.headers["DPoP-Nonce"]
                        continue
                    if data.get("error") in ("invalid_grant", "access_denied"):
                        raise PetivityAuthError(
                            f"Purina rejected the sign-in ({data.get('error')})"
                        )
                    raise PetivityConnectionError(
                        f"Purina token request failed: HTTP {resp.status} {data.get('error', '')}"
                    )
            except (aiohttp.ClientError, TimeoutError) as err:
                raise PetivityConnectionError(f"Purina token request failed: {err}") from err
        raise PetivityConnectionError("Purina kept asking for a new DPoP nonce")
