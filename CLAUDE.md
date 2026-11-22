# Corridor: project context

Team project for the European Defense Tech Hackathon (EDTH) Paris 2026. Software only: the team
does not build drones or other hardware.

## What we are building

4D (space + time) drone route planning synchronised with the friendly EW schedule.

- The planner computes the route that reaches the target while avoiding radars and jamming zones
  and keeping the radio link with the pilot.
- When a friendly jammer has a scheduled cut, the planner recomputes a more direct route that
  crosses the zone during the cut, instead of a long detour that costs battery.
- The pilot and the jammer operator are notified (crossing window, restart time).

Problem behind it: friendly jammers knock out friendly drones (EM fratricide). In Ukraine, pilots
negotiate corridors past friendly jammers over group chats.

Functional spec (users, inputs, behaviour, safety rules, demo story): `docs/feature.md`.

## Decided

- No ATAK plugin (TAK SDK not available). Corridor is an external service speaking CoT to an
  OpenTAKServer (OTS 1.8.3, Docker, `infra/ots/`). ATAK phones connect to the same OTS.
- OTS plain-TCP CoT on port 8088. OTS quirks (registration SA, 1 s settle delay, no echo) are
  documented in `docs/tak.md` and handled by `corridor.tak.client.TakClient`.
- Architecture: web front end ⇄ Python backend (API + simulation clock, planner, TAK bridge)
  ⇄ OTS ⇄ ATAK. The backend owns all state; the web front end never talks to OTS directly.
- Front end: React + Vite + TypeScript, Mantine, MapLibre GL JS (direct, no wrapper), offline
  PMTiles basemap, vis-timeline, Zustand. Details and rules in `frontend/README.md`, section
  "Stack and rules".
- Backend: FastAPI. Commands and state both go over one WebSocket; contract in
  `schema/ws.schema.json`, protocol in `docs/architecture.md`.
- Scenario format and J/S jamming model (free space, circles of Apollonius): see
  `docs/architecture.md`.

## Still open (do not decide these in code without the team)

- Planner choices: implemented, pending team validation. See "Open decisions" in
  `docs/architecture.md`.
- Demo scenario tuning (current one: `backend/scenarios/demo.json`).
- Pitch content.

A standalone working prototype of a planner + CoT output is in `resources/spectrum_sync/`
(reference only, not adopted). Research notes with sources: `resources/research.md`.
`resources/README.md` says what each piece is for.

## Existing solutions (for positioning)

- Ukraine: DELTA and its Mission Control module (manual route planning, zones, coordination with
  EW units). No public sign of automatic routing against the friendly jammer schedule.
- US: EWPMT (Army, brigade-level EW planning), EMBM-J (joint spectrum management), FAAD C2
  (counter-UAS C2). France: CMD3D (3D airspace coordination), PARADE (counter-UAS).
  NATO: spectrum management tools around SMADEF-XML.
- None of them works at the pilot / jammer-operator level, minute by minute.

## Repo conventions

- Python 3.12, uv, project in `backend/`. From `backend/`: `uv run pytest`,
  `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy` (strict).
  Line length 100.
- Package: `backend/src/corridor/`. TAK code in `backend/src/corridor/tak/`. Front end in
  `frontend/` (see `frontend/README.md`). Setup and checks: `CONTRIBUTING.md`.
- After changing a WebSocket message model, regenerate the schema from `backend/`:
  `uv run corridor-schema > ../schema/ws.schema.json` (a test fails otherwise).
- Docs and code comments in English.
