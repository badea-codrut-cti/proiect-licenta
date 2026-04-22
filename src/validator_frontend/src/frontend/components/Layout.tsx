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

export function AuthenticatedLayout({ title, session, children }: AuthenticatedLayoutProps) {
  return (
    <Layout title={title}>
      <nav class="bg-white shadow mb-4">
        <div class="max-w-6xl mx-auto px-4 py-3 flex justify-between items-center">
          <span class="font-bold text-lg">CDL Validator</span>
          <div class="flex items-center gap-4">
            <span class="text-sm text-gray-600">
              {session.validatorType === 'first' ? 'Validator 1' : 'Validator 2'} · {session.batchType === 'easy' ? 'Uşor' : 'Greu'}
            </span>
            <a href="/auth/logout" class="text-sm text-red-600 hover:text-red-800">Delogare</a>
          </div>
        </div>
      </nav>
      {children}
    </Layout>
  );
}
