'use client';

import { Modal, ModalSize } from '@gouvfr-lasuite/cunningham-react';
import { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/Button/Button';

import './SharedAccessModal.scss';

export type SharedAccessMode = 'manual' | 'transfer';

type SharedAccessModalProps = {
  isOpen: boolean;
  onClose: () => void;
  onContinue: () => void;
  selectedMode: SharedAccessMode;
  onSelectMode: (mode: SharedAccessMode) => void;
};

const ManualAccessIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    aria-hidden
  >
    <path
      d="M17.2929 5.29289C17.6834 4.90237 18.3164 4.90237 18.707 5.29289C19.0974 5.68342 19.0975 6.31646 18.707 6.70696L13.414 11.9999L18.707 17.2929C19.0974 17.6834 19.0975 18.3165 18.707 18.707C18.3165 19.0975 17.6834 19.0974 17.2929 18.707L11.9999 13.414L6.70696 18.707C6.31646 19.0975 5.68342 19.0974 5.29289 18.707C4.90237 18.3164 4.90237 17.6834 5.29289 17.2929L10.5859 11.9999L5.29289 6.70696C4.90237 6.31643 4.90237 5.68342 5.29289 5.29289C5.68342 4.90237 6.31643 4.90237 6.70696 5.29289L11.9999 10.5859L17.2929 5.29289Z"
      fill="#3E5DE7"
    />
  </svg>
);

const TransferAccessIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
  >
    <path
      d="M15.293 12.2931C15.6835 11.9026 16.3165 11.9026 16.707 12.2931L20.707 16.2931C20.7548 16.341 20.7976 16.3937 20.835 16.4504C20.8597 16.4879 20.8812 16.5268 20.9004 16.5666C20.9222 16.6118 20.9412 16.6585 20.9561 16.7072C20.9623 16.7275 20.9668 16.7482 20.9717 16.7687C20.9893 16.8431 21 16.9204 21 17.0002C21 17.0832 20.9878 17.1633 20.9688 17.2404C20.9646 17.2573 20.9611 17.2744 20.9561 17.2912C20.9412 17.3402 20.9223 17.3873 20.9004 17.4328C20.8804 17.4745 20.8572 17.5148 20.8311 17.5539C20.8185 17.5727 20.8048 17.5907 20.791 17.6086C20.7649 17.6424 20.738 17.6762 20.707 17.7072L16.707 21.7072C16.3165 22.0977 15.6835 22.0977 15.293 21.7072C14.9025 21.3167 14.9025 20.6837 15.293 20.2931L17.5859 18.0002H4C3.44774 18.0002 3.00004 17.5524 3 17.0002C3 16.4479 3.44772 16.0002 4 16.0002H17.5859L15.293 13.7072C14.9025 13.3167 14.9025 12.6837 15.293 12.2931Z"
      fill="#626A80"
    />
    <path
      d="M7.29297 2.29314C7.68349 1.90261 8.31651 1.90261 8.70703 2.29314C9.09752 2.68366 9.09754 3.31669 8.70703 3.7072L6.41406 6.00017H20C20.5523 6.00017 21 6.44788 21 7.00017C21 7.55242 20.5523 8.00017 20 8.00017H6.41406L8.70703 10.2931C9.09752 10.6837 9.09754 11.3167 8.70703 11.7072C8.31652 12.0977 7.68348 12.0977 7.29297 11.7072L3.29297 7.7072C3.26183 7.67606 3.23418 7.64254 3.20801 7.60857C3.19423 7.59066 3.18053 7.57272 3.16797 7.55388C3.14195 7.51489 3.11956 7.47429 3.09961 7.43279C3.07767 7.38721 3.05788 7.34028 3.04297 7.29118C3.0379 7.27446 3.03445 7.25732 3.03027 7.2404C3.01125 7.16336 3.00001 7.08309 3 7.00017C3 6.92049 3.00974 6.84302 3.02734 6.76872C3.03221 6.74817 3.03678 6.72747 3.04297 6.7072C3.05787 6.65845 3.07777 6.61185 3.09961 6.56658C3.11877 6.52678 3.1403 6.48785 3.16504 6.45036C3.20242 6.39371 3.24516 6.34095 3.29297 6.29314L7.29297 2.29314Z"
      fill="#626A80"
    />
  </svg>
);

const AccessOption = ({
  icon,
  title,
  description,
  isSelected,
  isDisabled = false,
  onSelect,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  isSelected: boolean;
  isDisabled?: boolean;
  onSelect: () => void;
}) => (
  <button
    type="button"
    className={[
      'shared-access-modal__option',
      isSelected && 'shared-access-modal__option--selected',
      isDisabled && 'shared-access-modal__option--disabled',
    ]
      .filter(Boolean)
      .join(' ')}
    disabled={isDisabled}
    aria-pressed={isSelected}
    onClick={onSelect}
  >
    <div className="shared-access-modal__option-icon" aria-hidden>
      {icon}
    </div>
    <div className="shared-access-modal__option-text">
      <p className="shared-access-modal__option-title">{title}</p>
      <p className="shared-access-modal__option-description">{description}</p>
    </div>
  </button>
);

export const SharedAccessModal = ({
  isOpen,
  onClose,
  onContinue,
  selectedMode,
  onSelectMode,
}: SharedAccessModalProps) => {
  const { t } = useTranslation();

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('Gérer les accès partagés')}
      size={ModalSize.MEDIUM}
      rightActions={
        <>
          <Button
            variant="bordered"
            color="neutral"
            onClick={onClose}
            fullWidth
          >
            {t('Annuler')}
          </Button>
          <Button
            variant="primary"
            color="brand"
            fullWidth
            onClick={onContinue}
          >
            {t('Continuer')}
          </Button>
        </>
      }
    >
      <div className="shared-access-modal">
        <p className="shared-access-modal__subtitle">
          {t('Choisissez comment reprendre les partages après la migration.')}
        </p>
        <div className="shared-access-modal__options">
          <AccessOption
            icon={<ManualAccessIcon />}
            title={t('Recréer les accès manuellement (recommandé)')}
            description={t(
              'Après la migration, vous ajouterez vous-même les accès aux dossiers et documents.',
            )}
            isSelected={selectedMode === 'manual'}
            onSelect={() => onSelectMode('manual')}
          />
          <AccessOption
            icon={<TransferAccessIcon />}
            title={t('Transférer les accès')}
            description={t(
              "L'outil reprendra les accès existants quand il le peut. À éviter si l'arborescence est complexe : certains accès peuvent être mal appliqués.",
            )}
            isSelected={selectedMode === 'transfer'}
            isDisabled
            onSelect={() => onSelectMode('transfer')}
          />
        </div>
      </div>
    </Modal>
  );
};
