import {
  getConnectPath,
  getMigrationDestination,
  isMigrationTarget,
  needsProConnect,
} from '../migrationTarget';

describe('isMigrationTarget', () => {
  it.each(['lasuite-fichiers', 'archive-zip', 'tribu'])(
    'accepts %s',
    (value) => {
      expect(isMigrationTarget(value)).toBe(true);
    },
  );

  it.each([null, '', 'drive', 'archive', 'toString'])('rejects %s', (value) => {
    expect(isMigrationTarget(value)).toBe(false);
  });
});

describe('needsProConnect', () => {
  it('requires ProConnect for La Suite Fichiers', () => {
    expect(needsProConnect('lasuite-fichiers')).toBe(true);
  });

  it('does not require ProConnect for a zip archive', () => {
    expect(needsProConnect('archive-zip')).toBe(false);
  });
});

describe('getConnectPath', () => {
  it('keeps the chosen target in the connect query', () => {
    expect(getConnectPath('archive-zip')).toBe('/connect?target=archive-zip');
    expect(getConnectPath('lasuite-fichiers')).toBe(
      '/connect?target=lasuite-fichiers',
    );
  });
});

describe('getMigrationDestination', () => {
  it('maps a zip archive to the archive destination', () => {
    expect(getMigrationDestination('archive-zip')).toBe('archive');
  });

  it('maps La Suite Fichiers to Drive', () => {
    expect(getMigrationDestination('lasuite-fichiers')).toBe('drive');
  });

  it('maps Tribu to the tribu destination', () => {
    expect(getMigrationDestination('tribu')).toBe('tribu');
  });
});
