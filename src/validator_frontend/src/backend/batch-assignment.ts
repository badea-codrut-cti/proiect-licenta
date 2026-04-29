import { eq, and, isNull, sql, inArray } from 'drizzle-orm';
import { schema } from './schema';
import type { BatchType, ValidatorType, BatchAssignment, ValidatorStats, Db, ClaimedImageData, Env } from './schema';

export interface BatchAssignmentConfig {
  easyMaxLines: number;
  easyBatchSize: number;
  hardBatchSize: number;
}

/**
 * Parses environment variables into a typed BatchAssignmentConfig.
 */
export function getBatchConfig(env: Env): BatchAssignmentConfig {
  return {
    easyMaxLines: parseInt(env.EASY_MAX_LINES || '10', 10),
    easyBatchSize: parseInt(env.EASY_BATCH_SIZE || '20', 10),
    hardBatchSize: parseInt(env.HARD_BATCH_SIZE || '5', 10),
  };
}

/**
 * Builds the WHERE clause for filtering images by validator type and difficulty.
 */
export function getValidationFilters(
  validatorType: ValidatorType,
  batchType: BatchType,
  easyMaxLines: number,
  sessionId?: string
) {
  const instructionCountFilter = batchType === 'hard'
    ? sql`${schema.problems.instructionCount} > ${easyMaxLines}`
    : sql`${schema.problems.instructionCount} <= ${easyMaxLines}`;

  const isFirst = validatorType === 'first';
  const approvedField = isFirst ? schema.images.firstValidatorApproved : schema.images.secondValidatorApproved;
  const sessionIdField = isFirst ? schema.problems.firstValidatorSessionId : schema.problems.secondValidatorSessionId;

  const filters = [
    isNull(approvedField),
    sessionId
      ? sql`(${sessionIdField} IS NULL OR ${sessionIdField} = ${sessionId})`
      : isNull(sessionIdField),
    instructionCountFilter,
  ];

  if (!isFirst) {
    // Second validator only sees images already processed by the first validator
    filters.push(sql`${schema.images.firstValidatorApproved} IS NOT NULL`);
  }

  return and(...filters);
}

/**
 * Fetches total remaining images to be validated for a specific validator/difficulty.
 */
export async function getRemainingCount(
  db: Db,
  validatorType: ValidatorType,
  batchType: BatchType,
  easyMaxLines: number
): Promise<number> {
  const filters = getValidationFilters(validatorType, batchType, easyMaxLines);

  const result = await db
    .select({ count: sql<number>`count(*)` })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(filters);

  return result[0]?.count || 0;
}

/**
 * Fetches full data for a set of image IDs, formatted for the validation UI.
 */
export async function fetchBatchImagesData(
  db: Db,
  imageIds: number[],
  validatorType: ValidatorType,
  batchType: BatchType,
  easyMaxLines: number
): Promise<ClaimedImageData[]> {
  if (imageIds.length === 0) return [];

  const rows = await db
    .select({
      id: schema.images.id,
      problemId: schema.images.problemId,
      link: schema.images.link,
      firstValidatorApproved: schema.images.firstValidatorApproved,
      secondValidatorApproved: schema.images.secondValidatorApproved,

      cerinta: schema.problems.cerinta,
      explicatie: schema.problems.explicatie,
      aiDescription: schema.images.aiDescription,
      cropTop: schema.images.cropTop,
      cropLeft: schema.images.cropLeft,
      cropWidth: schema.images.cropWidth,
      cropHeight: schema.images.cropHeight,

      cerintaFirstValidator: schema.problems.cerintaFirstValidator,
      aiDescriptionFirstValidator: schema.images.aiDescriptionFirstValidator,
      cropTopFirstValidator: schema.images.cropTopFirstValidator,
      cropLeftFirstValidator: schema.images.cropLeftFirstValidator,
      cropWidthFirstValidator: schema.images.cropWidthFirstValidator,
      cropHeightFirstValidator: schema.images.cropHeightFirstValidator,
    })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(
      and(
        inArray(schema.images.id, imageIds),
        batchType === 'easy'
          ? sql`${schema.problems.instructionCount} <= ${easyMaxLines}`
          : sql`${schema.problems.instructionCount} > ${easyMaxLines}`
      )
    )
    .orderBy(schema.images.id);

  const isFirst = validatorType === 'first';

  return rows.map(row => {
    // Baseline is original data for first validator
    let cerinta = row.cerinta;
    let aiDescription = row.aiDescription;
    let cropTop = row.cropTop;
    let cropLeft = row.cropLeft;
    let cropWidth = row.cropWidth;
    let cropHeight = row.cropHeight;

    // For second validator, use first validator's changes if they exist
    if (!isFirst) {
      cerinta = row.cerintaFirstValidator ?? cerinta;
      aiDescription = row.aiDescriptionFirstValidator ?? aiDescription;
      cropTop = row.cropTopFirstValidator ?? cropTop;
      cropLeft = row.cropLeftFirstValidator ?? cropLeft;
      cropWidth = row.cropWidthFirstValidator ?? cropWidth;
      cropHeight = row.cropHeightFirstValidator ?? cropHeight;
    }

    return {
      id: row.id,
      problemId: row.problemId,
      link: row.link,
      cerinta,
      explicatie: row.explicatie,
      aiDescription,
      cropTop,
      cropLeft,
      cropWidth,
      cropHeight,
      firstValidatorApproved: row.firstValidatorApproved,
      isCompleted: isFirst ? row.firstValidatorApproved !== null : row.secondValidatorApproved !== null,
    };
  });
}

/**
 * Claim images for a validator session.
 */
export async function claimBatch(
  db: Db,
  sessionId: string,
  validatorType: ValidatorType,
  batchType: BatchType,
  config: BatchAssignmentConfig
): Promise<BatchAssignment & { stats: ValidatorStats }> {
  const { easyMaxLines, easyBatchSize, hardBatchSize } = config;
  const batchSize = batchType === 'easy' ? easyBatchSize : hardBatchSize;
  const filters = getValidationFilters(validatorType, batchType, easyMaxLines, sessionId);

  // Count total remaining
  const totalRemaining = await getRemainingCount(db, validatorType, batchType, easyMaxLines);

  // Select unclaimed images to claim
  const unclaimedImages = await db
    .select({ id: schema.images.id, problemId: schema.images.problemId })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(filters)
    .limit(batchSize);

  if (unclaimedImages.length === 0) {
    return { sessionId, imageIds: [], totalRemaining, stats: { claimed: 0, completed: 0, totalRemaining: 0 } };
  }

  const candidateProblemIds = Array.from(new Set(unclaimedImages.map(img => img.problemId)));

  // Claim problems atomically
  const updateField = validatorType === 'first' ? 'firstValidatorSessionId' : 'secondValidatorSessionId';
  await db
    .update(schema.problems)
    .set({ [updateField]: sessionId })
    .where(and(inArray(schema.problems.id, candidateProblemIds), isNull(schema.problems[updateField])));

  // Re-query to get actual claimed problems
  const claimedProblems = await db
    .select({ id: schema.problems.id })
    .from(schema.problems)
    .where(eq(schema.problems[updateField], sessionId));

  if (claimedProblems.length === 0) {
    return { sessionId, imageIds: [], totalRemaining, stats: { claimed: 0, completed: 0, totalRemaining: 0 } };
  }

  const claimedProblemIds = claimedProblems.map(p => p.id);

  // Claim all images for the successfully claimed problems
  await db
    .update(schema.images)
    .set({ [updateField]: sessionId })
    .where(inArray(schema.images.problemId, claimedProblemIds));

  // Get claimed + completed counts
  const sessionIdField = validatorType === 'first' ? schema.images.firstValidatorSessionId : schema.images.secondValidatorSessionId;
  const approvedField = validatorType === 'first' ? schema.images.firstValidatorApproved : schema.images.secondValidatorApproved;

  const allSessionImages = await db
    .select({ id: schema.images.id, approved: approvedField })
    .from(schema.images)
    .where(eq(sessionIdField, sessionId));

  const imageIds = allSessionImages.map(img => img.id);
  const claimed = allSessionImages.length;
  const completed = allSessionImages.filter(img => img.approved !== null).length;

  return {
    sessionId,
    imageIds,
    totalRemaining,
    stats: { claimed, completed, totalRemaining },
  };
}

/**
 * Release all image claims for a session.
 */
export async function releaseSessionClaims(
  db: Db,
  sessionId: string,
  validatorType: ValidatorType
) {
  const sessionIdField = validatorType === 'first' ? schema.images.firstValidatorSessionId : schema.images.secondValidatorSessionId;
  const updateField = validatorType === 'first' ? 'firstValidatorSessionId' : 'secondValidatorSessionId';
  const approvedField = validatorType === 'first' ? schema.images.firstValidatorApproved : schema.images.secondValidatorApproved;

  // Find problems that have incomplete images for this session
  const problemsToRelease = await db
    .select({ problemId: schema.images.problemId })
    .from(schema.images)
    .where(and(eq(sessionIdField, sessionId), isNull(approvedField)));

  const problemIds = Array.from(new Set(problemsToRelease.map(p => p.problemId)));

  if (problemIds.length > 0) {
    // Release problem locks
    await db
      .update(schema.problems)
      .set({ [updateField]: null })
      .where(inArray(schema.problems.id, problemIds));
  }

  // Release image claims
  await db
    .update(schema.images)
    .set({ [updateField]: null })
    .where(
      and(
        eq(sessionIdField, sessionId),
        isNull(approvedField)
      )
    );
}
