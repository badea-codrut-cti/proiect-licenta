import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { renderer } from "../renderer";
import auth from './routes/auth';
import validate from './routes/validate';
import '../style.css';

import type { Env } from '../backend/schema';

const app = new Hono<{ Bindings: Env }>();

app.use('*', cors());

// Mount renderer middleware
app.use(renderer);

// Mount routes
app.route('/auth', auth);
app.route('/validate', validate);

// Redirect root to login
app.get('/', (c) => c.redirect('/auth/login'));

export default app;
