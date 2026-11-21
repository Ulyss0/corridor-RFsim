// Flight levels in 3D: one colour per level, lowest first, shared by the map and the profile.
export const LEVEL_COLORS = ['#63e6be', '#22b8cf', '#4c6ef5', '#be4bdb', '#f783ac', '#ffd43b']

export function levelColor(index: number): string {
  return LEVEL_COLORS[Math.max(0, index) % LEVEL_COLORS.length]
}

/** Index of ``alt`` among ``levels``, or -1 (on the ground, or not a level). */
export function levelIndex(levels: readonly number[], alt: number | null | undefined): number {
  return alt === null || alt === undefined ? -1 : levels.indexOf(alt)
}
