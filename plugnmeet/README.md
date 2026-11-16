# plugNmeet — official release snapshot

> **خلاصه:** این پوشه شامل نسخه‌ی رسمی سه جزء اصلی پلتفرم plugNmeet (سرور، کلاینت و ریکوردر) است که مستقیماً از مخازن رسمی GitHub و از روی آخرین تگ‌های release پایدار گرفته شده است. در لحظه‌ی واردکردن هیچ فایلی تغییر نکرده بود و هش درخت Git هر پوشه با تگ upstream یکسان بود. سفارشی‌سازی‌ها از همان نقطه‌ی پایه شروع شده‌اند و در بخش «Local customizations» همین فایل فهرست می‌شوند.

Snapshots of the three official **plugNmeet** core components, taken from the upstream GitHub
repositories at their latest stable release tags (imported 2026-11-16, **unmodified**). Everything we
changed afterwards is listed under [Local customizations](#local-customizations).

The layout follows the official development guide
(<https://www.plugnmeet.org/docs/developer-guide/setup-development>), so
`server/docker-compose_sample.yaml` — which mounts `../client` and `../recording_files` — works as-is.

| Directory   | Upstream repository                                                              | Release               | Commit (GitHub-verified)                   | Git tree hash (pristine import)            |
|-------------|----------------------------------------------------------------------------------|-----------------------|--------------------------------------------|--------------------------------------------|
| `server/`   | [mynaparrot/plugNmeet-server](https://github.com/mynaparrot/plugNmeet-server)     | `v2.5.7` (2026-11-03) | `f84dd8b0144c64543ed0735daf19b6b6ec10c9a8` | `be88fa02ca358d4423898afe9b9f3e2c8925322e` |
| `client/`   | [mynaparrot/plugNmeet-client](https://github.com/mynaparrot/plugNmeet-client)     | `v2.5.6` (2026-11-03) | `3f56d49936eebe2a49605878000cdb480c028f96` | `06988764c494732fbcb3a62861465794bfb02975` |
| `recorder/` | [mynaparrot/plugNmeet-recorder](https://github.com/mynaparrot/plugNmeet-recorder) | `v2.3.2` (2026-10-30) | `aaaf9ba29bebd9c53798a659640fb59eea0b2704` | `b73d9bcb15943f81f6785bac997adc7714ecd073` |

* The three releases form a matching set: all use the plugNmeet protocol **1.6.3**
  (`plugnmeet-protocol` in the `go.mod` of server and recorder, `plugnmeet-protocol-js` in the client's `package.json`).
* License: MIT — every directory keeps its own `LICENSE`.
* **Integrity proof.** At import time `git rev-parse <import-commit>:plugnmeet/server` was equal to
  the upstream `git rev-parse v2.5.7^{tree}` (same for client and recorder). A Git tree hash covers every
  path, every byte and every executable bit, so the copies were identical to the official tags.

## Local customizations

Differences from the official releases, newest last. Policy: use the platform's own configuration
first and touch the source only where there is no configuration option.

| Component | Change                                                                                                                                                                            | Files                                                                                                                                                                                   |
|-----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `client/` | Persian (`fa-IR`) translation completed: the 67 keys missing upstream (random participant picker, *Export Hub*, chat/whiteboard texts) were added, 93 % → 100 %. Nothing existing was changed. | `src/assets/locales/fa-IR/translation.json`, test `src/helpers/__tests__/faLocale.test.ts`                                                                                              |
| `client/` | Persian is the default UI language (right-to-left is automatic). The browser language is ignored on the first visit; a language picked by the user or `?lng=xx-XX` still wins. Override with `defaultLanguage` in `config.js`, e.g. `'en'`. | new `src/helpers/languageDetector.ts`, `src/helpers/i18n.ts`, `src/assets/config_sample.js`, test `src/helpers/__tests__/languageDetector.test.ts`                                       |

Notes for maintainers:

* Persian is the default and English is the i18next fallback, so a key missing from `fa-IR` would show
  English text inside the Persian UI. `pnpm test` in `client/` fails and lists the keys if that happens —
  typically right after merging a newer upstream release that added English strings.
* Two translated strings still contain the product name *plugNmeet* because the English originals do:
  `export-hub.chat-export-title` and `export-hub.polls-export-title` (revisit when branding the product).
* `login.html` of the client is an upstream **developer test page** (it asks for the API key and secret in
  the browser); it is still English and is not meant for end users.

## Run it (from the official guide)

```bash
# 1) Backend + LiveKit, NATS, Redis, MariaDB
cd plugnmeet/server
cp config_sample.yaml config.yaml
cp livekit_sample.yaml livekit.yaml
cp ingress_sample.yaml ingress.yaml
cp nats_server_sample.conf nats_server.conf
cp docker-compose_sample.yaml docker-compose.yaml
docker-compose up --build

# 2) Web client (dev server: http://localhost:3000/login.html) — Node >= 22.12 and pnpm
cd plugnmeet/client
cp src/assets/config_sample.js src/assets/config.js
pnpm install && pnpm start

# 3) Optional: recorder (MP4 recording / RTMP streaming)
cd plugnmeet/recorder
cp config_sample.yaml config.yaml
cp docker-compose_sample.yaml docker-compose.yaml
docker-compose up --build
```

The real `config.yaml`, `livekit.yaml`, `config.js`, … contain secrets and are already git-ignored by the
upstream `.gitignore` files; `plugnmeet/.gitignore` additionally ignores `recording_files/`.
Instead of building the client you can let the server download the official prebuilt client at start-up
(`client.auto_client_download` in `server/config_sample.yaml`).

## See what you have customized

```bash
git clone --depth 1 --branch v2.5.7 https://github.com/mynaparrot/plugNmeet-server /tmp/pnm-server
diff -ru --exclude=.git /tmp/pnm-server plugnmeet/server
```

Same idea for the client (`plugNmeet-client`, tag `v2.5.6`) and the recorder (`plugNmeet-recorder`, tag `v2.3.2`).
The result must match the table under [Local customizations](#local-customizations) — keep that table up
to date whenever you change something.

## Update to a newer official release

Recommended: update the three components together and keep the protocol version identical across them
(this is how the current set was released).

```bash
# in a clone of the upstream repo (example tags)
git diff v2.5.7 v2.6.0 > /tmp/server-upgrade.patch
# in this repository (use --directory=plugnmeet/client or plugnmeet/recorder for the others)
git apply --3way --directory=plugnmeet/server /tmp/server-upgrade.patch
```

Lines you customized that upstream also changed are reported as ordinary merge conflicts.

## Intentionally not included

* Build outputs attached to upstream releases (`client.zip`, recorder binaries): they are reproducible from
  source, and the server can fetch the client itself (see above).
* Dependencies: Go modules (`go.sum`) and npm packages (`pnpm-lock.yaml`) are resolved at build time,
  exactly as upstream does.
* Other upstream projects that are separate repositories: `plugnmeet-protocol` (pinned to 1.6.3 by the
  server/recorder/client), `plugNmeet-install` (installer script), SDKs and CMS plugins.
