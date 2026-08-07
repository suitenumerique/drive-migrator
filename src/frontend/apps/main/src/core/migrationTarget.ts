export type MigrationTarget = 'lasuite-fichiers' | 'archive-zip';

export const MIGRATION_TARGET_STORAGE_KEY = 'migration_target';
export const MIGRATION_RETURN_STORAGE_KEY = 'migration_return';

export const isMigrationTarget = (
  value: string | null,
): value is MigrationTarget =>
  value === 'lasuite-fichiers' || value === 'archive-zip';

export const needsProConnect = (target: MigrationTarget) =>
  target === 'lasuite-fichiers';

export const getConnectPath = (target: MigrationTarget) =>
  `/connect?target=${target}`;

export const getMigrationDestination = (
  target: MigrationTarget,
): 'archive' | 'drive' => (target === 'archive-zip' ? 'archive' : 'drive');
