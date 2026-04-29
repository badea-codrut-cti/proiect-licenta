import type { PropsWithChildren } from 'hono/jsx';
import type { ValidatorSession } from '../../backend/schema';

interface LayoutProps extends PropsWithChildren {
  title?: string;
}

export function Layout({ children }: LayoutProps) {
  // renderer.tsx handles the <html> shell, so we just return a fragment/div here
  return (
    <div class="min-h-screen bg-gray-100">
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

export function AuthenticatedLayout({ session, children }: AuthenticatedLayoutProps) {
  return (
    <Layout>
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

interface StatusMessageProps {
  title: string;
  message: string;
  type?: 'info' | 'error' | 'success';
  link?: { label: string; href: string };
  form?: { label: string; action: string };
  secondaryLink?: { label: string; href: string };
}

export function StatusMessage({ title, message, type = 'info', link, form, secondaryLink }: StatusMessageProps) {
  const titleColor = type === 'error' ? 'text-red-600' : 'text-gray-800';
  const buttonColor = type === 'error' ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700';

  return (
    <div class="bg-white rounded-lg shadow p-8 text-center max-w-2xl mx-auto">
      <h2 class={`text-2xl font-bold mb-4 ${titleColor}`}>{title}</h2>
      <p class="text-gray-600 mb-6">{message}</p>
      <div class="flex flex-wrap gap-4 justify-center">
        {link && (
          <a href={link.href} class={`${buttonColor} text-white px-6 py-2 rounded-lg transition`}>
            {link.label}
          </a>
        )}
        {form && (
          <form action={form.action} method="post">
            <button type="submit" class={`${buttonColor} text-white px-6 py-2 rounded-lg transition`}>
              {form.label}
            </button>
          </form>
        )}
        {secondaryLink && (
          <a href={secondaryLink.href} class="bg-gray-600 text-white px-6 py-2 rounded-lg hover:bg-gray-700 transition">
            {secondaryLink.label}
          </a>
        )}
      </div>
    </div>
  );
}
