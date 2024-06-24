import { Loader } from '@openfun/cunningham-react';
import { PropsWithChildren, useEffect, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { User } from '@/core/auth/types';
import { baseApiUrl } from '@/core/conf';

export const logout = () => {
  window.location.replace(new URL('logout/', baseApiUrl()).href);
};

export const Auth = ({ children }: PropsWithChildren) => {
  const [user, setUser] = useState<User>();
  console.log('user', user);

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

  return children;
};
