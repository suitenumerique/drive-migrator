import {
  EMPTY_FILTERS,
  STATUS_OPTIONS,
  isListFiltered,
  listQuery,
  nextOrdering,
  statusView,
} from './query';

describe('statusView', () => {
  it('shows a queued workspace as waiting, and a running one as in progress', () => {
    expect(statusView('PENDING', 'queued').label).toBe("En file d'attente");
    expect(statusView('PENDING', 'running').label).toBe('En cours');
    expect(statusView('NONE', null).label).toBe('À migrer');
    expect(statusView('LOCKED', null).label).toBe('Verrouillé dans Resana');
  });
});

describe('listQuery', () => {
  it('sends the organization, the page and only the filters that are set', () => {
    const query = listQuery('org-1', {
      ...EMPTY_FILTERS,
      search: 'vélo',
      status: 'FAILURE',
      page: 2,
    });

    expect(query).toContain('organization=org-1');
    expect(query).toContain('search=v%C3%A9lo');
    expect(query).toContain('status=FAILURE');
    expect(query).toContain('page=2');
    expect(query).toContain('page_size=50');
    expect(query).not.toContain('target=');
    expect(query).not.toContain('connected=');
  });
});

describe('isListFiltered', () => {
  it('is false until a search or a filter is set', () => {
    expect(isListFiltered(EMPTY_FILTERS)).toBe(false);
    expect(isListFiltered({ ...EMPTY_FILTERS, connected: 'yes' })).toBe(true);
  });
});

describe('STATUS_OPTIONS', () => {
  it('does not offer the locked filter while the tile stays N/A', () => {
    expect(STATUS_OPTIONS.map((option) => option.value)).not.toContain(
      'LOCKED',
    );
    expect(statusView('LOCKED', null).label).toBe('Verrouillé dans Resana');
  });
});

describe('nextOrdering', () => {
  it('flips the current column and otherwise sorts it ascending', () => {
    expect(nextOrdering('title', 'title')).toBe('-title');
    expect(nextOrdering('-title', 'status')).toBe('status');
  });
});
