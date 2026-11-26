export function adminOrganizationTarget(
  current: string | null,
  ids: string[],
): string | null {
  if (ids.length === 0 || (current !== null && ids.includes(current))) {
    return null;
  }
  return ids[0];
}
