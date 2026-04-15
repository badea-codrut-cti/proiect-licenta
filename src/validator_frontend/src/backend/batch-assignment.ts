import { eq, and, isNull, sql, inArray } from 'drizzle-orm';
import { schema } from './schema';
import type { BatchType, ValidatorType, BatchAssignment, ValidatorStats } from './types';

export interface BatchAssignmentConfig {
  easyMaxLines: number;
  easyBatchSize: number;
  hardBatchSize: number;
}

/**
 * Claim images for a validator session.
 * - First validators: unvalidated images (firstValidatorApproved IS NULL)
 * - Second validators: images that first validator processed (firstValidatorApproved IS NOT NULL)
 * - Easy: instructionCount <= EASY_MAX_LINES
 * - Hard: instructionCount > EASY_MAX_LINES
 */
export async function claimBatch(
  db: any,
  sessionId: string,
  validatorType: ValidatorType,
  batchType: BatchType,
  config: BatchAssignmentConfig
): Promise<BatchAssignment & { stats: ValidatorStats }> {
  const { easyMaxLines, easyBatchSize, hardBatchSize } = config;
  const batchSize = batchType === 'easy' ? easyBatchSize : hardBatchSize;
  const isHard = batchType === 'hard';
  const instructionCountFilter = isHard
    ? sql`${schema.problems.instructionCount} > ${easyMaxLines}`
    : sql`${schema.problems.instructionCount} <= ${easyMaxLines}`;

  // Build WHERE clause based on validator type
  const getWhereClause = () => {
    const baseFilters = [instructionCountFilter];

    if (validatorType === 'first') {
      // First validator: unvalidated images
      return and(
        isNull(schema.images.firstValidatorApproved),
        isNull(schema.images.firstValidatorSessionId),
        ...baseFilters
      );
    } else {
      // Second validator: first-processed images (approved or corrected)
      return and(
        sql`${schema.images.firstValidatorApproved} IS NOT NULL`,
        isNull(schema.images.secondValidatorApproved),
        isNull(schema.images.secondValidatorSessionId),
        ...baseFilters
      );
    }
  };

  // Count total remaining
  const totalRemainingResult = await db
    .select({ count: sql<number>`count(*)` })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(getWhereClause());
  const totalRemaining = totalRemainingResult[0]?.count || 0;

  // Select unclaimed images to claim (D1 doesn't support UPDATE ... LIMIT)
  const unclaimedImages = await db
    .select({ id: schema.images.id })
    .from(schema.images)
    .innerJoin(schema.problems, eq(schema.images.problemId, schema.problems.id))
    .where(getWhereClause())
    .limit(batchSize);

  if (unclaimedImages.length === 0) {
    return { sessionId, imageIds: [], totalRemaining, stats: { claimed: 0, completed: 0, totalRemaining: 0 } };
  }

  const imageIds = unclaimedImages.map(img => img.id);

  // Claim images atomically
  if (validatorType === 'first') {
    await db
      .update(schema.images)
      .set({ firstValidatorSessionId: sessionId })
      .where(and(inArray(schema.images.id, imageIds), isNull(schema.images.firstValidatorSessionId)));
  } else {
    await db
      .update(schema.images)
      .set({ secondValidatorSessionId: sessionId })
      .where(and(inArray(schema.images.id, imageIds), isNull(schema.images.secondValidatorSessionId)));
  }

  // Get claimed + completed counts by fetching all images with this sessionId
  const sessionIdField = validatorType === 'first' ? schema.images.firstValidatorSessionId : schema.images.secondValidatorSessionId;
  const approvedField = validatorType === 'first' ? schema.images.firstValidatorApproved : schema.images.secondValidatorApproved;

  const allSessionImages = await db
    .select({ approved: approvedField })
    .from(schema.images)
    .where(eq(sessionIdField, sessionId));

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
 * Release all image claims for a session (when user logs out or session is abandoned)
 */
export async function releaseSessionClaims(
  db: any,
  sessionId: string,
  validatorType: ValidatorType
) {
  const sessionIdField = validatorType === 'first' ? schema.images.firstValidatorSessionId : schema.images.secondValidatorSessionId;

  // Clear session claims from incomplete images
  await db
    .update(schema.images)
    .set({ [validatorType === 'first' ? 'firstValidatorSessionId' : 'secondValidatorSessionId']: null })
    .where(
      and(
        eq(sessionIdField, sessionId),
        // Only clear claims for images that aren't completed
        validatorType === 'first'
          ? isNull(schema.images.firstValidatorApproved)
          : isNull(schema.images.secondValidatorApproved)
      )
    );
}
