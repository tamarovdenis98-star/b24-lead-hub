# ADR 0010: Local server app for robot, deal tab and event subscription

**Status:** accepted (stage 4)

## Context
`bizproc.robot.add`, `bizproc.event.send`, `placement.bind` and `event.bind` work only in an
application context, not with an inbound webhook. The outbound-webhook form on the demo portal did not
save, so the deal events also have to come through the app.

## Decision
- **Install** (`[B24HUB] 05 app install`, "app completes installation itself" = on):
  1. Only the configured portal is accepted (`settings.portal_url`).
  2. One `batch` with the request's `AUTH_ID` authenticates the request (a fake token fails every command)
     and does the setup: `app.info`, `profile` (installer must be an admin), `event.bind` ONCRMDEALUPDATE → `04`
     and ONAPPUNINSTALL → `05c`, `bizproc.robot.add` + `bizproc.robot.update` (idempotent re-install),
     `placement.unbind` + `placement.bind` CRM_DEAL_DETAIL_TAB.
  3. Only after that are `AUTH_ID`, `REFRESH_ID`, `APPLICATION_TOKEN` stored in `b24_app`, and the page
     calls `BX24.installFinish()`. On any problem nothing is stored and installFinish is not called.
- **Tokens** (`[B24HUB] lib b24_app_token`): refreshed on `expired_token` (or when the stored expiry has
  passed), not on a schedule, as the Bitrix24 docs recommend. One refresher at a time (`refresh_lock_until`);
  others wait and reuse the new token. The new `refresh_token` replaces the old one on every refresh.
  `client_id` / `client_secret` live in the n8n credential `B24HUB App OAuth` and are sent only to `oauth.bitrix24.tech`.
- **`lib b24_call`** takes `auth: 'app'` for app-only methods; everything else stays on the inbound webhook
  (smaller blast radius: a broken app does not stop lead processing).
- **Robot** «Проверить контрагента по ИНН» (`05a`): `auth[application_token]` must match `b24_app` (else 403),
  answers 200 at once, checks the INN checksum, DaData (shared 30-day cache), then `bizproc.event.send`
  with CHECK_RESULT / COMPANY_NAME / STATUS / IS_ACTIVE / KPP / OGRN / ADDRESS. Every run → `robot_runs`,
  and `lead_events` if the deal came through the hub.
- **Deal tab «Интеграция»** (`05b`) and **app page** (`05`): the viewer's `AUTH_ID` is checked with `app.info`
  on the stored portal + `member_id`, then the lead journal is rendered server-side (HTML-escaped).
- **Uninstall** (`05c`): ONAPPUNINSTALL carries no OAuth tokens, so `application_token` is the only proof; the
  app row is marked `uninstalled` and its tokens wiped.

## Verified before install (simulated requests)
Install without params / with a fake `AUTH_ID` / from a foreign portal → 403, nothing stored
(`invalid_token` came back from the portal for the fake token). Tab, app page, robot and uninstall without
an installed app → 403. The webhook path of `lib b24_call` still works. The tab HTML was rendered locally
from the real journal of deal 42.

## Amendment (stage 4, after the first real installs)
- **Bitrix24 re-keys the app after `installFinish`.** The `APPLICATION_TOKEN` / tokens posted to the install page
  became invalid (`APPLICATION_NOT_FOUND`); the current ones came in ONAPPINSTALL and on every page open.
  ONAPPINSTALL / ONAPPUPDATE are therefore authenticated by their own `access_token` (`app.info` on our portal,
  same `member_id`), not by comparison with the stored token, and then re-key `b24_app`.
- **Verified page opens re-sync the keys:** the current `APPLICATION_TOKEN` is stored; an admin's token pair
  replaces the stored one.
- **Setup is a library** (`[B24HUB] lib app_setup`, stored OAuth tokens): it runs after ONAPPINSTALL / ONAPPUPDATE,
  when an admin opens the app and the stored setup belongs to another app code (re-created app, e.g. without
  "app completes installation itself"), and on demand (`90 test harness`, `{"setup": true}`).
- **Reverse proxy:** n8n's `CSP: sandbox` and `CORP: same-origin` are overridden for `/` and `/webhook/b24hub/app*`
  only (docs/runbook.md).
