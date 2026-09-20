import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { EntryGenerator } from './$types';

export const prerender = true;

// Runs only at build time (`vite build`, invoked with cwd = web/ -- see the "build-web" pixi
// task -- after `pixi run generate-site-data` has already populated static/data/, see
// .github/workflows/pages.yml). Reads the pre-computed package name list straight off disk, per
// the plan, rather than duplicating any fetch/runtime logic here.
export const entries: EntryGenerator = () => {
	const indexPath = join(process.cwd(), 'static', 'data', 'packages', 'index.json');
	const names = JSON.parse(readFileSync(indexPath, 'utf-8')) as string[];
	return names.map((name) => ({ name }));
};
