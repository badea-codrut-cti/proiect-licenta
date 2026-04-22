import type { PropsWithChildren } from 'hono/jsx';
import type { ValidatorSession } from '../backend/types';

interface LayoutProps extends PropsWithChildren {
  title?: string;
}

export function Layout({ title = 'CDL Validator', children }: LayoutProps) {
  return (
    <html lang="ro">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{title}</title>
      </head>
      <body class="min-h-screen bg-gray-100">
        {children}
      </body>
    </html>
  );
}

export function CenteredLayout({ children }: PropsWithChildren) {
  return (
    <div class="min-h-screen bg-gray-100 flex items-center justify-center p-4">
      {children}
    </div>
  );
}

interface AuthenticatedLayoutProps extends PropsWithChildren {
  title?: string;
  session: ValidatorSession;
}
