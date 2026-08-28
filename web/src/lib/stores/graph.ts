import { writable } from 'svelte/store';

/** All GitHub usernames present in the currently-loaded maintainer graph. */
export const usernames = writable<string[]>([]);

/** The username currently selected in the sidebar's autocomplete filter, or null. */
export const selectedUsername = writable<string | null>(null);

/** Bumped whenever the sidebar's "Reset view" button is clicked. */
export const resetViewRequested = writable<number>(0);

/** How many hops from the selected node to keep visible. Only meaningful when a node is selected. */
export const degreesOfSeparation = writable<number>(2);

/** Full attribute bag of the currently-selected node (for the detail panel), or null. */
export const selectedNodeAttributes = writable<Record<string, any> | null>(null);
