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
