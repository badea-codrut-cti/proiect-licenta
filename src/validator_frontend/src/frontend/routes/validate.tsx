import { Hono } from 'hono';
import { requireSession, updateSession } from '../../backend/middleware/session';
import type { AppVariables } from '../../backend/middleware/session';
import type { Env } from '../../backend/schema';
import { AuthenticatedLayout, StatusMessage } from '../components/Layout';
import { eq } from 'drizzle-orm';
import { createDb, schema } from '../../backend/schema';
import { 
  getBatchConfig, 
  getRemainingCount, 
  fetchBatchImagesData, 
  claimBatch 
} from '../../backend/batch-assignment';

const validate = new Hono<{ Bindings: Env; Variables: AppVariables }>();

// Validation page - uses claimed batch from session
validate.get('/', requireSession, async (c) => {
  const session = c.get('session');
  const db = createDb(c.env.DB);
  const config = getBatchConfig(c.env);

  // Get claimed image IDs from session
  const claimedImageIds = session.claimedImageIds;

  if (claimedImageIds.length === 0) {
    return c.render(
      <AuthenticatedLayout session={session}>
        <StatusMessage 
          title="Sesiunea a expirat!"
          message="Te rog să te autentifici din nou pentru a primi imagini."
          type="error"
          form={{ label: "Delogare", action: "/auth/logout" }}
        />
      </AuthenticatedLayout>
    );
  }

  // Get images from session cache if available, else fetch from DB
  let images = session.claimedImagesData;

  if (!images) {
    images = await fetchBatchImagesData(
      db, 
      claimedImageIds, 
      session.validatorType, 
      session.batchType, 
      config.easyMaxLines
    );

    await updateSession(c, { claimedImagesData: images });
  }

  // Filter out completed images client-side
  const incompleteImages = images.filter(img => !img.isCompleted);
  const completedCount = images.length - incompleteImages.length;

  if (incompleteImages.length === 0) {
    return c.render(
      <AuthenticatedLayout session={session}>
        <StatusMessage 
          title="Batch complet!"
          message="Ai validat toate imaginile din acest batch."
          secondaryLink={{ label: "Delogare", href: "/auth/logout" }}
          link={{ label: "Mai multe imagini", href: "/validate/more" }}
        />
      </AuthenticatedLayout>
    );
  }

  const currentImage = incompleteImages[0];

  // Calculate stats
  const totalInBatch = images.length;
  const progressPercent = totalInBatch > 0 ? ((totalInBatch - incompleteImages.length) / totalInBatch) * 100 : 100;

  let totalRemaining = session.totalRemaining;
  if (totalRemaining === undefined) {
    totalRemaining = await getRemainingCount(db, session.validatorType, session.batchType, config.easyMaxLines);
    await updateSession(c, { totalRemaining });
  }

  return c.render(
    <AuthenticatedLayout session={session}>
      <main class="max-w-6xl mx-auto mt-6 p-4">
        {/* Progress */}
        <div class="bg-white rounded-lg shadow p-4 mb-6">
          <div class="flex justify-between mb-2">
            <span class="font-medium">Progres batch ({session.batchType === 'easy' ? 'Uşor' : 'Greu'})</span>
            <span>{completedCount} / {totalInBatch} din batch | {totalRemaining} rămase în total</span>
          </div>
          <div class="w-full bg-gray-200 rounded-full h-3">
            <div class="bg-blue-600 h-3 rounded-full transition-all" style={`width: ${progressPercent}%`}></div>
          </div>
        </div>

        <div
          id="validation-root"
          data-image={JSON.stringify({
            id: currentImage.id,
            problemId: currentImage.problemId,
            cerinta: currentImage.cerinta,
            link: currentImage.link,
            aiDescription: currentImage.aiDescription,
            cropTop: currentImage.cropTop,
            cropLeft: currentImage.cropLeft,
            cropWidth: currentImage.cropWidth,
            cropHeight: currentImage.cropHeight,
          })}
        />
      </main>
    </AuthenticatedLayout>
  );
});

// Submit validation result
validate.post('/submit', requireSession, async (c) => {
  const session = c.get('session');
  const db = createDb(c.env.DB);

  const { imageId, problemId, approved, modifications, cerinta, cropTop, cropLeft, cropWidth, cropHeight } = await c.req.json();

  if (!imageId || !problemId || typeof approved !== 'boolean') {
    return c.json({ error: 'Missing required fields' }, 400);
  }

  // Verify this image belongs to this session's batch
  if (!session.claimedImageIds.includes(imageId)) {
    return c.json({ error: 'Image not in your batch' }, 403);
  }

  const isFirst = session.validatorType === 'first';
  
  const imageUpdateData: any = {
    [isFirst ? 'firstValidatorApproved' : 'secondValidatorApproved']: approved,
    [isFirst ? 'cropTopFirstValidator' : 'cropTopSecondValidator']: cropTop,
    [isFirst ? 'cropLeftFirstValidator' : 'cropLeftSecondValidator']: cropLeft,
    [isFirst ? 'cropWidthFirstValidator' : 'cropWidthSecondValidator']: cropWidth,
    [isFirst ? 'cropHeightFirstValidator' : 'cropHeightSecondValidator']: cropHeight,
    [isFirst ? 'aiDescriptionFirstValidator' : 'aiDescriptionSecondValidator']: modifications,
  };

  const problemUpdateData: any = {
    [isFirst ? 'cerintaFirstValidator' : 'cerintaSecondValidator']: cerinta,
  };

  await db.update(schema.images).set(imageUpdateData).where(eq(schema.images.id, imageId));
  await db.update(schema.problems).set(problemUpdateData).where(eq(schema.problems.id, problemId));

  // Update session cache
  if (session.claimedImagesData) {
    const img = session.claimedImagesData.find(i => i.id === imageId);
    if (img) {
      img.isCompleted = true;
      img.aiDescription = modifications;
      img.cerinta = cerinta;
      img.cropTop = cropTop;
      img.cropLeft = cropLeft;
      img.cropWidth = cropWidth;
      img.cropHeight = cropHeight;
    }
  }
  
  await updateSession(c, {
    claimedImagesData: session.claimedImagesData,
    totalRemaining: session.totalRemaining !== undefined ? Math.max(0, session.totalRemaining - 1) : undefined
  });

  return c.json({ success: true });
});

// Get more images (claim new batch)
validate.get('/more', requireSession, async (c) => {
  const session = c.get('session');
  const sessionId = c.get('sessionId');
  const db = createDb(c.env.DB);
  const config = getBatchConfig(c.env);

  const batchAssignment = await claimBatch(db, sessionId, session.validatorType, session.batchType, config);

  if (batchAssignment.imageIds.length === 0) {
    return c.redirect('/validate');
  }

  // Fetch full image data for caching
  const imagesData = await fetchBatchImagesData(
    db, 
    batchAssignment.imageIds, 
    session.validatorType, 
    session.batchType, 
    config.easyMaxLines
  );

  // Update session
  await updateSession(c, {
    claimedImageIds: batchAssignment.imageIds,
    claimedImagesData: imagesData,
    totalRemaining: batchAssignment.totalRemaining,
    claimedAt: Date.now(),
  });

  return c.redirect('/validate');
});


// Proxy image with CORS headers
validate.get('/image-proxy', requireSession, async (c) => {
  const url = c.req.query('url');
  if (!url) return c.json({ error: 'Missing url' }, 400);
  
  try {
    const response = await fetch(url);
    const data = await response.arrayBuffer();
    const contentType = response.headers.get('content-type') || 'image/jpeg';
    
    return new Response(data, {
      headers: {
        'Content-Type': contentType,
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'public, max-age=86400',
      },
    });
  } catch (error) {
    return c.json({ error: 'Failed to fetch image' }, 500);
  }
});

export default validate;
