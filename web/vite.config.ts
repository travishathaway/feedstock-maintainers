import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// Root-relative base path the site is served from (SvelteKit's `kit.paths.base`). Set in CI
// (see .github/workflows/pages.yml) since the site is deployed to a GitHub Project Pages
// subpath (https://travishathaway.github.io/feedstock-maintainers/), not the domain root.
// Unset for local dev/preview, which serve from the root.
function resolveBasePath(): '' | `/${string}` {
	const raw = process.env.BASE_PATH;
	if (!raw) return '';
	if (!raw.startsWith('/') || raw.endsWith('/')) {
		throw new Error(`BASE_PATH must start with "/" and not end with "/" (got "${raw}")`);
	}
	return raw as `/${string}`;
}

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// Every internal link/`goto()` in the app uses `resolve()` from `$app/paths`, which
			// prefixes with this base path automatically -- without it, every internal navigation
			// (nav bar, badges, profile links) would 404 once deployed under the subpath.
			paths: { base: resolveBasePath() },

			adapter: adapter({ pages: 'build', assets: 'build', fallback: undefined, strict: true })
		})
	],
	ssr: {
		// svelte-chartjs ships its component as raw, uncompiled .svelte source (the standard
		// approach for Svelte libraries) and expects the consuming app's Svelte plugin to compile
		// it. Vite's SSR dependency externalization sometimes fails to auto-detect that for
		// packages installed under pnpm's nested node_modules layout, instead handing the raw
		// .svelte file straight to Node's module loader -- which doesn't understand that
		// extension and throws ERR_UNKNOWN_FILE_EXTENSION. Listing it here forces Vite to always
		// run it through the Svelte compiler during SSR, regardless of that detection.
		noExternal: ['svelte-chartjs']
	}
});
