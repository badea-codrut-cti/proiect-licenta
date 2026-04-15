import type { Context, Next } from 'hono';
import type { ValidatorSession } from '../types';
import { getCookie, setCookie, deleteCookie } from 'hono/cookie';

// Define the Env type inline for proper typing
type Env = {
  SESSIONS: KVNamespace;
  DB: D1Database;
  EASY_MAX_LINES: string;
  EASY_BATCH_SIZE: string;
  HARD_BATCH_SIZE: string;
};

type AppContext = Context<{ Bindings: Env; Variables: { session: ValidatorSession; sessionId: string } }>;

const SESSION_COOKIE = 'validator_session';
const SESSION_TTL = 8 * 60 * 60 * 1000; // 8 hours

export async function requireSession(c: AppContext, next: Next) {
  const sessionId = getCookie(c, SESSION_COOKIE);

  if (!sessionId) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  const kv = c.env.SESSIONS;
  const sessionData = await kv.get(sessionId, 'json') as ValidatorSession | null;

  if (!sessionData) {
    deleteCookie(c, SESSION_COOKIE);
    return c.json({ error: 'Session expired' }, 401);
  }

  // Check if session is expired
  if (Date.now() - sessionData.startedAt > SESSION_TTL) {
    await kv.delete(sessionId);
    deleteCookie(c, SESSION_COOKIE);
    return c.json({ error: 'Session expired' }, 401);
  }

  c.set('session', sessionData);
  c.set('sessionId', sessionId);

  await next();
}

export async function createSession(
  c: AppContext,
  validatorType: 'first' | 'second',
  batchType: 'easy' | 'hard'
) {
  const sessionId = crypto.randomUUID();
  const session: ValidatorSession = {
    sessionId,
    validatorType,
    batchType,
    startedAt: Date.now(),
    sessionCompletedCount: 0,
  };

  const kv = c.env.SESSIONS;
  await kv.put(sessionId, JSON.stringify(session), { expirationTtl: SESSION_TTL / 1000 });

  setCookie(c, SESSION_COOKIE, sessionId, {
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
    maxAge: SESSION_TTL / 1000,
    path: '/',
  });

  return session;
}

export async function destroySession(c: AppContext) {
  const sessionId = getCookie(c, SESSION_COOKIE);

  if (sessionId) {
    const kv = c.env.SESSIONS;
    await kv.delete(sessionId);
  }

  deleteCookie(c, SESSION_COOKIE);
}

export async function getSession(c: AppContext): Promise<ValidatorSession | null> {
  const sessionId = getCookie(c, SESSION_COOKIE);

  if (!sessionId) return null;

  const kv = c.env.SESSIONS;
  const sessionData = await kv.get(sessionId, 'json') as ValidatorSession | null;

  if (!sessionData) return null;

  if (Date.now() - sessionData.startedAt > SESSION_TTL) {
    await kv.delete(sessionId);
    deleteCookie(c, SESSION_COOKIE);
    return null;
  }

  return sessionData;
}
