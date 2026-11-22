# Route export

One flight route (waypoints with altitude and time constraints, `corridor.export.route`), several
destinations. Only a flyable plan is exported: a `preview` plan relies on cuts nobody confirmed.

| Destination | What | Time constraints |
|---|---|---|
| ATAK | native route (`b-m-r`), addressed to the pilot's callsign only, plus the briefing as a GeoChat DM | in the waypoint names: `T/O 14:12:35Z-14:14:00Z`, `WP3 100m HOLD til 14:20:10Z`, `TGT 100m 14:21:37Z` |
| INAV | MW XML `.mission` (INAV Configurator, mwp, ezgui) | none in the firmware: take off on time; a hold is a fixed `POSHOLD_TIME` |
| ArduPilot | QGC WPL 110 `.waypoints` (Mission Planner, QGroundControl) | `NAV_DELAY` until a UTC time: take-off and every hold departure are "not before" times |

- Need to know: the pilot gets the route and its times, never a zone or a jammer.
- Take-off window: when the route crosses a friendly jammer during a cut, a late take-off is as
  unsafe as an early one. The latest take-off comes from the flight safety rule (cut margins kept).
- Altitude, above the launch point (the flight controller's home): a 3D plan gives each
  waypoint its own (its altitude above sea level minus the ground at launch; negative where the
  route flies below the launch point). A 2D plan has none: every waypoint gets the export
  altitude (`altitude_m`, default 100 m), which a 3D plan ignores.
- Both firmwares navigate on GNSS: no use if friendly jamming covers GNSS bands.
- ArduPilot's `NAV_DELAY` needs GPS time and is reported to overrun by up to about a minute: test
  on the target vehicle.

From the running server (front: "Export route" card, for the selected mission; Vite proxies
`/api`). `mission_id` is required:

```bash
curl -OJ "http://127.0.0.1:8000/api/export/inav?mission_id=M1&altitude_m=80"   # M1.mission
curl -OJ "http://127.0.0.1:8000/api/export/ardupilot?mission_id=M1"             # M1.waypoints
curl "http://127.0.0.1:8000/api/export/briefing?mission_id=M1"
curl -X POST "http://127.0.0.1:8000/api/export/atak?mission_id=M1"               # needs --tak-host
```

Offline, without the server (cuts given with `--cut` count as confirmed), files in `export/`:

```bash
uv run corridor-export --cut J3:900:1380
uv run corridor-export --cut J3:900:1380 --altitude 80 --send 192.168.1.20   # also to ATAK
uv run corridor-export --revoke 192.168.1.20                                  # delete it on ATAK
uv run corridor-export --mission M2 --cut J4:900:1380                         # another mission
```

Before the demo, on the real OTS and phone:

1. `uv run corridor-tak route-smoke`: a route addressed to one of two clients must reach only it
   (OTS `<marti><dest>` routing, read in the OTS source, never run on 1.8.3).
2. Draw a route in ATAK, capture it (`corridor-tak listen --capture captures/`) and align
   `corridor.export.atak.route_cot` on it, as for shapes.
3. Open `M1.mission` in INAV Configurator (Mission Control tab, Load mission file) to show it.

Server options: `--tak-host <ip>` and `--tak-port` (default 8088) set the OpenTAKServer the ATAK
export sends to; `--tak-callsign` (default `CORRIDOR`) is our callsign on TAK.
