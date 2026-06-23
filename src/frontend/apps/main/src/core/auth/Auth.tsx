'use client';

import { Loader } from '@gouvfr-lasuite/cunningham-react';
import { usePathname } from 'next/navigation';
import React, { PropsWithChildren, useEffect, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { User } from '@/core/auth/types';
import { baseApiUrl } from '@/core/conf';
import { MIGRATION_RETURN_STORAGE_KEY } from '@/core/migrationTarget';
import { terminateSupportSession } from '@/hooks/useSupport';

import { isPublicPath } from './publicRoutes';

export const logout = () => {
  terminateSupportSession();
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
}

export const AuthContext = React.createContext<AuthContextInterface>({
  isAuthenticated: false,
});

export const useAuth = () => React.useContext(AuthContext);

export const Auth = ({ children }: PropsWithChildren) => {
  const pathname = usePathname();
  const publicRoute = isPublicPath(pathname);
  const [user, setUser] = useState<User>();
  const [isLoading, setIsLoading] = useState(!publicRoute);

  useEffect(() => {
    const init = async () => {
      const response = await fetchAPI(`users/me/`, undefined, {
        logoutOn401: false,
      });

      if (!response.ok) {
        if (!publicRoute) {
          login();
          return;
        }
        setIsLoading(false);
        return;
      }

      const data = (await response.json()) as User;
      setUser(data);
      setIsLoading(false);
    };

    setIsLoading(!publicRoute);
    setUser(undefined);
    void init();
  }, [pathname, publicRoute]);

  if (isLoading) {
    return <Loader />;
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: Boolean(user),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
