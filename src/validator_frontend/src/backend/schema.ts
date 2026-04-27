import { drizzle } from 'drizzle-orm/d1';
import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';

// Problems table - stores the CDL descriptions from images
export const problems = sqliteTable('problems', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  cerinta: text('cerinta').notNull(),
  explicatie: text('explicatie').notNull(),
  instructionCount: integer('instruction_count').notNull(),
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

  secondValidatorApproved: integer('second_validator_approved', { mode: 'boolean' }),
  secondValidatorSessionId: text('second_validator_session_id'),
});

export type Problem = typeof problems.$inferSelect;
export type Image = typeof images.$inferSelect;

export const schema = { problems, images };

export function createDb(d1: D1Database) {
  return drizzle(d1, { schema });
}

export interface ValidatorSession {
  sessionId: string;
  validatorType: 'first' | 'second';
  startedAt: number;
  batchType: 'easy' | 'hard';
  sessionCompletedCount: number;
  
  claimedImageIds: number[];
  claimedAt: number;
}

export interface ImageForValidation {
  id: number;
  problemId: number;
  link: string;
  description: string;
  cerinta: string;
  explicatie: string;
}

export interface ValidationResult {
  imageId: number;
  approved: boolean;
  modifications: string | null;
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
