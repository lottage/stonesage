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
| 2 | [Hearth](https://github.com/lottage/hearth), [Grove](https://github.com/lottage/grove) | Apps: a Home Assistant house assistant; an optional agents add-on |
| 1 | **StoneSage** (this repository) | Agent platform: souls, tools, approvals, memory |
| 0 | [Lodestone](https://github.com/lottage/lodestone) | Serves the models |

StoneSage imports Lodestone and reaches models over OpenAI-compatible endpoints; it imports nothing from any app. An
app is a package with an `app.py` whose `install(ctx)` registers its routes, tools, hooks, start-up work, Prometheus metrics, a scoped
API key (a key that opens exactly the routes the app names) and a cockpit tab; which apps run is a list in `config.json` (`"apps"`),
and an installed package announces itself through the Python entry point group `stonesage.apps`. Installing an app never switches it
on, and with none listed nothing of any app appears in the server or the page (`GET /api/apps` is the manifest the cockpit builds its
app tabs from).

## What is in it

- `StoneSage/backend/stonesage/`: the server (standard-library HTTP, no framework), the app loader (`apps.py`), the
  soul store (`soul.py`), the tool-calling agent loop with approvals, escalation, memory and trace (`courage/`), the
  tool harness, health probes and first-run setup (`first_run.py`).
- `StoneSage/frontend/`: the web cockpit, a plain-JavaScript progressive web app with no build step.
- `harness/`: the command-line harness and its core libraries (multi-node scheduling, memory fabric, connectors).
- `tests/`: unit tests, route goldens and headless JavaScript tests.

## Install with Docker

`docker build -t ghcr.io/lottage/stonesage:0.1.0-rc.5 .` builds the platform and cockpit image (non-root, state in the
`/config` and `/data` volumes; Lodestone is installed from GitHub at the same release, v0.1.0-rc.5; `--build-arg
LODESTONE_PIP=...` points elsewhere). The easiest whole stack is the Compose file that Lodestone's installer writes
(`python -m lodestone.installer --docker`, then `docker compose up -d --build`; the steps and prerequisites are in the
[Hearth README](https://github.com/lottage/hearth)): models, a gateway, and StoneSage with the Hearth app. Docker runs the
CPU profile on Linux, Windows and macOS; NVIDIA GPUs on Linux and Windows (WSL2); AMD and Intel GPUs on Linux only. Docker
needs Buildx and Compose 2.20 or newer (Ubuntu's `docker.io` alone has no Buildx).

## Install without Docker

### What you need first

A clean machine needs these once, before the commands below.

**Python 3.10 or newer with `venv`, and git.** pip fetches the layers from GitHub, so even a pip install needs git. Modern
Linux (Ubuntu 24.04 among them) refuses `pip install` outside a virtual environment, so make one:

- Ubuntu / Debian: `sudo apt install git python3-venv python3-pip`, then
  `python3 -m venv ~/stonesage && . ~/stonesage/bin/activate`
- Windows (PowerShell): install Python 3.10+ and Git for Windows, then
  `py -m venv $HOME\stonesage; & $HOME\stonesage\Scripts\Activate.ps1`
  (if PowerShell refuses the script: `Set-ExecutionPolicy -Scope Process RemoteSigned`, then repeat)
- macOS: `brew install python git` (or the python.org installer), then
  `python3 -m venv ~/stonesage && . ~/stonesage/bin/activate`

StoneSage depends on [Lodestone](https://github.com/lottage/lodestone) (not on PyPI); pip fetches it from GitHub, at the
same release, as part of the install:

```
git clone --branch v0.1.0-rc.5 https://github.com/lottage/stonesage
cd stonesage
pip install -e .
```

## Run

```
python -m stonesage.first_run        # asks for what is missing and writes StoneSage/backend/config.json
python StoneSage/backend/server.py   # cockpit on http://localhost:8888 (config: server.host, server.port)
```

`StoneSage/backend/config.example.json` shows the layout. `config.json` holds secrets and is never committed.

## Backups and upgrades

The server writes a backup of `config.json`, the souls and the small state files every night (config `backup`), keeps the newest
14, and writes one before it migrates an old settings file; `python -m stonesage.backup create | list | restore <file>` does it
by hand. A backup holds your secrets and there is no download route: copy the files off the machine yourself, or let StoneSage
do it after every backup (config `backup.destinations`: a NAS or USB folder, a synced Google Drive / Dropbox folder, or any
`rclone` remote, which must be encrypted unless you say otherwise). Upgrading is
"get the new version and restart" (`docker compose pull && docker compose up -d`, or `git pull`); a settings file written by a
newer version is refused. [docs/UPGRADING.md](docs/UPGRADING.md) covers upgrading, rolling back and moving to another machine.

## Tests

```
python tests/run_tests.py
```

The runner is offline by design: it refuses every connection that is not to localhost, and refuses to run network
programs such as `ssh`. The headless JavaScript tests need Node.js (CI uses 20).

## Licence

[The Unlicense](LICENSE): public domain, do what you like.
