'use client';

import {
  Alert,
  Button,
  Loader,
  Modal,
  ModalSize,
  VariantType,
} from '@gouvfr-lasuite/cunningham-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAPI } from '@/api/fetchApi';
import { Badge } from '@/components/Badge/Badge';

import './ManagersModal.scss';
import { ManagersResponse, ManagersTarget } from './types';

type Props = {
  target: ManagersTarget | null;
  onClose: () => void;
};

const formatDate = (value: string, locale: string) =>
  new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(
    new Date(value),
  );

export const ManagersModal = ({ target, onClose }: Props) => {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<ManagersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!target) {
      setData(null);
      setFailed(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    setData(null);
    void fetchAPI(`admin/workspaces/${target.id}/managers`, undefined, {
      logoutOn401: false,
    }).then(async (response) => {
      if (cancelled) {
        return;
      }
      if (response.ok) {
        setData((await response.json()) as ManagersResponse);
      } else {
        setFailed(true);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [target]);

  return (
    <Modal
      isOpen={Boolean(target)}
      onClose={onClose}
      size={ModalSize.MEDIUM}
      title={
        target
          ? t('Animateurs de « {{title}} »', { title: target.title })
          : undefined
      }
      rightActions={
        <Button variant="tertiary" onClick={onClose}>
          {t('Fermer')}
        </Button>
      }
    >
      {loading && <Loader size="medium" />}
      {failed && (
        <Alert type={VariantType.ERROR}>
          {t(
            'Le service est momentanément indisponible, veuillez réessayer dans quelques instants.',
          )}
        </Alert>
      )}
      {data && target && (
        <div className="adm-modal-body">
          <p className="adm-muted">
            {t('{{connected}} sur {{managers}} connecté au migrateur', {
              connected: data.connected_count,
              managers: data.managers_count,
              count: data.connected_count,
            })}
          </p>
          <ul className="adm-people">
            {data.results.map((manager) => (
              <li key={`${manager.email}-${manager.name}`}>
                <div className="adm-people__id">
                  <span>{manager.name}</span>
                  {manager.email && (
                    <span className="adm-small adm-muted">{manager.email}</span>
                  )}
                </div>
                {manager.connected && manager.last_login ? (
                  <Badge variant={VariantType.SUCCESS}>
                    {t('Connecté le {{date}}', {
                      date: formatDate(manager.last_login, i18n.language),
                    })}
                  </Badge>
                ) : (
                  <Badge variant={VariantType.NEUTRAL}>
                    {t('Jamais connecté')}
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
};
