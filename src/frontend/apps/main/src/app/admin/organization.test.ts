import { adminOrganizationTarget } from './organization';

describe('adminOrganizationTarget', () => {
  it('picks the first id when the url is empty or unknown', () => {
    expect(adminOrganizationTarget(null, ['org-a', 'org-b'])).toBe('org-a');
    expect(adminOrganizationTarget('missing', ['org-a', 'org-b'])).toBe(
      'org-a',
    );
  });

  it('stays put when the current id is already valid', () => {
    expect(adminOrganizationTarget('org-b', ['org-a', 'org-b'])).toBeNull();
  });

  it('does not redirect when the list is empty', () => {
    expect(adminOrganizationTarget(null, [])).toBeNull();
    expect(adminOrganizationTarget('org-a', [])).toBeNull();
  });
});
