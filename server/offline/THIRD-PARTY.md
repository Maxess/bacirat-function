# What is redistributed

- `apt/pool/`: unmodified amd64/all binaries from the signed Ubuntu Noble,
  Noble Updates, and Noble Security archives. `ubuntu-package-lock.tsv` lists
  exact binary versions, SHA-256 and matching source versions. The adjacent
  GitHub Release asset `*-ubuntu-source.tar` provides the corresponding Ubuntu
  source files (`.dsc` plus their archives), including FFmpeg, its packaged
  libraries, and the signed dependency-family companions for older Noble
  hosts. The companion candidates are not unconditional install goals.
  Source files are not needed to install or run the VPS. Package copyright
  notices are installed under `/usr/share/doc/<package>/copyright` per Ubuntu.
  This is not a Canonical or Ubuntu-endorsed distribution.
- `payload/livekit_*`: unmodified LiveKit OSS release archive (Apache-2.0),
  [source](https://github.com/livekit/livekit/tree/v1.13.11).
- `payload/caddy_*`: unmodified standard Caddy release archive, including its
  notices (Apache-2.0), [source](https://github.com/caddyserver/caddy/tree/v2.11.9).
- `payload/chrome-headless-shell-linux64.zip`: Chromium **headless shell only**,
  matched to the Playwright wheel, with its original `LICENSE.headless_shell`
  including third-party notices. No full Chrome or Widevine. [Chromium source](https://chromium.googlesource.com/chromium/src/+/refs/tags/148.0.7778.96).
- `wheels/`: original PyPI wheels with their `.dist-info` licenses, hashes in
  `requirements.lock`. Sources: [Playwright](https://github.com/microsoft/playwright-python/tree/v1.60.0),
  [pyee](https://github.com/jfhbrook/pyee/tree/v13.0.1),
  [greenlet](https://github.com/python-greenlet/greenlet/tree/3.3.2),
  [typing-extensions](https://github.com/python/typing_extensions/tree/4.15.0).
- Frozen Recorder and Renderer archives already distributed by this repository
  include native Go dependency notices, the LiveKit client, PDF.js, and
  Vazirmatn's OFL. The school site's files are a separate ZIP and are not
  installed on the VPS.
