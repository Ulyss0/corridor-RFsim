# Screenshots branch (do not merge)

`screenshots/hongkong-3d` exists only to take pictures of Hong Kong in 3D. It is not meant for
`main`: the 3D layers are quick drawings, not functionality.

What the **3D View** button adds here (top-right toolbar):

- the real relief at real scale (exaggeration 1 instead of 2.8; terrain tiles from the internet);
- the 31 395 OSM buildings extruded to their height (brighter = taller);
- the selected mission's jamming zones at every flight level, stacked as translucent slabs:
  the zone as a volume, narrowing or widening with height (friendly red, green while cut);
- the selected route as a band at its real altitude (colour = flight level), with its 2D
  track still drawn on the ground.

Heights of the slabs and of the band are measured above the terrain MapLibre shows, so they are
approximate (good for a picture, not for measuring).

## Run

```powershell
cd backend
uv run corridor-server --scenario scenarios/hongkong.json --cell-m 25 --clock-factor 5
```

```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173/?as=CMD`, select the mission, wait for the route (about 20 s at
25 m), then **3D View**. Hold right-click (or Ctrl + drag) to rotate and tilt.

- **Keep the clock paused until the route is on the map.** At 25 m a plan takes about 18 s;
  with the clock running, the planned take-off may pass before the plan arrives and the server
  plans again.
- `--cell-m`: 25 m is a good trade-off (smoother zones and route than the default 100 m).
  15 m works too (about 70 s per plan). Below 10 m brings nothing: the elevation file is 30 m
  and buildings are rasterised at 10 m; 10 m would take about 4 min per plan, 5 m about 30 min.
- For the cut and the jammer coming back on, use `scenarios/hongkong_scripted.json` (planned
  at 100 m: at 25 m the crossing times shift a little).
