'use client';

import {
  Alert,
  Button,
  Loader,
  VariantType,
} from '@gouvfr-lasuite/cunningham-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect } from 'react';
import { useTranslation } from 'react-i18next';

import { useAdminContext } from '@/hooks/useAdminContext';

import { AdminBoard } from './AdminBoard';
import './AdminBoard.scss';

const AdminPageContent = () => {
  const { t } = useTranslation();
  const router = useRouter();
  const params = useSearchParams();
  const organizationId = params.get('organization');
  const { result, loading } = useAdminContext();

  useEffect(() => {
    if (result?.kind !== 'ready' || !result.data.is_organization_admin) {
      return;
    }
    const ids = result.data.organizations.map(
      (organization) => organization.id,
    );
    if (!organizationId || !ids.includes(organizationId)) {
      router.replace(`/admin/?organization=${encodeURIComponent(ids[0])}`);
    }
  }, [organizationId, result, router]);

  if (loading || !result) {
    return (
      <div className="adm-access">
        <Loader size="medium" />
        <p>{t("Vérification de votre rôle d'administrateur…")}</p>
      </div>
    );
  }

  if (result.kind === 'disabled') {
    return (
      <div className="adm-access">
        <Alert type={VariantType.INFO}>
          {t("L'administration n'est pas disponible.")}
        </Alert>
      </div>
    );
  }

  if (result.kind === 'error') {
    return (
      <div className="adm-access">
        <Alert type={VariantType.ERROR}>
          {t(
            'Le service est momentanément indisponible, veuillez réessayer dans quelques instants.',
          )}
        </Alert>
      </div>
    );
  }

  if (!result.data.is_organization_admin) {
    return (
      <div className="adm-access">
        <h1 className="adm-h1">
          {t("Vous n'êtes administrateur d'aucune organisation Resana")}
        </h1>
        <Alert type={VariantType.INFO}>
          {t(
            'Ce compte n’administre aucune organisation. Vous pouvez migrer les espaces dont vous êtes animateur.',
          )}
        </Alert>
        <Button href="/dashboard/" color="brand">
          {t("Migrer les espaces que j'anime")}
        </Button>
      </div>
    );
  }

  if (
    !organizationId ||
    !result.data.organizations.some((item) => item.id === organizationId)
  ) {
    return (
      <div className="adm-access">
        <Loader size="medium" />
      </div>
    );
  }

  return (
    <AdminBoard
      organizations={result.data.organizations}
      organizationId={organizationId}
      onOrganizationChange={(id) =>
        router.replace(`/admin/?organization=${encodeURIComponent(id)}`)
      }
    />
  );
};

export default function AdminPage() {
  return (
    <Suspense
      fallback={
        <div className="adm-access">
          <Loader size="medium" />
        </div>
      }
    >
      <AdminPageContent />
    </Suspense>
  );
}
