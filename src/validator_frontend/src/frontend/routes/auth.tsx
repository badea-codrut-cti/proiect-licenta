import { Hono } from 'hono';
import { createSession, destroySession, requireSession } from '../../backend/middleware/session';
import type { ValidatorSession } from '../../backend/types';
import { claimBatch } from '../../backend/batch-assignment';
import { createDb, schema } from '../../backend/schema';
import { CenteredLayout } from '../components/Layout';

type Variables = {
  session: ValidatorSession;
  sessionId: string;
};

type Env = {
  SESSIONS: KVNamespace;
  DB: D1Database;
  VALIDATOR_PASSWORD: string;
  EASY_MAX_LINES: string;
  EASY_BATCH_SIZE: string;
  HARD_BATCH_SIZE: string;
};

const auth = new Hono<{ Bindings: Env; Variables: Variables }>();

// Login page
auth.get('/login', async (c) => {
  return c.render(
    <CenteredLayout>
      <div class="bg-white rounded-lg shadow-lg p-8 w-full max-w-md">
        <h2 class="text-2xl font-bold mb-6 text-center">Autentificare</h2>
        <form action="/auth/login" method="POST">
          <div class="mb-4">
            <label class="block text-gray-700 font-medium mb-2" for="password">Parolă</label>
            <input
              type="password"
              id="password"
              name="password"
              required
              class="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div class="mb-4">
            <label class="block text-gray-700 font-medium mb-2" for="validatorType">Tip Validator</label>
            <select
              id="validatorType"
              name="validatorType"
              required
              class="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="first">Primul Validator</option>
              <option value="second">Al Doilea Validator</option>
            </select>
          </div>
          <div class="mb-6">
            <label class="block text-gray-700 font-medium mb-2" for="batchType">Dificultate</label>
            <select
              id="batchType"
              name="batchType"
              required
              class="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="easy">Ușor (max {c.env.EASY_BATCH_SIZE || 20} imagini)</option>
              <option value="hard">Greu (max {c.env.HARD_BATCH_SIZE || 5} imagini)</option>
            </select>
          </div>
          <button
            type="submit"
            class="w-full bg-blue-600 text-white py-2 px-4 rounded-lg hover:bg-blue-700 transition"
          >
            Autentificare
          </button>
        </form>
      </div>
    </CenteredLayout>
  );
});

// Handle login POST
auth.post('/login', async (c) => {
  const body = await c.req.formData();
  const password = body.get('password') as string;
  const validatorType = body.get('validatorType') as 'first' | 'second';
  const batchType = body.get('batchType') as 'easy' | 'hard';

  if (password !== c.env.VALIDATOR_PASSWORD) {
    return c.render(
      <CenteredLayout>
        <div class="bg-white rounded-lg shadow-lg p-8 w-full max-w-md text-center">
          <p class="text-red-600 font-medium mb-4">Parolă incorectă!</p>
          <a href="/auth/login" class="text-blue-600 hover:underline">Încearcă din nou</a>
        </div>
      </CenteredLayout>
    );
  }

  const db = createDb(c.env.DB);
  const easyMaxLines = parseInt(c.env.EASY_MAX_LINES || '10', 10);
  const easyBatchSize = parseInt(c.env.EASY_BATCH_SIZE || '20', 10);
  const hardBatchSize = parseInt(c.env.HARD_BATCH_SIZE || '5', 10);

  // Create session first
  const session = await createSession(c, validatorType, batchType);

  // Claim a batch of images atomically
  const batchAssignment = await claimBatch(db, session.sessionId, validatorType, batchType, {
    easyMaxLines,
    easyBatchSize,
    hardBatchSize,
  });

  // Update session with claimed image IDs
  const kv = c.env.SESSIONS;
  await kv.put(session.sessionId, JSON.stringify({
    ...session,
    claimedImageIds: batchAssignment.imageIds,
    claimedAt: Date.now(),
  }), { expirationTtl: 8 * 60 * 60 });

  if (batchAssignment.imageIds.length === 0) {
    return c.render(
      <CenteredLayout>
        <div class="bg-white rounded-lg shadow-lg p-8 w-full max-w-md text-center">
          <h2 class="text-xl font-bold mb-4">Nu mai sunt imagini disponibile!</h2>
          <p class="text-gray-600 mb-6">Nu există imagini de validat pentru acest tip de validator și dificultate.</p>
          <form action="/auth/logout" method="POST">
            <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700">Delogare</button>
          </form>
        </div>
      </CenteredLayout>
    );
  }

  return c.redirect('/validate');
});

// Logout
auth.get('/logout', requireSession, async (c) => {
  const session = c.get('session');
  const db = createDb(c.env.DB);

  // Release claimed images so they can be picked up by other validators
  const { releaseSessionClaims } = await import('../../backend/batch-assignment');
  await releaseSessionClaims(db, session.sessionId, session.validatorType);

  await destroySession(c);
  return c.redirect('/auth/login');
});

export default auth;
