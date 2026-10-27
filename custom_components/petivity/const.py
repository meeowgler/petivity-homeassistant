"""Constants for the Petivity integration."""

from datetime import timedelta

DOMAIN = "petivity"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
# Session material the integration obtains and renews on its own, stored so a
# restart does not need a fresh sign-in. The DPoP key must be kept because
# Auth0 binds the refresh token to it.
CONF_ID_TOKEN = "id_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DPOP_KEY = "dpop_key"

SCAN_INTERVAL = timedelta(minutes=5)

# How far before local midnight to fetch events, so a visit that uploads
# just after midnight still fires its event entity.
EVENT_LOOKBACK = timedelta(hours=6)

MANUFACTURER = "Purina Petivity"
MACHINE_MODEL = "Smart Litter Box Monitor"
