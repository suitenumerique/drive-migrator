'use client';

import { HelpMenu } from '@gouvfr-lasuite/ui-kit';

import { useMigrationConfig } from '@/hooks/useMigrationConfig';

import './AppHelpMenu.scss';

export const AppHelpMenu = () => {
  const { helpDocumentationUrl, helpContactEmail } = useMigrationConfig();

  return (
    <div className="app-help-menu">
      <HelpMenu
        documentationUrl={helpDocumentationUrl}
        onContactUs={
          helpContactEmail
            ? () => {
                window.location.href = `mailto:${helpContactEmail}`;
              }
            : undefined
        }
      />
    </div>
  );
};
