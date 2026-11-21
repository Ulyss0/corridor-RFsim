# Feature: drone routes synchronised with the friendly EW schedule

Functional description: what the system does and for whom. How the planner works (algorithm,
RF model) is still open, see the end of this file.

## Problem

Friendly jammers protect units against enemy drones, but they also cut the links of friendly
drones. Today, pilots either take long detours around friendly jamming zones (battery, time) or
negotiate a passage over radio or group chats ("cut your jammer 14:15 to 14:30"), with lost
messages, wrong timings and jammers forgotten off afterwards.

Friendly jammers are not always on: EW units plan cuts (maintenance, relocation, friendly
operations). Nobody uses that schedule to plan drone routes.

## Users

| Role | Needs | Interface |
|---|---|---|
| Drone pilot / crew | A route to the target that keeps the link and saves battery; to know exactly when to cross a friendly zone | ATAK (route, zones, drone, chat) |
| EW operator (jammer crew) | Keep the unit protected; know which friendly drone crosses when; a reminder to restart | ATAK chat, web screen |
| Mission planner / commander | One picture of drone routes, jamming zones and the cut schedule; approve routes | Web planning screen |

## Inputs

- **Missions**: launch point, pilot position, target, departure time; several at once.
- **Drone profile**: speed, battery capacity, power use (cruise, hover), reserve, flight altitude,
  control and video links (bands, power, tolerance to jamming).
- **Friendly jammers**: position, bands, power, antenna, operator, and their **schedule**: planned
  cut windows, each one *confirmed* or *pending*.
- **Known threats**: enemy jammers and radars (position, estimated reach), from intelligence.
  Corridor does not detect them.

## What the system does

1. **Plans a route in space and time.** Reaches the target while avoiding radars and jamming zones
   and keeping both links with the pilot. A jamming zone is not a fixed circle: it depends on how
   far the drone is from its pilot, so the system checks the link along the route.
2. **Uses the cut schedule.** If a friendly jammer is cut during a window, the route may cross its
   zone inside that window, timed so the drone enters after the cut and leaves before the restart.
   The route may include a hover or a slightly longer path so the drone reaches the zone exactly
   when it opens.
3. **Compares with the detour.** Every plan shows the route that ignores cuts (all friendly
   jammers on) and the gain: distance, time, battery.
4. **Replans on events.** A cut added, moved, confirmed or cancelled; the drone running late; a new
   threat. The new route is pushed to everyone.
5. **Notifies the right people.**
   - Pilot: route ready, crossing window ("enter not before 14:02:46, exit by 14:05:33"), entering
     and leaving the zone, on target.
   - EW operator: which friendly drone crosses and when, "cut now", "restart now".
6. **Closes the confirmation loop.** A route that relies on a *pending* cut is shown as conditional
   and the EW operator gets a request. Once the operator confirms (web screen or `ACK` in ATAK
   chat), the route becomes valid and is sent to the pilot.

## Safety rules

- **EW protection has priority.** The drone adapts to the jammer schedule; Corridor never switches
  a jammer off or on by itself.
- A pilot is never sent through a friendly zone on the strength of an unconfirmed cut.
- Margins around every cut: enter some time after it starts, leave some time before it ends
  (demo values: 30 s and 60 s).
- If the drone will miss its crossing window (late, slower than planned), the system falls back to
  the route that does not need the cut.
- The jammer restart time never moves because a drone is late.
- A human approves routes; the system recommends.

## What each person sees

Several missions run at once over the same jammers. Each person sees what their role needs,
nothing more; the server filters, so hidden data never reaches a screen.

- **Command view (web)**: every jammer and threat with its nominal footprint (its reach against
  a reference link, independent of any mission), every mission with its route and drone, the
  cut schedule of every jammer. Selecting a mission overlays its own zones.
- **Pilot view (web)**: its mission only, and only the jammers and threats that concern it (zone
  on the mission band touching its area), drawn as they apply to its link. Planned route and
  detour, drone position, timeline of the cuts of those jammers with the drone's crossing on
  it, mission card (distance, time, battery vs detour), launch button, its notifications.
- **EW operator view (web)**: its jammers, their cuts, and the missions that cross them with
  their crossing windows; its notifications (cut requests to confirm, "cut now", "restart now").
- **ATAK (pilot and EW operator phones)**: the same zones and route as map shapes, the drone
  position, and the notifications in chat.

## Demo story (about 3 minutes)

1. Mission: friendly jammer between pilot and target, enemy radar on one side. Route = long detour.
2. The EW schedule shows a cut on that jammer: the system proposes the direct route, timed on the
   cut, with the battery gain. Pilot and EW operator get their messages, on the web and in ATAK.
3. The cut is pending: the EW operator confirms in ATAK chat, the route becomes valid.
4. The drone flies (simulated): the zone turns green during the cut, the drone crosses, the jammer
   restarts on time.
5. Twist: the drone is late, the system switches back to the safe route.

## Out of scope

- Flying the drone (no autopilot link), building drones or jammers.
- Detecting or locating enemy jammers and radars.
- Creating or modifying the EW schedule automatically.

## Open questions (team to decide)

- Planner algorithm and RF / jamming model; whether altitude is part of the search.
- Can the system *request* a new cut from an EW unit, or only use cuts already planned?
- How the pilot follows the route: on screen only, or exported to the ground station
  (e.g. a mission file for QGroundControl / Mission Planner)?
- One drone or several at once (deconflicting drones between themselves).
- Where threat data comes from in a real deployment.
- Scenario format, backend framework and API contract.
