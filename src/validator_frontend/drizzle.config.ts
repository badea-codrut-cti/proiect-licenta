import { defineConfig } from 'drizzle-kit';

export default defineConfig({
  schema: './src/backend/schema.ts',
  out: './drizzle',
  dialect: 'sqlite',
  dbCredentials: {
    url: '.tmp.db', // Local SQLite for development
  },
});