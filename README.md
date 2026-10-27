# Purina Petivity for Home Assistant

A custom integration for the [Purina Petivity Smart Litter Box Monitor](https://www.petivity.com/products/smart-litter-box-monitor). It reads your household's monitors, cats and litter box visits from the Petivity cloud.

> **Unofficial.** This is not affiliated with or endorsed by Purina or Nestlé. It uses the same private API as Petivity's own web app, which can change without notice.

Discussion and questions: [Home Assistant Community thread](https://community.home-assistant.io/t/purina-petivity-smart-litter-box-monitor-integration-hacs/1026566). Bugs: [GitHub issues](https://github.com/meeowgler/petivity-homeassistant/issues).

## Why cloud only

The monitor has no local interface. It sleeps with Wi-Fi off, wakes after a visit, uploads a few kilobytes to Petivity, and disconnects again, typically within about five minutes. There is nothing on your network to talk to, so this integration polls the cloud every five minutes. Expect new visits to appear a few minutes after the cat leaves the box.

## What you get

**One device per monitor**

| Entity | Notes |
|---|---|
| Battery | `null` (unknown) when the monitor runs on mains power |
| Battery warning | Petivity's own low-battery flag |
| Last upload | When the monitor last reached Petivity; a good "is it alive" check |
| Visits today, Urinations today, Defecations today | All cats, since local midnight |
| Last visit | Most recent cat visit today |
| Power mode, Wi-Fi signal, Upload warning, Firmware update available | Diagnostic |
| Sensor firmware, Wi-Fi firmware | Diagnostic, disabled by default |
| Visit (event) | Fires once per new cat visit, with `cat`, `visit_type`, `weight_kg`, `duration_s` and `started`. Visits already there when Home Assistant starts do not fire again |

**One device per cat**, named "Petivity <cat>" so its entities do not collide with the same cat in other pet integrations

| Entity | Notes |
|---|---|
| Weight | From the latest weighed visit; outliers are ignored. Pounds on a US-customary system, kilograms otherwise |
| Last visit, Last visit type, Last visit duration | Type is urination, defecation, both, or no elimination |
| Visits today, Urinations today, Defecations today | Since local midnight |

**Cat attribution depends on your labels.** Petivity only credits a visit to a cat once it has been identified, either automatically or by you labeling it in the app. Unlabeled visits still count on the monitor, but not on any cat.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/meeowgler/petivity-homeassistant`, category **Integration**.
2. Install **Purina Petivity**, then restart Home Assistant.

### Manual

Copy `custom_components/petivity` into your Home Assistant `config/custom_components/` folder and restart.

## Setup

In Home Assistant: **Settings → Devices & services → Add integration → Purina Petivity**, then enter the **email and password** for your Petivity (Purina) account — the same ones you use in the Petivity app.

The integration signs in the way the app does (Purina's hosted login), stores the resulting session, and renews it on its own. If sign-in ever stops working — for example after a password change — Home Assistant raises a **re-authentication** prompt for the new password.

> If your account uses **Sign in with Apple** or **Sign in with Google**, set a password on it first (in the Petivity app's account settings, or via "Forgot password" on the website), since this integration signs in with an email and password.

## Privacy

Your email, password and the renewed session tokens are stored in Home Assistant's config entry, like any other integration credential, and the integration talks only to `auth.purina.com` (sign-in) and `api.petivity.com` (data). All of them are redacted from diagnostics downloads, which also replace every Petivity ID with an alias and redact names and serial numbers, so a diagnostics file attached to a public issue does not identify the account.

## Development

`pytest` runs the unit tests (`pip install -r requirements_test.txt` first); CI runs them with hassfest and the HACS check on every push.

`tests/live_check.py` separately runs the API client, the event aggregation and every sensor's value function against your real account, outside Home Assistant:

```bash
PETIVITY_ID_TOKEN=... PETIVITY_REFRESH_TOKEN=... python tests/live_check.py
```

It needs a Python environment with Home Assistant installed.

## License

MIT
