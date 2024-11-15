import { Loader } from '@openfun/cunningham-react';
import React, { PropsWithChildren, useEffect, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { User } from '@/core/auth/types';
import { baseApiUrl } from '@/core/conf';
import { terminateSupportSession } from '@/hooks/useSupport';

export const logout = () => {
  terminateSupportSession();
  window.location.replace(new URL('logout/', baseApiUrl()).href);
};

interface AuthContextInterface {
  user?: User;
}

export const AuthContext = React.createContext<AuthContextInterface>({});

export const useAuth = () => React.useContext(AuthContext);

export const Auth = ({ children }: PropsWithChildren) => {
  const [user, setUser] = useState<User>();

  const init = async () => {
    const response = await fetchAPI(`users/me/`, undefined, {
      logoutOn401: false,
    });
    if (!response.ok) {
      window.location.replace(new URL('authenticate/', baseApiUrl()).href);
      return;
    }
    const data = (await response.json()) as User;
    setUser(data);
  };

  useEffect(() => {
    void init();
  }, []);

  if (!user) {
    return <Loader />;
  }

  return (
    <AuthContext.Provider
      value={{
        user,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
