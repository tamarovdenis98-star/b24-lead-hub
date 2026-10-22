# Безопасность

## Где хранятся секреты
| Секрет | Где | Где точно нет |
|---|---|---|
| Входящий вебхук Б24 (токен в URL) | Postgres `b24_auth`, засевается из `.env` сервера | workflow, git, логи выполнений lib (успешные выполнения не сохраняются) |
| OAuth-токены приложения, `application_token` | Postgres `b24_app` | workflow, git |
| `client_id` / `client_secret` приложения | n8n Credential `B24HUB App OAuth`, отправляется только на `oauth.bitrix24.tech` | workflow, git |
| Секрет intake, токены Telegram и DaData, пароль БД | n8n Credentials, `.env` сервера (`chmod 600`) | workflow, git |
| chat_id для алертов | Postgres `settings` | workflow |

В git попадает только `.env.example`. Проверки: `scripts/check_exports.py` (URL вебхука Б24, токены Telegram, id креденшлов, JWT) и gitleaks по всей истории в CI. Перед каждым коммитом дерево дополнительно проверяется поиском по реальным значениям секретов.

## Кто может вызвать каждую точку входа
| Точка | Проверка | Ответ при ошибке |
|---|---|---|
| `POST /webhook/b24hub/intake` | Header Auth на уровне n8n: выполнение даже не создаётся ([ADR 0001](adr/0001-intake-shared-secret.md)) | 403 |
| `POST /webhook/b24hub/b24-events` | `auth[application_token]` = токен установленного приложения | 403 |
| `/webhook/b24hub/app/install` | только портал из `settings.portal_url`; `AUTH_ID` должен работать на этом портале (пакетный вызов `app.info`, `profile`); ставить приложение может только администратор | 403, ничего не сохраняется |
| `ONAPPINSTALL` / `ONAPPUPDATE` | подлинность по собственному `access_token` события (`app.info` на нашем портале, тот же `member_id`) | 403 |
| `/webhook/b24hub/app`, `/app/tab` | `AUTH_ID` зрителя проверяется вызовом `app.info` на сохранённом портале + `member_id`; GET без токена получает только загрузочную страницу | 403 |
| `/webhook/b24hub/app/robot`, `/app/events` | `auth[application_token]` + `member_id` | 403 |
| test harness | тот же секрет, что у intake | 403 |

## Reverse proxy
n8n отдаёт свои страницы с `CORP: same-origin`, `X-Frame-Options: SAMEORIGIN`, а вебхуки с `CSP: sandbox`. Caddy ослабляет это **только** для `/` (CORP, для проверки доступности со стороны Битрикс24) и для `/webhook/b24hub/app*` (`frame-ancestors https://*.bitrix24.ru`, без `X-Frame-Options`). Редактор n8n и остальные вебхуки не тронуты (проверено curl'ом, см. results.md).

## База данных
- `b24hub_app` (n8n): только DML, без DDL.
- `b24hub_ro` (Grafana): только `SELECT`, без доступа к `b24_auth`, `b24_app`, `settings`.
- Postgres слушает только `127.0.0.1` хоста и внутреннюю docker-сеть; Grafana — только `127.0.0.1:3000` (доступ через SSH-туннель).

## Персональные данные
В `inbox.payload` хранятся имя, телефон, e-mail и текст заявки, это необходимо для повторов и разбора. Наружу они уходят только в Битрикс24. В DaData уходит только ИНН. В алерты Telegram ПДн не попадают: туда идут имя workflow, нода, текст ошибки и ссылка на выполнение. Срок хранения журнала в демо не ограничен; в продуктиве понадобится политика очистки `inbox` и `lead_events`.

## Известные ограничения
- Нет HMAC-подписи тела intake: от подделки защищает TLS и секрет, повтор запроса безвреден благодаря идемпотентности ([ADR 0001](adr/0001-intake-shared-secret.md)).
- Анонимный просмотр Grafana включён, потому что она привязана к `127.0.0.1`; при публикации наружу его нужно выключить.
