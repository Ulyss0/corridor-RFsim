# Contributing to Corridor

How to set up, run, check and change the code. What Corridor is and how to try it:
[`README.md`](README.md). Front end stack and rules: [`frontend/README.md`](frontend/README.md).
TAK side (OpenTAKServer, CoT client, ATAK): [`docs/tak.md`](docs/tak.md).

## Conventions

- Python 3.12, managed with uv; project in `backend/`, package `backend/src/corridor/`. Line length 100,
  ruff for lint and format, mypy in strict mode.
- After changing a WebSocket message model, regenerate the schema (see "Corridor server" below): a
  test fails otherwise. The front end types are generated from it, never written by hand.
- Docs and code comments in English.

## Requirements

- Docker with the compose plugin, daemon running (`sudo systemctl enable --now docker`, user in
  the `docker` group)
- uv (installs Python 3.12 if missing)
- Node.js LTS (front end development only)

## Corridor server

Python commands run from `backend/`.

```bash
cd backend
uv sync
uv run corridor-server            # ws://127.0.0.1:8000/ws?callsign=..., scenarios/demo.json, clock x20, paused
uv run corridor-server --host 0.0.0.0 --port 8000 --clock-factor 30 --cell-m 200 \
  --scenario scenarios/other.json
uv run corridor-schema > ../schema/ws.schema.json   # after changing a message model
```

- A client connects with its callsign (`/ws?callsign=CMD`, `PILOT-1`, `EW-J3`...). It gets a
  `snapshot` and the events its role may see (command: everything; pilot: its mission; jammer
  operator: its jammers and the missions they concern). A reconnect gets a fresh snapshot.
- The simulation clock starts paused at t = 0; the front starts it with a `clock.set` command.
- Demo: with the demo powers, J3's zone takes about 300 s to cross. A cut shorter than about
  7 min (crossing + 30 s + 60 s of margins) cannot give the straight route.
- Late-drone twist: launch on an approved plan, start the clock, then send `inject.delay`
  (front: "Hold drone 120 s") before the drone reaches J3: it switches to a fallback route.
- State lives in memory: restart the server to reset the scenario.
- `--host 0.0.0.0` exposes the server on the LAN (another laptop, a phone browser).
- A test fails when `schema/ws.schema.json` is out of date: regenerate it with `corridor-schema`.

## Headless runs (`corridor-run`)

The engine without the server: a scenario and a command script (the human actions: cut
requests, confirmations, launches, at given simulated times), faster than real time, with an
event log in JSON Lines.

```bash
cd backend
uv run corridor-run run scenarios/demo.json --script scenarios/scripts/demo_two_flights.json \
  --log run.jsonl                  # stops when every mission has arrived, else at --until-s
uv run corridor-run summary run.jsonl
```

- Script: `{"commands": [{"at_s": 30, "command": {"type": "cut.confirm", "actor": "CMD",
  "cut": "cut-1"}}, ...]}`. Each command is a WebSocket command; cuts are `cut-1`, `cut-2`... in
  the order they are scheduled. Scripts live in `scenarios/scripts/`.
- A refused command is logged with its reason; the run goes on.
- Same scenario, script and options: the same log, byte for byte.
- Options: `--step-s` (5), `--until-s` (3600), `--cell-m` (100).

## Development

```bash
cd backend
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
```

```bash
cd frontend
npm run lint && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs both on every pull request and every push to `main`, and
also fails when `frontend/src/api/ws.gen.ts` is not regenerated from `schema/ws.schema.json`.
Dependabot (`.github/dependabot.yml`) opens one grouped PR a week per ecosystem (actions, uv,
npm).
