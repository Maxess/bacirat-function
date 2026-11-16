# plugNmeet — official release snapshot

> **خلاصه:** این پوشه شامل نسخه‌ی رسمی و دست‌نخورده‌ی سه جزء اصلی پلتفرم plugNmeet (سرور، کلاینت و ریکوردر) است که مستقیماً از مخازن رسمی GitHub و از روی آخرین تگ‌های release پایدار گرفته شده است. هیچ فایلی تغییر نکرده و هش درخت Git هر پوشه با تگ upstream یکسان است. تغییرات و سفارشی‌سازی از همین نقطه‌ی پایه شروع می‌شود.

Unmodified snapshots of the three official **plugNmeet** core components, taken from the upstream
GitHub repositories at their latest stable release tags (imported 2026-11-16).

The layout follows the official development guide
(<https://www.plugnmeet.org/docs/developer-guide/setup-development>), so
`server/docker-compose_sample.yaml` — which mounts `../client` and `../recording_files` — works as-is.

| Directory   | Upstream repository                                                              | Release               | Commit (GitHub-verified)                   | Git tree hash                              |
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
