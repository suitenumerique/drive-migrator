'use client';

import { Button, Input } from '@gouvfr-lasuite/cunningham-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAPI } from '@/api/fetchApi';
import { useAuth } from '@/core/auth/Auth';

import './ResanaConnectSection.scss';

type AuthStep = 'password' | 'otp';

interface Props {
  onConnected: () => void;
  onAuthRequired?: () => void;
  onStepChange?: (step: AuthStep) => void;
}

export const ResanaConnectSection = ({
  onConnected,
  onAuthRequired,
  onStepChange,
}: Props) => {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [step, setStep] = useState<AuthStep>('password');
  const [password, setPassword] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    onStepChange?.(step);
  }, [step, onStepChange]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      const response = await fetchAPI(
        'resana/auth/connect',
        { method: 'POST', body: JSON.stringify({ password }) },
        { logoutOn401: false },
      );
      if (response.ok) {
        const data = await response.json().catch(() => null);
        if (data?.otp_required) {
          setStep('otp');
        } else {
          onConnected();
        }
        return;
      }

      if (response.status === 401) {
        let payload: { error?: string; detail?: string } = {};
        try {
          payload = (await response.json()) as {
            error?: string;
            detail?: string;
          };
        } catch {
          // ignore JSON parse errors
        }

        if (payload.error === 'Authentication failed') {
          setError(t('Identifiants invalides, veuillez réessayer.'));
          return;
        }

        if (onAuthRequired) {
          onAuthRequired();
          return;
        }
      }

      setError(t('Identifiants invalides, veuillez réessayer.'));
    } catch {
      setError(t('Une erreur est survenue, veuillez réessayer.'));
    } finally {
      setIsLoading(false);
    }
  };

  const handleOtpSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      const response = await fetchAPI(
        'resana/auth/otp',
        { method: 'POST', body: JSON.stringify({ code: otpCode }) },
        { logoutOn401: false },
      );
      if (response.ok) {
        onConnected();
      } else if (response.status === 401) {
        setError(t('Code de vérification invalide, veuillez réessayer.'));
      } else if (response.status === 400) {
        setStep('password');
        setError(
          t('La session a expiré, veuillez ressaisir votre mot de passe.'),
        );
      } else {
        setError(t('Une erreur est survenue, veuillez réessayer.'));
      }
    } catch {
      setError(t('Une erreur est survenue, veuillez réessayer.'));
    } finally {
      setIsLoading(false);
    }
  };

  if (step === 'otp') {
    return (
      <form
        className="resana-connect-form"
        onSubmit={(e) => void handleOtpSubmit(e)}
      >
        {error && (
          <p className="resana-connect-form__error" role="alert">
            {error}
          </p>
        )}
        <Input
          className="resana-connect-form__field"
          variant="classic"
          label={t('Code de vérification')}
          type="text"
          inputMode="numeric"
          maxLength={6}
          placeholder={t('Code à 6 chiffres')}
          value={otpCode}
          onChange={(e) => setOtpCode(e.target.value)}
          fullWidth
          required
        />
        <Button
          type="submit"
          fullWidth
          variant="primary"
          color="brand"
          disabled={isLoading}
        >
          {t('Valider')}
        </Button>
      </form>
    );
  }

  return (
    <form
      className="resana-connect-form"
      onSubmit={(e) => void handleSubmit(e)}
    >
      {error && (
        <p className="resana-connect-form__error" role="alert">
          {error}
        </p>
      )}
      <Input
        className="resana-connect-form__field"
        variant="classic"
        label={t('Adresse e-mail')}
        type="email"
        value={user?.email ?? ''}
        fullWidth
        disabled
      />
      <Input
        className="resana-connect-form__field"
        variant="classic"
        label={t('Mot de passe')}
        type="password"
        placeholder={t('Votre mot de passe')}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        fullWidth
        required
      />
      <Button
        type="submit"
        fullWidth
        variant="primary"
        color="brand"
        disabled={isLoading}
      >
        {t('Se connecter')}
      </Button>
    </form>
  );
};
