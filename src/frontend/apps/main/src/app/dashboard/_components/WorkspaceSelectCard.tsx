'use client';

import { VariantType } from '@gouvfr-lasuite/cunningham-react';
import { useController, useFormContext } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/Badge/Badge';
import { Workspace } from '@/components/Workspace/Workspace';

import './WorkspaceSelectCard.scss';

type WorkspaceSelectCardProps = {
  workspace: Workspace;
  migrated?: boolean;
};

export const WorkspaceSelectCard = ({
  workspace,
  migrated = false,
}: WorkspaceSelectCardProps) => {
  const { t } = useTranslation();
  const { setValue } = useFormContext();
  const { field } = useController({
    name: workspace.id,
    defaultValue: false,
  });

  const checked = Boolean(field.value);
  const showCheck = migrated || checked;

  return (
    <button
      type="button"
      className={[
        'workspace-select-card',
        migrated && 'workspace-select-card--migrated',
        !migrated && checked && 'workspace-select-card--selected',
      ]
        .filter(Boolean)
        .join(' ')}
      disabled={migrated}
      aria-pressed={!migrated && checked}
      onClick={() => {
        if (!migrated) {
          setValue(workspace.id, !checked, { shouldValidate: true });
        }
      }}
    >
      <span
        className={[
          'workspace-select-card__checkbox',
          showCheck && 'workspace-select-card__checkbox--checked',
          migrated && 'workspace-select-card__checkbox--migrated',
        ]
          .filter(Boolean)
          .join(' ')}
        aria-hidden
      >
        {showCheck && (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="11"
            height="11"
            viewBox="0 0 11 11"
            fill="none"
          >
            <path
              d="M4.10247 10.5137C3.8116 10.5137 3.56315 10.3844 3.35712 10.1259L0.218152 6.19916C0.141395 6.10624 0.0848369 6.01534 0.0484782 5.92647C0.0161594 5.83759 0 5.74669 0 5.65378C0 5.4437 0.0686775 5.26999 0.206033 5.13263C0.347427 4.99528 0.525181 4.9266 0.739293 4.9266C0.985724 4.9266 1.19378 5.0377 1.36345 5.25989L4.07823 8.74426L9.33812 0.387826C9.43104 0.246431 9.52598 0.147455 9.62293 0.0908967C9.71989 0.0302989 9.84512 0 9.99864 0C10.2087 0 10.3804 0.0666576 10.5137 0.199973C10.647 0.329248 10.7137 0.498922 10.7137 0.708994C10.7137 0.793831 10.6996 0.880688 10.6713 0.969565C10.643 1.0544 10.5986 1.1453 10.538 1.24226L4.84176 10.1198C4.66401 10.3824 4.41758 10.5137 4.10247 10.5137Z"
              fill="#F6F8F9"
              fillOpacity="0.95"
            />
          </svg>
        )}
      </span>
      <span className="workspace-select-card__title">{workspace.title}</span>
      {migrated && workspace.files_limited && (
        <Badge variant={VariantType.WARNING}>{t('Migration partielle')}</Badge>
      )}
      {migrated && (
        <span className="workspace-select-card__badge">{t('Migré')}</span>
      )}
    </button>
  );
};
