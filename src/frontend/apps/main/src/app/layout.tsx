import SubLayout from '@/app/sub-layout';

import './global.scss';

export const metadata = {
  title: 'Osmose Migration Tool',
  description: 'Migrate your Osmose data to a new platform.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <SubLayout>{children}</SubLayout>
      </body>
    </html>
  );
}
