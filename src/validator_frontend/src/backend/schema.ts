import { drizzle } from 'drizzle-orm/d1';
import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';

// Problems table - stores the CDL descriptions from images
export const problems = sqliteTable('problems', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  cerinta: text('cerinta').notNull(),
  explicatie: text('explicatie').notNull(),
  instructionCount: integer('instruction_count').notNull(),
  firstValidatorSessionId: text('first_validator_session_id'),
  secondValidatorSessionId: text('second_validator_session_id'),
  cerintaFirstValidator: text('cerinta_first_validator'),
  cerintaSecondValidator: text('cerinta_second_validator'),
});

export const images = sqliteTable('images', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  problemId: integer('problem_id').notNull().references(() => problems.id),
  aiDescription: text('ai_description').notNull(),

  link: text('link').notNull(),
  cropTop: integer('crop_top'),
  cropLeft: integer('crop_left'),
  cropWidth: integer('crop_width'),
  cropHeight: integer('crop_height'),

  firstValidatorApproved: integer('first_validator_approved', { mode: 'boolean' }),
  firstValidatorSessionId: text('first_validator_session_id'),

  aiDescriptionFirstValidator: text('ai_description_first_validator'),
  cropTopFirstValidator: integer('crop_top_first_validator'),
  cropLeftFirstValidator: integer('crop_left_first_validator'),
  cropWidthFirstValidator: integer('crop_width_first_validator'),
  cropHeightFirstValidator: integer('crop_height_first_validator'),

  secondValidatorApproved: integer('second_validator_approved', { mode: 'boolean' }),
  secondValidatorSessionId: text('second_validator_session_id'),

  aiDescriptionSecondValidator: text('ai_description_second_validator'),
  cropTopSecondValidator: integer('crop_top_second_validator'),
  cropLeftSecondValidator: integer('crop_left_second_validator'),
  cropWidthSecondValidator: integer('crop_width_second_validator'),
  cropHeightSecondValidator: integer('crop_height_second_validator'),
});

export type Problem = typeof problems.$inferSelect;
export type Image = typeof images.$inferSelect;

export const schema = { problems, images };

export function createDb(d1: D1Database) {
  return drizzle(d1, { schema });
}

export type Db = ReturnType<typeof createDb>;


export interface ClaimedImageData {
  id: number;
  problemId: number;
  link: string;
  aiDescription: string;
  firstValidatorApproved: boolean | null;
  cerinta: string;
  explicatie: string;
  isCompleted: boolean;
  cropTop: number | null;
  cropLeft: number | null;
  cropWidth: number | null;
  cropHeight: number | null;
}

export interface ValidatorSession {
  sessionId: string;
  validatorType: 'first' | 'second';
  startedAt: number;
  batchType: 'easy' | 'hard';
  sessionCompletedCount: number;

  claimedImageIds: number[];
  claimedImagesData?: ClaimedImageData[];
  totalRemaining?: number;
  claimedAt: number;
}



export interface ValidatorStats {
  claimed: number;
  completed: number;
  totalRemaining: number;
}

export type BatchType = 'easy' | 'hard';
export type ValidatorType = 'first' | 'second';

// Batch assignment based on validator type and difficulty
export interface BatchAssignment {
  sessionId: string;
  imageIds: number[];     // IDs of images assigned to this session
  totalRemaining: number;// Total remaining for this validator type/difficulty
}

export type Env = {
  SESSIONS: KVNamespace;
  DB: D1Database;
  VALIDATOR_PASSWORD: string;
  EASY_MAX_LINES: string;
  EASY_BATCH_SIZE: string;
  HARD_BATCH_SIZE: string;
};
