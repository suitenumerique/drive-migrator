'use client';

import {
  Input,
  Loader,
  Pagination,
} from '@gouvfr-lasuite/cunningham-react';
import { Filter } from '@gouvfr-lasuite/ui-kit';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAPI } from '@/api/fetchApi';
import { AdminOrganization } from '@/hooks/useAdminContext';

import './AdminBoard.scss';
import { Selector } from './Selector';
import { StatusBadge } from './StatusBadge';
import {
  EMPTY_FILTERS,
  PAGE_SIZES,
  isListFiltered,
  listQuery,
  nextOrdering,
  showsTarget,
  statusView,
} from './query';
import { KpiFilter, OrganizationStats, WorkspacePage, WorkspaceRow } from './types';

type Props = {
  organizations: AdminOrganization[];
  organizationId: string;
  onOrganizationChange: (id: string) => void;
};

const formatCount = (value: number) =>
  new Intl.NumberFormat('fr-FR').format(value);

const formatDate = (value: string | null, locale: string) => {
  if (!value) {
    return '—';
  }
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(
    new Date(value),
  );
};

const formatDateTime = (value: string, locale: string) =>
  new Intl.DateTimeFormat(locale, {
    dateStyle: 'long',
    timeStyle: 'short',
  }).format(new Date(value));

export const AdminBoard = ({
  organizations,
  organizationId,
  onOrganizationChange,
}: Props) => {
  const { t, i18n } = useTranslation();
  const organization = organizations.find((item) => item.id === organizationId);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [searchInput, setSearchInput] = useState('');
  const [stats, setStats] = useState<OrganizationStats | null>(null);
  const [pageData, setPageData] = useState<WorkspacePage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setFilters(EMPTY_FILTERS);
    setSearchInput('');
  }, [organizationId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setFilters((current) =>
        current.search === searchInput
          ? current
          : { ...current, search: searchInput, page: 1 },
      );
    }, 250);
    return () => clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    void fetchAPI(
      `admin/stats?organization=${encodeURIComponent(organizationId)}`,
    ).then(async (response) => {
      if (!cancelled && response.ok) {
        setStats((await response.json()) as OrganizationStats);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [organizationId, organization?.last_sync]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchAPI(listQuery(organizationId, filters)).then(async (response) => {
      if (cancelled) {
        return;
      }
      if (response.ok) {
        setPageData((await response.json()) as WorkspacePage);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [organizationId, filters]);

  const applyKpi = (filter: KpiFilter) => {
    setSearchInput('');
    setFilters({
      ...EMPTY_FILTERS,
      status: filter.status ?? null,
      target: filter.target ?? null,
      connected: filter.connected ?? null,
      pageSize: filters.pageSize,
    });
    document.getElementById('adm-list')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    });
  };

  const setFilter = (
    key: 'status' | 'target' | 'connected',
    value: string | null,
  ) => {
    setFilters((current) => ({ ...current, [key]: value, page: 1 }));
  };

  const filtered = isListFiltered(filters);
  const count = pageData?.count ?? 0;
  const pages = Math.max(1, Math.ceil(count / filters.pageSize));
  const total = stats?.resana ?? 0;
  const percent = (value: number) =>
    total === 0 ? 0 : Math.round((value / total) * 100);

  const tiles: {
    label: string;
    value: number;
    hint: string;
    filter: KpiFilter;
    tone: string;
  }[] = [
    {
      label: t('Espaces dans Resana'),
      value: total,
      hint: t("Espaces partagés de l'organisation"),
      filter: {},
      tone: 'neutral',
    },
    {
      label: t('Avec un animateur déjà connecté'),
      value: stats?.connected ?? 0,
      hint: t('au migrateur · {{percent}} % des espaces', {
        percent: percent(stats?.connected ?? 0),
      }),
      filter: { connected: 'yes' },
      tone: 'neutral',
    },
    {
      label: t('Non migrés'),
      value: stats?.not_migrated ?? 0,
      hint: t('À migrer ou en échec'),
      filter: { status: 'not_migrated' },
      tone: 'info-fill',
    },
    {
      label: t('Verrouillés dans Resana'),
      value: stats?.locked ?? 0,
      hint: '\u00a0',
      filter: { status: 'LOCKED' },
      tone: 'neutral-fill',
    },
    {
      label: t('Migrés vers Fichiers'),
      value: stats?.drive ?? 0,
      hint: t('{{percent}} % des espaces', {
        percent: percent(stats?.drive ?? 0),
      }),
      filter: { status: 'migrated', target: 'drive' },
      tone: 'success-fill',
    },
    {
      label: t('Migrés en Archive ZIP'),
      value: stats?.archive ?? 0,
      hint: t('{{percent}} % des espaces', {
        percent: percent(stats?.archive ?? 0),
      }),
      filter: { status: 'migrated', target: 'archive' },
      tone: 'success',
    },
    {
      label: t('En cours'),
      value: stats?.pending ?? 0,
      hint: '\u00a0',
      filter: { status: 'PENDING' },
      tone: 'info',
    },
    {
      label: t('En échec'),
      value: stats?.failure ?? 0,
      hint: '\u00a0',
      filter: { status: 'FAILURE' },
      tone: 'danger-fill',
    },
  ];

  const sort = (field: string) => {
    setFilters((current) => ({
      ...current,
      ordering: nextOrdering(current.ordering, field),
      page: 1,
    }));
  };

  return (
    <div className="adm-page">
      <header className="adm-head">
        <div className="adm-head__title">
          <h1 className="adm-h1">{t('Suivi de la migration')}</h1>
          {organization?.last_sync && (
            <p className="adm-muted">
              {t('Données Resana mises à jour le {{date}}', {
                date: formatDateTime(organization.last_sync, i18n.language),
              })}
            </p>
          )}
        </div>
        <div className="adm-head__actions">
          {organizations.length > 1 && (
            <Selector
              id="adm-org"
              title={t('Organisation')}
              icon="corporate_fare"
              value={organizationId}
              options={organizations.map((item) => ({
                value: item.id,
                label: item.name,
              }))}
              onChange={(id) => id && onOrganizationChange(id)}
            />
          )}
        </div>
      </header>

      <section aria-labelledby="adm-kpi-title" className="adm-section">
        <h2 id="adm-kpi-title" className="adm-h2">
          {organization?.name}
        </h2>
        <div className="adm-kpis">
          {tiles.map((tile) => (
            <button
              key={tile.label}
              type="button"
              className={`adm-kpi adm-kpi--${tile.tone}`}
              onClick={() => applyKpi(tile.filter)}
              aria-label={t('{{label}} : {{value}}. Filtrer la liste', {
                label: tile.label,
                value: formatCount(tile.value),
              })}
            >
              <span className="adm-kpi__label">{tile.label}</span>
              <span className="adm-kpi__value">{formatCount(tile.value)}</span>
              <span className="adm-kpi__hint">{tile.hint}</span>
            </button>
          ))}
        </div>
      </section>

      <section id="adm-list" className="adm-section">
        <div className="adm-toolbar">
          <div className="adm-toolbar__search">
            <Input
              id="adm-search"
              fullWidth
              variant="classic"
              hideLabel
              label={t('Rechercher un espace ou un animateur')}
              placeholder={t('Rechercher un espace ou un animateur')}
              icon={<span className="material-icons">search</span>}
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              rightIcon={
                searchInput ? (
                  <button
                    type="button"
                    className="adm-icon-btn"
                    aria-label={t('Effacer la recherche')}
                    onClick={() => setSearchInput('')}
                  >
                    <span className="material-icons">close</span>
                  </button>
                ) : undefined
              }
            />
          </div>
        </div>
        <div className="adm-filters">
          <Filter
            label={t('Statut')}
            options={STATUS_OPTIONS.map((option) => ({
              value: option.value,
              label: t(option.label),
            }))}
            selectedKey={filters.status}
            onSelectionChange={(key) =>
              setFilter('status', key ? String(key) : null)
            }
          />
          <Filter
            label={t('Cible')}
            options={[
              { value: 'drive', label: t('Fichiers') },
              { value: 'archive', label: t('Archive ZIP') },
            ]}
            selectedKey={filters.target}
            onSelectionChange={(key) =>
              setFilter('target', key ? String(key) : null)
            }
          />
          <Filter
            label={t('Animateur déjà connecté')}
            options={[
              { value: 'yes', label: t('Au moins un') },
              { value: 'no', label: t('Aucun') },
            ]}
            selectedKey={filters.connected}
            onSelectionChange={(key) =>
              setFilter('connected', key ? String(key) : null)
            }
          />
          {filtered && (
            <Button
              variant="tertiary"
              icon={<span className="material-icons">undo</span>}
              onClick={() => {
                setSearchInput('');
                setFilters((current) => ({
                  ...EMPTY_FILTERS,
                  pageSize: current.pageSize,
                }));
              }}
            >
              {t('Réinitialiser')}
            </Button>
          )}
        </div>
        <div className="adm-resultline">
          <p aria-live="polite">
            {filtered
              ? t('{{count}} espaces sur {{total}}', {
                  count: formatCount(count),
                  total: formatCount(total),
                })
              : t('{{count}} espaces', { count: formatCount(count) })}
          </p>
          <Selector
            id="adm-page-size"
            title={t('Par page')}
            value={String(filters.pageSize)}
            options={PAGE_SIZES.map((size) => ({
              value: String(size),
              label: String(size),
            }))}
            onChange={(value) =>
              setFilters((current) => ({
                ...current,
                pageSize: Number(value),
                page: 1,
              }))
            }
          />
        </div>
        {loading && !pageData ? (
          <Loader size="medium" />
        ) : (
          <WorkspaceTable
            rows={pageData?.results ?? []}
            ordering={filters.ordering}
            onSort={sort}
            locale={i18n.language}
            emptyLabel={t('Aucun espace ne correspond à ces critères.')}
          />
        )}
        <Pagination
          page={filters.page}
          pageSize={filters.pageSize}
          pagesCount={pages}
          displayGoto
          onPageChange={(page) =>
            setFilters((current) => ({ ...current, page }))
          }
        />
      </section>
    </div>
  );
};

const STATUS_OPTIONS = [
  { value: 'not_migrated', label: 'Non migré' },
  { value: 'NONE', label: 'À migrer' },
  { value: 'PENDING', label: 'En cours' },
  { value: 'migrated', label: 'Migré' },
  { value: 'GAP', label: 'Migré avec écarts' },
  { value: 'FAILURE', label: 'Échec' },
  { value: 'LOCKED', label: 'Verrouillé dans Resana' },
];

const COLUMNS: { field: string; label: string }[] = [
  { field: 'title', label: 'Espace' },
  { field: 'status', label: 'Statut' },
  { field: '', label: 'Cible' },
  { field: 'connected_count', label: 'Animateurs déjà connectés' },
  { field: 'source_files_count', label: 'Fichiers' },
  { field: 'migrated_at', label: 'Migré le' },
];

const WorkspaceTable = ({
  rows,
  ordering,
  onSort,
  locale,
  emptyLabel,
}: {
  rows: WorkspaceRow[];
  ordering: string;
  onSort: (field: string) => void;
  locale: string;
  emptyLabel: string;
}) => {
  const { t } = useTranslation();
  if (rows.length === 0) {
    return <p>{emptyLabel}</p>;
  }
  return (
    <div className="adm-files">
      <table>
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th key={column.label} scope="col">
                {column.field ? (
                  <button type="button" onClick={() => onSort(column.field)}>
                    {t(column.label)}
                    <span className="material-icons" aria-hidden>
                      {ordering === column.field
                        ? 'arrow_upward'
                        : ordering === `-${column.field}`
                          ? 'arrow_downward'
                          : 'unfold_more'}
                    </span>
                  </button>
                ) : (
                  t(column.label)
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const view = statusView(row.status, row.queue);
            const files =
              row.migrated_files_count == null
                ? row.source_files_count == null
                  ? '—'
                  : formatCount(row.source_files_count)
                : `${formatCount(row.migrated_files_count)} / ${formatCount(row.source_files_count ?? 0)}`;
            return (
              <tr key={row.id}>
                <td>
                  <span className="adm-name">
                    <span className="material-icons" aria-hidden>
                      folder
                    </span>
                    <span className="adm-ellipsis">{row.title}</span>
                  </span>
                </td>
                <td>
                  <StatusBadge
                    status={row.status}
                    queue={row.queue}
                    label={t(view.label)}
                  />
                </td>
                <td>
                  {showsTarget(row.status) &&
                    row.target === 'drive' &&
                    t('Fichiers')}
                  {showsTarget(row.status) &&
                    row.target === 'archive' &&
                    t('Archive ZIP')}
                  {(!showsTarget(row.status) || !row.target) && '—'}
                </td>
                <td>
                  <span
                    className={row.connected_count === 0 ? 'adm-warn' : undefined}
                  >
                    {t('{{connected}} sur {{managers}}', {
                      connected: formatCount(row.connected_count),
                      managers: formatCount(row.managers_count),
                    })}
                  </span>
                </td>
                <td>{files}</td>
                <td className="adm-muted">
                  {formatDate(row.migrated_at, locale)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
