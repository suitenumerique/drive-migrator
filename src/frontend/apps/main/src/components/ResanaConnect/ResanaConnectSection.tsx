'use client';

import { Button, Input } from '@openfun/cunningham-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAPI } from '@/api/fetchApi';

interface Props {
  onConnected: () => void;
}

export const ResanaConnectSection = ({ onConnected }: Props) => {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      const response = await fetchAPI(
        'resana/auth/connect',
        { method: 'POST', body: JSON.stringify({ email, password }) },
        { logoutOn401: false },
      );
      if (response.ok) {
        onConnected();
      } else {
        setError(t('Identifiants invalides, veuillez réessayer.'));
      }
    } catch {
      setError(t('Une erreur est survenue, veuillez réessayer.'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <form onSubmit={(e) => void handleSubmit(e)}>
      {error && <div role="alert">{error}</div>}
      <Input
        label={t('Email')}
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        fullWidth
      />
      <Input
        label={t('Mot de passe')}
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        fullWidth
      />
      <Button type="submit" disabled={isLoading}>
        {t('Se connecter')}
      </Button>
    </form>
  );
};
