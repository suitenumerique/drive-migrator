const DEFAULT_ADMIN_ORG_SYNC_TIMEOUT_SECONDS = 900;

export const adminOrgSyncTimeoutMs = (): number => {
  const raw = process.env.ADMIN_ORG_SYNC_TIMEOUT_SECONDS;
  const seconds = raw
    ? Number.parseInt(raw, 10)
    : DEFAULT_ADMIN_ORG_SYNC_TIMEOUT_SECONDS;
  if (!Number.isFinite(seconds) || seconds < 1) {
    return DEFAULT_ADMIN_ORG_SYNC_TIMEOUT_SECONDS * 1000;
  }
  return seconds * 1000;
};

export const baseApiUrl = (apiVersion: string = '1.0') => {
  const origin =
    process.env.NEXT_PUBLIC_API_ORIGIN ||
    (typeof window !== 'undefined' ? window.location.origin : '');

  return `${origin}/api/v${apiVersion}/`;
};

export const signalingUrl = (padId: string) => {
  const base =
    process.env.NEXT_PUBLIC_SIGNALING_URL ||
    (typeof window !== 'undefined' ? `wss://${window.location.host}/ws` : '');

  return `${base}/${padId}`;
};
