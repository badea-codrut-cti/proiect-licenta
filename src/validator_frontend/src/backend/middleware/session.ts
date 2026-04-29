import type { Context, Next } from 'hono';
import type { ValidatorSession, Env, ValidatorType, BatchType } from '../schema';
import { getCookie, setCookie, deleteCookie } from 'hono/cookie';

export type AppVariables = {
  session: ValidatorSession;
  sessionId: string;
};

export type AppContext = Context<{ Bindings: Env; Variables: AppVariables }>;

const SESSION_COOKIE = 'validator_session';
export const SESSION_TTL_SECONDS = 8 * 60 * 60; // 8 hours
export const SESSION_TTL_MS = SESSION_TTL_SECONDS * 1000;

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
  if (Date.now() - sessionData.startedAt > SESSION_TTL_MS) {
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
  validatorType: ValidatorType,
  batchType: BatchType
) {
  const sessionId = crypto.randomUUID();
  const session: ValidatorSession = {
    sessionId,
    validatorType,
    batchType,
    startedAt: Date.now(),
    sessionCompletedCount: 0,
    claimedImageIds: [],
    claimedAt: 0,
  };

  const kv = c.env.SESSIONS;
  await kv.put(sessionId, JSON.stringify(session), { expirationTtl: SESSION_TTL_SECONDS });

  setCookie(c, SESSION_COOKIE, sessionId, {
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
    maxAge: SESSION_TTL_SECONDS,
    path: '/',
  });

  return session;
}

export async function updateSession(c: AppContext, updates: Partial<ValidatorSession>) {
  const session = c.get('session');
  const sessionId = c.get('sessionId');
  const updatedSession = { ...session, ...updates };
  
  await c.env.SESSIONS.put(sessionId, JSON.stringify(updatedSession), { 
    expirationTtl: SESSION_TTL_SECONDS 
  });
  
  c.set('session', updatedSession);
  return updatedSession;
}


export async function destroySession(c: AppContext) {
  const sessionId = getCookie(c, SESSION_COOKIE);

  if (sessionId) {
    const kv = c.env.SESSIONS;
    await kv.delete(sessionId);
  }

  deleteCookie(c, SESSION_COOKIE);
}
