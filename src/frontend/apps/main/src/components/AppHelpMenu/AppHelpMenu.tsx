'use client';

import { HelpMenu } from '@gouvfr-lasuite/ui-kit';

import './AppHelpMenu.scss';

const DOCUMENTATION_URL =
  'https://docs.numerique.gouv.fr/docs/542784bf-c713-49ea-bdd4-ec6bcb3a191c/';
const CONTACT_EMAIL = 'support-resana@numerique.gouv.fr';

export const AppHelpMenu = () => (
  <div className="app-help-menu">
    <HelpMenu
      documentationUrl={DOCUMENTATION_URL}
      onContactUs={() => {
        window.location.href = `mailto:${CONTACT_EMAIL}`;
      }}
    />
  </div>
);
