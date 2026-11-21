// Single client store. It only mirrors what the server says (snapshot + events) plus view
// settings (who we are, which mission and which instant we look at). No business rule lives
// here: the server already filtered what this callsign may see.
import { create } from 'zustand'
import type { ConnStatus } from './api/socket'
import type {
  ClockState,
  Cut,
  MissionView,
  Notification,
  ServerMessage,
  Snapshot,
} from './api/protocol'

export interface FeedItem {
  id: number
  atS: number
  notification: Notification
}

interface State {
  conn: ConnStatus
  /** Callsign we connect as, used as `actor` in commands; empty = not signed in. */
  callsign: string
  /** Why the last sign-in ended (refused callsign), shown on the sign-in screen. */
  signInError: string | null
  seq: number
  snapshot: Snapshot | null
  cuts: Record<string, Cut>
  clock: ClockState
  /** performance.now() when `clock` was last synced, to extrapolate live sim time. */
  clockSyncedAt: number
  /** Missions in view, by id, in snapshot order. */
  missions: Record<string, MissionView>
  /** Mission shown in detail (card, its zones on the map); null = none. */
  selectedMission: string | null
  feed: FeedItem[]
  lastError: string | null
  /** Instant shown on map and timeline; null = follow the live clock. */
  viewTimeS: number | null

  setConn: (conn: ConnStatus) => void
  signIn: (callsign: string) => void
  signOut: (reason: string | null) => void
  apply: (msg: ServerMessage) => void
  selectMission: (id: string | null) => void
  setViewTime: (t: number | null) => void
  setError: (reason: string | null) => void
}

let feedId = 0

const SESSION = {
  seq: 0,
  snapshot: null,
  cuts: {},
  missions: {},
  selectedMission: null,
  feed: [],
  lastError: null,
  viewTimeS: null,
} satisfies Partial<State>

export const useStore = create<State>((set, get) => ({
  ...SESSION,
  conn: 'idle',
  callsign: '',
  signInError: null,
  clock: { sim_time_s: 0, factor: 1, paused: true },
  clockSyncedAt: performance.now(),

  setConn: (conn) => set({ conn }),
  signIn: (callsign) => set({ ...SESSION, callsign, signInError: null }),
  signOut: (reason) => set({ ...SESSION, callsign: '', signInError: reason }),
  selectMission: (selectedMission) => set({ selectedMission }),
  setViewTime: (viewTimeS) => set({ viewTimeS }),
  setError: (lastError) => set({ lastError }),

  apply: (msg) => {
    const p = msg.payload
    // Every envelope carries the server sim time: resync the extrapolated clock on each one.
    const clockSync = {
      clock: { ...get().clock, sim_time_s: msg.sim_time_s },
      clockSyncedAt: performance.now(),
      seq: msg.seq,
    }
    const patchMission = (id: string, patch: Partial<MissionView>) => {
      const current = get().missions[id]
      if (!current) return clockSync // not in view: the server should not have sent it
      return { ...clockSync, missions: { ...get().missions, [id]: { ...current, ...patch } } }
    }
    switch (p.type) {
      case 'snapshot': {
        const missions = Object.fromEntries(p.missions.map((m) => [m.constraints.mission, m]))
        const kept = get().selectedMission
        set({
          ...clockSync,
          snapshot: p,
          cuts: Object.fromEntries(p.cuts.map((c) => [c.id, c])),
          clock: p.clock,
          missions,
          // A pilot has one mission; others start on the first one, and keep their choice on
          // reconnect.
          selectedMission: kept && kept in missions ? kept : (p.missions[0]?.constraints.mission ?? null),
        })
        return
      }
      case 'clock':
        set({ ...clockSync, clock: p.clock })
        return
      case 'cut.scheduled':
      case 'cut.confirmed':
        set({ ...clockSync, cuts: { ...get().cuts, [p.cut.id]: p.cut } })
        return
      case 'cut.cancelled': {
        const cuts = { ...get().cuts }
        delete cuts[p.cut.id]
        set({ ...clockSync, cuts })
        return
      }
      case 'mission.status':
        set(patchMission(p.mission, { status: p.status }))
        return
      case 'notification':
        set({
          ...clockSync,
          feed: [{ id: ++feedId, atS: msg.sim_time_s, notification: p }, ...get().feed].slice(0, 100),
        })
        return
      case 'plan.updated':
        set(patchMission(p.mission, { plan: p }))
        return
      case 'drone.state':
        set(patchMission(p.mission, { drone: p }))
        return
      case 'jammer.restarted':
        // The map derives jammer state from the cuts; the notification reaches the feed.
        set(clockSync)
        return
      case 'ack':
        set({ ...clockSync, lastError: null })
        return
      case 'error':
        set({ ...clockSync, lastError: p.reason })
        return
    }
  },
}))

/** The mission shown in detail, or null. */
export function useSelectedMission(): MissionView | null {
  return useStore((s) => (s.selectedMission ? (s.missions[s.selectedMission] ?? null) : null))
}

/** Live simulated time, extrapolated from the last sync. */
export function liveSimTime(clock: ClockState, syncedAt: number, now = performance.now()): number {
  if (clock.paused) return clock.sim_time_s
  return clock.sim_time_s + ((now - syncedAt) / 1000) * clock.factor
}
