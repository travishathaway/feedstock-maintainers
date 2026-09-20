import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

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
