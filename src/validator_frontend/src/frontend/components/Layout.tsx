import type { PropsWithChildren } from 'hono/jsx';
import type { ValidatorSession } from '../backend/types';

interface LayoutProps extends PropsWithChildren {
  title?: string;
}

export function Layout({ title = 'CDL Validator', children }: LayoutProps) {
  return (
    <div class="min-h-screen bg-gray-100">
      <header class="bg-gray-800 text-white p-4 flex justify-between items-center">
        <h1 class="text-xl font-bold">{title}</h1>
      </header>
      {children}
    </div>
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

export function AuthenticatedLayout({ title = 'CDL Validator', session, children }: AuthenticatedLayoutProps) {
  return (
    <div class="min-h-screen bg-gray-100">
      <header class="bg-gray-800 text-white p-4 flex justify-between items-center">
        <h1 class="text-xl font-bold">{title}</h1>
        <div class="flex items-center gap-4">
          <span class={`px-3 py-1 rounded-full text-sm ${session.validatorType === 'first' ? 'bg-blue-500' : 'bg-green-500'}`}>
            {session.validatorType === 'first' ? 'Primul Validator' : 'Al Doilea Validator'}
          </span>
          <span class={`px-3 py-1 rounded-full text-sm ${session.batchType === 'easy' ? 'bg-yellow-500' : 'bg-pink-500'}`}>
            {session.batchType === 'easy' ? 'Ușor' : 'Greu'}
          </span>
          <form action="/auth/logout" method="POST" class="inline">
            <button type="submit" class="bg-gray-600 px-3 py-1 rounded text-sm hover:bg-gray-700">
              Delogare
            </button>
          </form>
        </div>
      </header>
      {children}
    </div>
  );
}
