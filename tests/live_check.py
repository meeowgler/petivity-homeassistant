"""Live smoke test against the real Petivity API, outside Home Assistant.

Usage (Python with Home Assistant installed, run from the repo root):

    PETIVITY_ID_TOKEN=... PETIVITY_REFRESH_TOKEN=... python tests/live_check.py

It exercises the API client, the coordinator's event aggregation and every
sensor's value function, then prints what each entity would report. Pass
PETIVITY_JAR=<path> to read and write the tokens from a JSON file instead,
so renewed cookies are kept between runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

import aiohttp  # noqa: E402

from petivity import coordinator as coord  # noqa: E402
from petivity.api import PetivityClient, jwt_expiry  # noqa: E402
from petivity.binary_sensor import MACHINE_BINARY_SENSORS  # noqa: E402
from petivity.sensor import CAT_WEIGHT, COUNT_SENSORS, LATEST_SENSORS, MACHINE_SENSORS  # noqa: E402


async def main() -> None:
    jar_path = os.environ.get("PETIVITY_JAR")
    if jar_path:
        jar = json.loads(Path(jar_path).read_text())
        id_token, refresh_token = jar["id_token"], jar["refresh_token"]
    else:
        id_token = os.environ["PETIVITY_ID_TOKEN"]
        refresh_token = os.environ["PETIVITY_REFRESH_TOKEN"]

    async with aiohttp.ClientSession() as session:
        client = PetivityClient(session, id_token, refresh_token)
        exp = datetime.fromtimestamp(jwt_expiry(client.id_token), timezone.utc)
        print(f"id_token expires {exp:%Y-%m-%d %H:%M:%S} UTC")
        household = await client.async_get_household()
        start = datetime.combine(datetime.now().date(), time.min).astimezone()
        events = await client.async_get_events(start, datetime.now(timezone.utc))
        renewed = client.id_token != id_token
        print(f"session renewed: {renewed}; refresh token changed: {client.refresh_token != refresh_token}")
        if jar_path:
            Path(jar_path).write_text(
                json.dumps({"id_token": client.id_token, "refresh_token": client.refresh_token})
            )

    # Reuse the coordinator's aggregation without a running Home Assistant.
    fake = MagicMock()

    async def household_fn():
        return household

    async def events_fn(*_):
        return events

    fake.client.async_get_household = household_fn
    fake.client.async_get_events = events_fn
    data = await coord.PetivityCoordinator._async_update_data(fake)

    print(f"\n{len(data.machines)} monitors, {len(data.cats)} cats, {len(events)} events today")
    for machine_id, machine in data.machines.items():
        print(f"\n[monitor] {machine.get('name')}")
        for d in MACHINE_SENSORS:
            print(f"  {d.key:22} {d.value_fn(machine)}")
        for d in MACHINE_BINARY_SENSORS:
            print(f"  {d.key:22} {d.value_fn(machine)}")
        for d in COUNT_SENSORS:
            print(f"  {d.key:22} {d.value_fn(data.machine_today[machine_id])}")
        print(f"  {'last_visit':22} {LATEST_SENSORS[0].value_fn(data.machine_latest.get(machine_id))}")
    for cat_id, cat in data.cats.items():
        print(f"\n[cat] {cat.get('name')}")
        for d in COUNT_SENSORS:
            print(f"  {d.key:22} {d.value_fn(data.cat_today[cat_id])}")
        for d in LATEST_SENSORS:
            print(f"  {d.key:22} {d.value_fn(data.cat_latest.get(cat_id))}")
        print(f"  {'weight (kg)':22} {CAT_WEIGHT.value_fn(data.cat_weighed.get(cat_id))}")


if __name__ == "__main__":
    asyncio.run(main())
