import { Hono } from 'hono';
import { html } from 'hono/html'
import { requireSession } from '../../backend/middleware/session';
import type { ValidatorSession } from '../../backend/types';
import { AuthenticatedLayout } from '../components/Layout';
import { ValidationForm } from '../components/ValidationForm';
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
        // Escape HTML in rendered output (temml outputs < for angles which breaks HTML)
        return rendered.replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
      // Crop data
      cropTop: schema.images.cropTop,
      cropLeft: schema.images.cropLeft,
      cropWidth: schema.images.cropWidth,
      cropHeight: schema.images.cropHeight,
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

  let croppedUrl = currentImage.link;
  if (currentImage.cropTop != null && currentImage.cropLeft != null && 
      currentImage.cropWidth != null && currentImage.cropHeight != null) {
    try {
      const url = new URL(currentImage.link);
      url.searchParams.set('top_left_y', String(currentImage.cropTop));
      url.searchParams.set('top_left_x', String(currentImage.cropLeft));
      url.searchParams.set('width', String(currentImage.cropWidth));
      url.searchParams.set('height', String(currentImage.cropHeight));
      croppedUrl = url.toString();
    } catch {
      // keep original URL
    }
  }

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

        {/* Problem Statement */}
        <div class="bg-yellow-100 border border-yellow-400 rounded-lg p-4 mb-6">
          <h3 class="font-bold mb-2">Cerinţă:</h3>
          <textarea readonly class="w-full bg-transparent border-none resize-none font-mono text-sm" rows={4} style="background: transparent;">{currentImage.cerinta}</textarea>
        </div>

          <ValidationForm image={currentImage as any} />

        {/* Evaluation */}
        <form id="validationForm" class="space-y-6">
          <input type="hidden" name="imageId" value={currentImage.id} />
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
            var recropBtn = document.getElementById('recropBtn');
            var descriptionEditor = document.getElementById('descriptionEditor');
            var modifications = descriptionEditor ? descriptionEditor.value : '';
            var imageId = recropBtn && recropBtn.dataset.imageId;
            
            if (!isApproval && (!modifications || !modifications.trim())) {
              alert('Trebuie să completezi descrierea CDL.');
              return;
            }
            
            var payload = {
              imageId: parseInt(imageId),
              approved: isApproval,
              modifications: modifications
            };
            try {
              var response = await fetch('/validate/submit', {
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

          (function() {
            var recropBtn = document.getElementById('recropBtn');
            var cropModal = document.getElementById('cropModal');
            var closeCropBtn = document.getElementById('closeCropBtn');
            var saveCropBtn = document.getElementById('saveCropBtn');
            var cancelCropBtn = document.getElementById('cancelCropBtn');
            var mainImage = document.getElementById('mainImage');
            var cropInfo = document.getElementById('cropInfo');
            var cropDataSpan = document.getElementById('cropData');
            var cropperLoading = document.getElementById('cropperLoading');

            var imageId = recropBtn && recropBtn.dataset.imageId;

            
            // Get stored crop data (in original image coordinates)
            var cropTop = recropBtn && parseInt(recropBtn.dataset.cropTop) || null;
            var cropLeft = recropBtn && parseInt(recropBtn.dataset.cropLeft) || null;
            var cropWidth = recropBtn && parseInt(recropBtn.dataset.cropWidth) || null;
            var cropHeight = recropBtn && parseInt(recropBtn.dataset.cropHeight) || null;
            
            var originalSrc = recropBtn && recropBtn.dataset.originalSrc;
            
            // Build full uncropped image URL (strip any existing crop params)
            function getFullImageUrl(url) {
              try {
                var urlObj = new URL(url);
                urlObj.searchParams.delete('height');
                urlObj.searchParams.delete('width');
                urlObj.searchParams.delete('top_left_y');
                urlObj.searchParams.delete('top_left_x');
                return urlObj.toString();
              } catch (e) {
                return url;
              }
            }
            
            var fullSrc = getFullImageUrl(originalSrc || '');
            
            // Track state across modal opens
            var isModalOpen = false;
            
            async function openCropModal() {
              if (!cropModal || !fullSrc) return;
              
              isModalOpen = true;
              cropModal.classList.remove('hidden');
              
              var selection = document.getElementById('cropperSelection');
              var cropperImage = document.getElementById('cropperImage');
              
              if (!selection || !cropperImage) return;
              
              // Hide loading indicator initially
              if (cropperLoading) cropperLoading.classList.remove('hidden');
              
              // Wait for image to be ready
              await cropperImage.$ready();

              cropperImage.zoom(0);
              
              if (cropperLoading) cropperLoading.classList.add('hidden');
              
            }

            function closeCropModal() {
              isModalOpen = false;
              if (cropModal) {
                cropModal.classList.add('hidden');
              }
              // Note: No destroy() needed - just hide the modal
              // The web components handle cleanup when hidden
            }
            async function saveCrop() {
              var selection = document.getElementById('cropperSelection');
              var cropperImage = document.getElementById('cropperImage');
              
              if (!selection || !cropperImage) return;
              
              // Get natural image dimensions
              var naturalWidth = cropperImage.naturalWidth;
              var naturalHeight = cropperImage.naturalHeight;
              
              // Calculate scale: cropper is w-full, image fills it, so selection is in natural coords
              
              var crop = {
                left: Math.round(selection.x),
                top: Math.round(selection.y),
                width: Math.round(selection.width),
                height: Math.round(selection.height),
              };

              // Update the main image preview with the new crop
              if (mainImage && originalSrc) {
                try {
                  var cleanUrl = getFullImageUrl(originalSrc);
                  var url = new URL(cleanUrl);
                  url.searchParams.set('top_left_y', String(crop.top));
                  url.searchParams.set('top_left_x', String(crop.left));
                  url.searchParams.set('width', String(crop.width));
                  url.searchParams.set('height', String(crop.height));
                  mainImage.src = '/validate/image-proxy?url=' + encodeURIComponent(url.toString());
                } catch (e) {
                  console.error('Failed to update image URL:', e);
                }
              }
              
              if (cropDataSpan) {
                cropDataSpan.textContent = JSON.stringify(crop);
              }
              if (cropInfo) {
                cropInfo.classList.remove('hidden');
              }
              
              // Update local crop variables for potential re-edit
              cropTop = crop.top;
              cropLeft = crop.left;
              cropWidth = crop.width;
              cropHeight = crop.height;
              
              // Update the recrop button data attributes
              if (recropBtn) {
                recropBtn.dataset.cropTop = String(crop.top);
                recropBtn.dataset.cropLeft = String(crop.left);
                recropBtn.dataset.cropWidth = String(crop.width);
                recropBtn.dataset.cropHeight = String(crop.height);
              }

              try {
                await fetch('/validate/crop', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    imageId: parseInt(imageId),
                    cropTop: crop.top,
                    cropLeft: crop.left,
                    cropWidth: crop.width,
                    cropHeight: crop.height,
                  })
                });
              } catch (e) {
                console.error('Failed to save crop:', e);
              }

              closeCropModal();
            }

            if (recropBtn) recropBtn.addEventListener('click', openCropModal);
            if (closeCropBtn) closeCropBtn.addEventListener('click', closeCropModal);
            if (cancelCropBtn) cancelCropBtn.addEventListener('click', closeCropModal);
            if (saveCropBtn) saveCropBtn.addEventListener('click', saveCrop);

            document.addEventListener('keydown', function(e) {
              if (e.key === 'Escape' && cropModal && !cropModal.classList.contains('hidden')) {
                closeCropModal();
              }
            });
          })();
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

// Save crop data
validate.post('/crop', requireSession, async (c) => {
  const session = c.get('session');
  const db = createDb(c.env.DB);

  const { imageId, cropTop, cropLeft, cropWidth, cropHeight } = await c.req.json();

  if (!imageId) {
    return c.json({ error: 'Missing imageId' }, 400);
  }


  // Verify this image belongs to this session's batch
  const claimedImageIds = session.claimedImageIds || [];
  if (!claimedImageIds.includes(imageId)) {
    return c.json({ error: 'Image not in your batch' }, 403);
  }

  await db
    .update(schema.images)
    .set({
      cropTop: cropTop ?? null,
      cropLeft: cropLeft ?? null,
      cropWidth: cropWidth ?? null,
      cropHeight: cropHeight ?? null,
    })
    .where(eq(schema.images.id, imageId));


  return c.json({ success: true });
});

// Proxy image with CORS headers
validate.get('/image-proxy', async (c) => {
  const url = c.req.query('url');
  if (!url) {
    return c.json({ error: 'Missing url' }, 400);
  }
  
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
