import type { PageLoad } from './$types';

// Just forwards the route param with a concrete (non-optional) type -- the profile JSON itself
// is still fetched client-side in +page.svelte via onMount, per the plan.
export const load: PageLoad = ({ params }) => {
	return { name: params.name };
};
