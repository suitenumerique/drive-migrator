export function lastSyncAdvanced(
  before: string | null | undefined,
  after: string | null | undefined,
): boolean {
  if (!after) {
    return false;
  }
  if (!before) {
    return true;
  }
  return new Date(after).getTime() > new Date(before).getTime();
}
