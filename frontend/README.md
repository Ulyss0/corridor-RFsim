# Corridor frontend (first draft)

Planning screen. Stack and rules: root `README.md`, section "Front end". Protocol:
`docs/architecture.md`, section "WebSocket protocol".

```bash
# terminal 1, from backend/
uv run corridor-server
# terminal 2, from frontend/
npm install
npm run dev            # http://localhost:5173, /ws proxied to 127.0.0.1:8000
npm run gen:types      # after the backend regenerates schema/ws.schema.json
npm run build          # type-check + production build in dist/
```

## Layout

```
src/
  api/ws.gen.ts        generated from schema/ws.schema.json, do not edit
  api/protocol.ts      friendly names over the generated types
  api/socket.ts        WebSocket client: ?callsign=, auto-reconnect (not after a 4003 refusal), req_id
  api/client.ts        the app's single connection; signIn/signOut; send() adds the callsign as actor
  store.ts             Zustand store: mirrors snapshot + events per mission, plus view settings
  domain/view.ts       pure helpers: zone state at an instant, drone position on a route, grid bounds
  map/mapView.ts       all MapLibre code, framework-free (setScene, setZoneStates, setRoutes, setDrones)
  hooks/useSimNow.ts   live sim time (extrapolated between server messages), view time
  components/          SignIn, TopBar, MapPanel, MissionsCard, MissionCard, CutsCard, FeedCard,
                       TimelinePanel
  dev/mockPlan.ts      DEV ONLY: fake plan.updated until the planner emits real ones
```

## What works

- Sign in with a callsign (`?as=CMD` in the URL, else the last one used in this browser, else the
  sign-in screen). The server decides the view from it and only sends what that role may see;
  "Switch" signs out. An unknown callsign comes back to the sign-in screen with the reason.
- Command (`CMD`): every mission (list to pick one), every route and drone, nominal footprints of
  every threat (dashed), the selected mission's own zones on top. Pilot: its mission only, with
  the zones that concern it. Jammer operator: its jammers' footprints and the missions they
  concern.
- Map: grids, zones (friendly red / cut green / pending orange, enemy purple, radar orange;
  `forbidden: outside` drawn as the grid with the ring as a hole), launch and target of every
  mission, threats.
- Commands: `clock.set` (start / pause / speed), `cut.schedule` (asked for the selected mission
  when its jammer concerns it), `cut.confirm` (only enabled for the jammer's operator),
  `cut.cancel`, `mission.launch` (shown to the mission pilot and the command). Server errors show
  as an alert.
- Notification feed with the confirm button from `notification.action`.
- Timeline: cuts per jammer in view, one row per drone in view with its route segments (`ground` / `fly` / `hover`), orange cursor on
  live time. Drag the cursor to scrub: map and zones follow; "Back to live" resets.
- `plan.updated`: planned route of every mission in view, detour of the selected one, metrics in
  the mission card. Launch is disabled while
  the plan is a `preview` (cut not confirmed).
- Live drones from `drone.state` (position, battery, delay; the selected one stands out); when
  scrubbing, each drone is placed on its planned route instead. "Hold drone 120 s (demo)" sends `inject.delay` (late-drone twist).
- "Mock plan (dev)" still injects a fake plan locally, for work without the backend planner.

## To do

- Offline basemap (PMTiles extract of the demo area). Today: online OSM raster tiles.
- Backend serves `frontend/dist/` (static mount in `adapters/ws/app.py`).
- Schedule / move cuts by dragging on the timeline (today: the form in the side panel).
- Demo controls: scenario picker, reset, keyboard shortcuts.
- Bundle size: maplibre + vis-timeline + mantine in one 2 MB chunk; split if it matters.
