import { Hono } from 'hono';
import { html } from 'hono/html'
import { requireSession } from '../../backend/middleware/session';
import type { ValidatorSession } from '../../backend/types';
import { AuthenticatedLayout } from '../components/Layout';
import { eq, and, inArray, sql } from 'drizzle-orm';
import { createDb, schema } from '../../backend/schema';
import temml from 'temml';

type Variables = {
  session: ValidatorSession;
  sessionId: string;
};

type Env = {
  SESSIONS: KVNamespace;
  DB: D1Database;
  EASY_MAX_LINES: string;
  EASY_BATCH_SIZE: string;
  HARD_BATCH_SIZE: string;
};

const validate = new Hono<{ Bindings: Env; Variables: Variables }>();

// Render math using temml - extracts $...$ and $$...$$ for rendering
function renderMathSafe(text: string): string {
  // Pattern to match math delimiters: $$...$$ (display) or $...$ (inline)
  const mathPattern = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;
  
  try {
    return text.replace(mathPattern, (match, displayMath, inlineMath) => {
      let math = displayMath || inlineMath;
      
      // Pre-process: replace \mathrm{X} with just X (temml doesn't parse it properly)
      // Also handle \text{X}, \textbf{X}, etc.
      math = math.replace(/\\(?:mathrm|text|textbf|textit|mathbf|mathsf|rm|bf|it|tt)\{([^}]+)\}/g, '$1');
      
      const isDisplay = !!displayMath;
      try {
        const rendered = temml.renderToString(math, { 
          displayMode: isDisplay 
        });
        return rendered;
      } catch {
        // If rendering fails, return original match
        return match;
      }
    });
  } catch {
    return text;
  }
}

// Validation page - uses claimed batch from session
validate.get('/', requireSession, async (c) => {
  const session = c.get('session');
  const db = createDb(c.env.DB);

  const easyMaxLines = parseInt(c.env.EASY_MAX_LINES || '10', 10);

  // Get claimed image IDs from session
  const claimedImageIds = session.claimedImageIds || [];

  if (claimedImageIds.length === 0) {
    return c.render(
      <AuthenticatedLayout session={session}>
        <div class="bg-white rounded-lg shadow p-8 text-center max-w-2xl mx-auto">
          <h2 class="text-2xl font-bold mb-4">Sesiunea a expirat!</h2>
          <p class="text-gray-600 mb-6">Te rog să te autentifici din nou pentru a primi imagini.</p>
          <form action="/auth/logout" method="post">
            <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700">Delogare</button>
          </form>
        </div>
      </AuthenticatedLayout>
    );
  }

  // Get images for this session, ordered by ID for consistency
  const images = await db
    .select({
      id: schema.images.id,
      problemId: schema.images.problemId,
      link: schema.images.link,
      aiDescription: schema.images.aiDescription,
      firstValidatorApproved: schema.images.firstValidatorApproved,
      firstValidatorModifications: schema.images.firstValidatorModifications,
      cerinta: schema.problems.cerinta,
      explicatie: schema.problems.explicatie,
      isCompleted: session.validatorType === 'first'
        ? sql`${schema.images.firstValidatorApproved} IS NOT NULL`
        : sql`${schema.images.secondValidatorApproved} IS NOT NULL`,
    })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(
      and(
        inArray(schema.images.id, claimedImageIds),
        // Filter by easy/hard based on session's batchType
        session.batchType === 'easy'
          ? sql`${schema.problems.instructionCount} <= ${easyMaxLines}`
          : sql`${schema.problems.instructionCount} > ${easyMaxLines}`
      )
    )
    .orderBy(schema.images.id);

  // Filter out completed images client-side (from the claimed batch)
  const incompleteImages = images.filter(img => !img.isCompleted);
  const completedCount = images.length - incompleteImages.length;

  if (incompleteImages.length === 0) {
    return c.render(
      <AuthenticatedLayout session={session}>
        <div class="bg-white rounded-lg shadow p-8 text-center max-w-2xl mx-auto">
          <h2 class="text-2xl font-bold mb-4">Batch complet!</h2>
          <p class="text-gray-600 mb-6">Ai validat toate imaginile din acest batch.</p>
          <div class="flex gap-4 justify-center">
            <a href="/auth/logout" class="bg-gray-600 text-white px-6 py-2 rounded-lg hover:bg-gray-700">Delogare</a>
            <a href="/validate/more" class="bg-green-600 text-white px-6 py-2 rounded-lg hover:bg-green-700">Mai multe imagini</a>
          </div>
        </div>
      </AuthenticatedLayout>
    );
  }

  const currentImage = incompleteImages[0];

  // Calculate stats
  const totalInBatch = images.length;
  const remainingInBatch = incompleteImages.length;
  const progressPercent = totalInBatch > 0 ? ((totalInBatch - remainingInBatch) / totalInBatch) * 100 : 100;

  // Get total remaining for this validator type/difficulty
  const getRemainingWhereClause = () => {
    if (session.validatorType === 'first') {
      return and(
        sql`${schema.images.firstValidatorApproved} IS NULL`,
        session.batchType === 'easy'
          ? sql`${schema.problems.instructionCount} <= ${easyMaxLines}`
          : sql`${schema.problems.instructionCount} > ${easyMaxLines}`
      );
    } else {
      return and(
        eq(schema.images.firstValidatorApproved, true),
        sql`${schema.images.secondValidatorApproved} IS NULL`,
        session.batchType === 'easy'
          ? sql`${schema.problems.instructionCount} <= ${easyMaxLines}`
          : sql`${schema.problems.instructionCount} > ${easyMaxLines}`
      );
    }
  };

  const totalRemainingResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(getRemainingWhereClause());
  const totalRemaining = totalRemainingResult[0]?.count || 0;

  // Description to show to second validator (first validator's correction or original AI description)
  const description = currentImage.firstValidatorModifications || currentImage.aiDescription;

  return c.render(
    <AuthenticatedLayout session={session}>
      <main class="max-w-6xl mx-auto mt-6 p-4">
        {/* Progress */}
        <div class="bg-white rounded-lg shadow p-4 mb-6">
          <div class="flex justify-between mb-2">
            <span class="font-medium">Progres batch ({session.batchType === 'easy' ? 'Ușor' : 'Greu'})</span>
            <span>{completedCount} / {totalInBatch} din batch | {totalRemaining} rămase în total</span>
          </div>
          <div class="w-full bg-gray-200 rounded-full h-3">
            <div class="bg-blue-600 h-3 rounded-full transition-all" style={`width: ${progressPercent}%`}></div>
          </div>
        </div>

        {/* Problem Statement */}
        <div class="bg-yellow-100 border border-yellow-400 rounded-lg p-4 mb-6">
          <h3 class="font-bold mb-2">Cerință:</h3>
          <pre class="whitespace-pre-wrap" dangerouslySetInnerHTML={{ __html: renderMathSafe(currentImage.cerinta) }} />
        </div>

          {/* Validation Form */}
          <form id="validationForm" class="space-y-6">
            <input type="hidden" name="imageId" value={currentImage.id} />

            <div class="grid md:grid-cols-2 gap-6">
              {/* Image */}
              <div class="bg-white rounded-lg shadow p-4">
                <h3 class="font-bold mb-4">Imagine</h3>
                <img src={currentImage.link} alt="Diagrama" class="w-full border rounded-lg" />
              </div>

              {/* Description Editor */}
              <div class="bg-white rounded-lg shadow p-4">
                <h3 class="font-bold mb-2">Descriere CDL</h3>
                <textarea
                  id="descriptionEditor"
                  name="modifications"
                  rows={12}
                  class="w-full px-4 py-2 border rounded-lg font-mono text-sm"
                  autocomplete="off"
                >{description}</textarea>
              </div>
            </div>

            {/* Submit */}
            <div class="bg-white rounded-lg shadow p-6">
              <div class="flex flex-wrap gap-4">
                <button
                  type="button"
                  onclick="submitValidation(true)"
                  class="flex-1 min-w-48 bg-green-600 text-white py-3 px-6 rounded-lg hover:bg-green-700 transition"
                >
                  ✓ Aprobat
                </button>
                <button
                  type="button"
                  onclick="submitValidation(false)"
                  class="flex-1 min-w-48 bg-yellow-600 text-white py-3 px-6 rounded-lg hover:bg-yellow-700 transition"
                >
                  ⚠ Corectat
                </button>
              </div>
            </div>
          </form>

        {html`<script>
          async function submitValidation(isApproval) {
            const form = document.getElementById('validationForm');
            const formData = new FormData(form);
            const modifications = formData.get('modifications');
            
            if (!modifications || !modifications.trim()) {
              alert('Trebuie să completezi descrierea CDL.');
              return;
            }
            
            const payload = {
              imageId: parseInt(formData.get('imageId')),
              approved: isApproval,
              modifications: modifications
            };
            try {
              const response = await fetch('/validate/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
              });
              if (response.ok) {
                location.reload();
              } else {
                alert('Eroare la trimitere. Te rog să încerci din nou.');
              }
            } catch (err) {
              alert('Eroare de conexiune.');
            }
          }
        </script>`}
      </main>
    </AuthenticatedLayout>
  );
});

// Submit validation result
validate.post('/submit', requireSession, async (c) => {
  const session = c.get('session');
  const db = createDb(c.env.DB);

  const { imageId, approved, modifications } = await c.req.json();

  if (!imageId || typeof approved !== 'boolean') {
    return c.json({ error: 'Missing required fields' }, 400);
  }

  // Verify this image belongs to this session's batch
  const claimedImageIds = session.claimedImageIds || [];
  if (!claimedImageIds.includes(imageId)) {
    return c.json({ error: 'Image not in your batch' }, 403);
  }

  if (session.validatorType === 'first') {
    await db
      .update(schema.images)
      .set({
        firstValidatorApproved: approved,
        ...(approved ? {} : { firstValidatorModifications: modifications || null }),
      })
      .where(eq(schema.images.id, imageId));
  } else {
    await db
      .update(schema.images)
      .set({
        secondValidatorApproved: approved,
        ...(approved ? {} : { secondValidatorModifications: modifications || null }),
      })
      .where(eq(schema.images.id, imageId));
  }

  return c.json({ success: true });
});

// Get more images (claim new batch)
validate.get('/more', requireSession, async (c) => {
  const session = c.get('session');
  const sessionId = c.get('sessionId');
  const db = createDb(c.env.DB);

  const easyMaxLines = parseInt(c.env.EASY_MAX_LINES || '10', 10);
  const easyBatchSize = parseInt(c.env.EASY_BATCH_SIZE || '20', 10);
  const hardBatchSize = parseInt(c.env.HARD_BATCH_SIZE || '5', 10);

  const { claimBatch } = await import('../../backend/batch-assignment');

  const batchAssignment = await claimBatch(
    db,
    sessionId,
    session.validatorType,
    session.batchType,
    { easyMaxLines, easyBatchSize, hardBatchSize }
  );

  if (batchAssignment.imageIds.length === 0) {
    return c.redirect('/validate');
  }

  // Update session with new claimed images
  const kv = c.env.SESSIONS;
  await kv.put(sessionId, JSON.stringify({
    ...session,
    claimedImageIds: batchAssignment.imageIds,
    claimedAt: Date.now(),
  }), { expirationTtl: 8 * 60 * 60 });

  return c.redirect('/validate');
});

export default validate;
