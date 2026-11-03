export type MigrationTarget = 'lasuite-fichiers' | 'archive-zip' | 'tribu';

export type MigrationDestination = 'archive' | 'drive' | 'tribu';

export const MIGRATION_TARGET_STORAGE_KEY = 'migration_target';
export const MIGRATION_RETURN_STORAGE_KEY = 'migration_return';

const DESTINATIONS: Record<MigrationTarget, MigrationDestination> = {
  'lasuite-fichiers': 'drive',
  'archive-zip': 'archive',
  tribu: 'tribu',
};

export const isMigrationTarget = (
  value: string | null,
): value is MigrationTarget =>
  value !== null && Object.keys(DESTINATIONS).includes(value);

export const needsProConnect = (target: MigrationTarget) =>
  target === 'lasuite-fichiers';

export const getConnectPath = (target: MigrationTarget) =>
  `/connect?target=${target}`;

export const getMigrationDestination = (
  target: MigrationTarget,
): MigrationDestination => DESTINATIONS[target];
