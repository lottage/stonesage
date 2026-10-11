# StoneSage

StoneSage is a platform for running agents on your own machines. An agent here has a *soul* (its persona and the
things it has learned, kept as plain files, independent of whichever model answers), a tool-calling loop that asks
before it does anything it was not told to do, memory, and a trace of every decision so behaviour can be measured
instead of guessed at. It brings a web cockpit and a command-line harness, and it hosts *apps* that give an agent a
job.

## Where it fits

StoneSage is layer 1 of a three-layer pyramid. Dependencies point down only: a layer never imports the one above it.

| Layer | Repository | Job |
| --- | --- | --- |
| 2 | [Hearth](https://github.com/lottage/hearth) | Apps, starting with a Home Assistant house assistant |
| 1 | **StoneSage** (this repository) | Agent platform: souls, tools, approvals, memory |
| 0 | [Lodestone](https://github.com/lottage/lodestone) | Serves the models |

StoneSage imports Lodestone and reaches models over OpenAI-compatible endpoints; it imports nothing from any app. An
app is a package with an `app.py` whose `install(ctx)` registers its routes, tools, hooks and start-up work; which
apps run is a list in `config.json` (`"apps"`), so the same platform runs with Hearth, with another app, or with none.

## What is in it

- `StoneSage/backend/stonesage/`: the server (standard-library HTTP, no framework), the app loader (`apps.py`), the
  soul store (`soul.py`), the tool-calling agent loop with approvals, escalation, memory and trace (`courage/`), the
  tool harness, health probes and first-run setup (`first_run.py`).
- `StoneSage/frontend/`: the web cockpit, a plain-JavaScript progressive web app with no build step.
- `harness/`: the command-line harness and its core libraries (multi-node scheduling, memory fabric, connectors).
- `tests/`: unit tests, route goldens and headless JavaScript tests.

## Install with Docker

`docker build -t ghcr.io/lottage/stonesage:latest .` builds the platform and cockpit image (non-root, state in the
`/config` and `/data` volumes; Lodestone is installed from GitHub, `--build-arg LODESTONE_PIP=...` points elsewhere). The
easiest whole stack is the Compose file that Lodestone's installer writes (`python -m lodestone.installer --docker`):
models, a gateway, and StoneSage with the [Hearth](https://github.com/lottage/hearth) app. Docker runs the CPU profile on
Linux, Windows and macOS; NVIDIA GPUs on Linux and Windows (WSL2); AMD and Intel GPUs on Linux only.

## Install without Docker

StoneSage depends on [Lodestone](https://github.com/lottage/lodestone) (not on PyPI); pip fetches it from GitHub as part
of the install:

```
git clone https://github.com/lottage/stonesage
cd stonesage
pip install -e .
```

## Run

```
python -m stonesage.first_run        # asks for what is missing and writes StoneSage/backend/config.json
python StoneSage/backend/server.py   # cockpit on http://localhost:8888 (config: server.host, server.port)
```

`StoneSage/backend/config.example.json` shows the layout. `config.json` holds secrets and is never committed.

## Tests

```
python tests/run_tests.py
```

The runner is offline by design: it refuses every connection that is not to localhost, and refuses to run network
programs such as `ssh`. The headless JavaScript tests need Node.js (CI uses 20).

## Licence

[The Unlicense](LICENSE): public domain, do what you like.
