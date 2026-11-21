// Friendly names over the generated contract (src/api/ws.gen.ts, from schema/ws.schema.json).
// Regenerate with `npm run gen:types` after the backend changes the schema.
import type * as G from './ws.gen'

export type ServerMessage = G.ServerMessage
export type Payload = G.Payload
export type Command = G.Command
export type Snapshot = G.Snapshot
export type Viewer = G.Viewer
export type Role = G.Role
export type Scenario = G.Scenario
export type Mission = G.Mission6
export type MissionView = G.MissionView
export type MissionConstraints = G.MissionConstraints
export type Grid = G.Grid
export type Zone = G.Zone
export type Position = G.Position
export type Cut = G.Cut2
export type ClockState = G.ClockState
export type MissionStatus = G.MissionView['status']
export type Notification = G.Notification
export type PlanUpdated = G.PlanUpdated
export type RoutePoint = G.RoutePoint
export type DroneState = G.DroneState
