"""Constants for the Petivity integration."""

from datetime import timedelta

DOMAIN = "petivity"

CONF_ID_TOKEN = "id_token"
CONF_REFRESH_TOKEN = "refresh_token"

SCAN_INTERVAL = timedelta(minutes=5)

# How far before local midnight to fetch events, so a visit that uploads
# just after midnight still fires its event entity.
EVENT_LOOKBACK = timedelta(hours=6)

MANUFACTURER = "Purina Petivity"
MACHINE_MODEL = "Smart Litter Box Monitor"
