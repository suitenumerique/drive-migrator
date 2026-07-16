import SubLayout from '@/app/sub-layout';

import './global.scss';

export const metadata = {
  title: 'Migrateur',
  description: 'Migrez vos données Resana.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr">
      <body>
        <SubLayout>{children}</SubLayout>
      </body>
    </html>
  );
}
