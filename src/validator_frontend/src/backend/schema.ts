import { drizzle } from 'drizzle-orm/d1';
import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';

// Problems table - stores the CDL descriptions from images
export const problems = sqliteTable('problems', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  cerinta: text('cerinta').notNull(),      // The problem statement (from the image)
  explicatie: text('explicatie').notNull(), // Explanation/solution
  instructionCount: integer('instruction_count').notNull(), // Line count of CDL description
});

// Images table - stores the images and validation state
export const images = sqliteTable('images', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  problemId: integer('problem_id').notNull().references(() => problems.id),
  link: text('link').notNull(),              // Image URL
  aiDescription: text('ai_description').notNull(), // AI-generated CDL description

  // First validator fields
  firstValidatorApproved: integer('first_validator_approved', { mode: 'boolean' }),
  firstValidatorModifications: text('first_validator_modifications'),
  firstValidatorSessionId: text('first_validator_session_id'), // Session that claimed this

  // Second validator fields
  secondValidatorApproved: integer('second_validator_approved', { mode: 'boolean' }),
  secondValidatorModifications: text('second_validator_modifications'),
  secondValidatorSessionId: text('second_validator_session_id'), // Session that claimed this
});

// Types for use in the app
export type Problem = typeof problems.$inferSelect;
export type Image = typeof images.$inferSelect;

// Schema object for drizzle
export const schema = { problems, images };

export function createDb(d1: D1Database) {
  return drizzle(d1, { schema });
}
