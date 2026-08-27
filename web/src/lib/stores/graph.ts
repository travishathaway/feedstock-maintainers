import { writable } from 'svelte/store';

/** All GitHub usernames present in the currently-loaded maintainer graph. */
export const usernames = writable<string[]>([]);

/** The username currently selected in the sidebar's autocomplete filter, or null. */
export const selectedUsername = writable<string | null>(null);

/** Bumped whenever the sidebar's "Reset view" button is clicked. */
export const resetViewRequested = writable<number>(0);
