// The app's single connection to corridor-server, wired to the store.
import { useStore } from '../store'
import type { Command } from './protocol'
import { CorridorSocket, defaultWsUrl } from './socket'

export const socket = new CorridorSocket(defaultWsUrl(), {
  onMessage: (msg) => useStore.getState().apply(msg),
  onStatus: (status) => useStore.getState().setConn(status),
  onRejected: (reason) => useStore.getState().signOut(`Connection refused: ${reason}`),
})

const CALLSIGN_KEY = 'corridor.callsign'

/** Connect as `callsign` and remember it (URL `?as=` and this browser). */
export function signIn(callsign: string): void {
  useStore.getState().signIn(callsign)
  try {
    localStorage.setItem(CALLSIGN_KEY, callsign)
  } catch {
    // storage may be unavailable (private window): the URL still carries the callsign
  }
  const url = new URL(window.location.href)
  url.searchParams.set('as', callsign)
  window.history.replaceState(null, '', url)
  socket.start(callsign)
}

export function signOut(): void {
  socket.stop()
  useStore.getState().signOut(null)
  const url = new URL(window.location.href)
  url.searchParams.delete('as')
  window.history.replaceState(null, '', url)
}

/** Callsign to start with: `?as=` in the URL, else the last one used in this browser. */
export function initialCallsign(): string {
  const fromUrl = new URLSearchParams(window.location.search).get('as')
  if (fromUrl) return fromUrl
  try {
    return localStorage.getItem(CALLSIGN_KEY) ?? ''
  } catch {
    return ''
  }
}

/** Commands that take an actor get the connected callsign. */
type WithoutActor<T> = T extends unknown ? Omit<T, 'actor'> : never

/** `as` overrides the callsign: demo controls act for a given role. */
export function send(command: WithoutActor<Command>, as?: string): void {
  const actor = as ?? useStore.getState().callsign ?? ''
  const reqId = socket.send({ ...command, actor } as Command)
  if (reqId === null) useStore.getState().setError('not connected to corridor-server')
}
