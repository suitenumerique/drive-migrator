'use client';

import { Loader } from '@gouvfr-lasuite/cunningham-react';
import { usePathname, useRouter } from 'next/navigation';
import React, { PropsWithChildren, useEffect, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { User } from '@/core/auth/types';
import { baseApiUrl } from '@/core/conf';
import { MIGRATION_RETURN_STORAGE_KEY } from '@/core/migrationTarget';

import { isPublicPath, normalizePath } from './publicRoutes';

export const logout = () => {
  window.location.replace(new URL('logout/', baseApiUrl()).href);
};

export const login = (returnPath?: string) => {
  if (returnPath) {
    sessionStorage.setItem(MIGRATION_RETURN_STORAGE_KEY, returnPath);
  }
  window.location.replace(new URL('authenticate/', baseApiUrl()).href);
};

interface AuthContextInterface {
  user?: User;
  isAuthenticated: boolean;
  isAuthPending: boolean;
}

export const AuthContext = React.createContext<AuthContextInterface>({
  isAuthenticated: false,
  isAuthPending: true,
});

export const useAuth = () => React.useContext(AuthContext);

export const Auth = ({ children }: PropsWithChildren) => {
  const pathname = usePathname();
  const router = useRouter();
  const publicRoute = isPublicPath(pathname);
  const [user, setUser] = useState<User>();
  const [isAuthPending, setIsAuthPending] = useState(true);

  useEffect(() => {
    const init = async () => {
      setIsAuthPending(true);
      setUser(undefined);

      const response = await fetchAPI(`users/me/`, undefined, {
        logoutOn401: false,
      });

      if (!response.ok) {
        if (!publicRoute) {
          login(pathname);
          return;
        }
        setIsAuthPending(false);
        return;
      }

      const data = (await response.json()) as User;
      setUser(data);
      setIsAuthPending(false);
    };

    void init();
  }, [pathname, publicRoute]);

  useEffect(() => {
    if (isAuthPending || !user) {
      return;
    }

    const returnPath = sessionStorage.getItem(MIGRATION_RETURN_STORAGE_KEY);
    if (!returnPath) {
      return;
    }

    sessionStorage.removeItem(MIGRATION_RETURN_STORAGE_KEY);

    // Post-OIDC callback only: avoid hijacking in-app navigation (e.g. /connect-resana).
    if (normalizePath(pathname) !== '/') {
      return;
    }

    router.replace(returnPath);
  }, [isAuthPending, user, router, pathname]);

  if (isAuthPending && !publicRoute) {
    return <Loader />;
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: Boolean(user),
        isAuthPending,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
