import { lastSyncAdvanced } from './sync';

describe('lastSyncAdvanced', () => {
  it('detects a newer sync timestamp', () => {
    expect(
      lastSyncAdvanced('2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'),
    ).toBe(true);
    expect(
      lastSyncAdvanced('2026-01-02T00:00:00Z', '2026-01-01T00:00:00Z'),
    ).toBe(false);
    expect(lastSyncAdvanced(null, '2026-01-01T00:00:00Z')).toBe(true);
    expect(lastSyncAdvanced('2026-01-01T00:00:00Z', null)).toBe(false);
  });
});
