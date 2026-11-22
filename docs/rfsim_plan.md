# Corridor-RFsim: build plan against the code

This fork turns Corridor into a simulation sandbox: a headless engine, a realistic urban RF
environment, mission planners per mission and a coordinator across them. The full plan (features
F0.1 to F5.8, relays R1 to R5, data ingestion D1 to D7, navigation N1 to N4, milestones M0 to M8)
lives in the team doc "Corridor — Build Plan". This file says what the code already has and what
is next, milestone by milestone.

## M0 Foundations

| Plan | Feature | In the code | Missing |
|---|---|---|---|
| F0.1 | Message schema | `schema/ws.schema.json`, generated from the command and event models (`domain/commands.py`, `domain/events.py`) | Claims, knowledge and constraints between coordinator and mission planners (needed at M3) |
| F0.2 | Scene builder | Copernicus DEM + OSM buildings raised into it (`domain/terrain.py`, `tools/fetch_buildings.py`) | Export to a Sionna / Mitsuba scene (needed at M1) |
| F0.3 | Scenario format | `domain/scenario.py`: jammers, threats, relays, terrain, pilots, missions, scripted events; command scripts for the human actions (`sim/script.py`) | Nothing |
| F0.4 | Clock and event bus | `engine/clock.py` (injectable wall clock), `Engine.subscribe` | Nothing |
| F0.5 | Logging and replay | Run log in JSON Lines and its reader (`sim/log.py`), `corridor-run summary` | Replay in the developer viewer (F5.8, M1) |
| F0.6 | Headless runner | `corridor-run` (`sim/runner.py`): scenario + command script, no server, plans inline, deterministic | Nothing |
| F0.7 | Role-filtered event stream | `adapters/ws/audience.py`, `hub.py` (WebSocket) | Nothing for M0 |

The engine has no randomness: the same scenario and command script give the same run.

## Order of work

1. Done: F0.5 + F0.6, `corridor-run` (see `CONTRIBUTING.md`).
2. M1: RF service (F1.1 to F1.3) on Sionna RT, scene export (F0.2), link model per band (F2.2),
   developer viewer (F5.8).
3. M2 onwards as in the plan.

## Kept from Corridor

The planner (SIPP, 3D levels), the J/S jamming model, the safety rule and fallback routes, cut
requests, relays, scripted events, the TAK bridge, route export (ATAK, INAV, ArduPilot, with the
3D plan's altitudes) and the web front end are the baseline every new piece is measured against.
