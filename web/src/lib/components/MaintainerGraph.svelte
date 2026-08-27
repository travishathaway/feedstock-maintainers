<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { Sigma as SigmaType } from 'sigma';
	import { loadMaintainerGraph } from '$lib/graph';
	import { usernames, selectedUsername, resetViewRequested } from '$lib/stores/graph';

	let container: HTMLDivElement;
	let sigma: SigmaType | undefined;
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let selected: string | null = null;

	const FADED_NODE_COLOR = '#e0e0e0';
	const FADED_EDGE_COLOR = '#f0f0f0';
	const SELECTED_COLOR = '#e67e22';
	const DEFAULT_NODE_COLOR = '#5B8DEF';

	onMount(async () => {
		try {
			// Dynamically imported: sigma touches WebGL globals at module-eval time, which
			// doesn't exist during SvelteKit's prerender/SSR pass.
			const [{ Sigma }, graph] = await Promise.all([import('sigma'), loadMaintainerGraph()]);
			usernames.set(graph.nodes()); // node key === GitHub login

			sigma = new Sigma(graph, container, {
				nodeReducer: (node, attrs) => {
					if (!selected) return { ...attrs, color: attrs.color ?? DEFAULT_NODE_COLOR };
					if (node === selected) return { ...attrs, color: SELECTED_COLOR, zIndex: 1 };
					if (graph.areNeighbors(node, selected)) {
						return { ...attrs, color: attrs.color ?? DEFAULT_NODE_COLOR };
					}
					return { ...attrs, color: FADED_NODE_COLOR };
				},
				edgeReducer: (edge, attrs) => {
					const size = Math.max(1, Math.log1p(attrs.weight ?? 1));
					if (!selected) return { ...attrs, size, color: attrs.color ?? '#ccc' };
					const touchesSelected = graph.extremities(edge).includes(selected);
					return { ...attrs, size, color: touchesSelected ? (attrs.color ?? '#ccc') : FADED_EDGE_COLOR };
				}
			});
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	const unsubscribeSelection = selectedUsername.subscribe((value) => {
		selected = value;
		sigma?.refresh();

		if (!sigma) return;
		const camera = sigma.getCamera();
		if (value) {
			const data = sigma.getNodeDisplayData(value);
			if (data) camera.animate({ x: data.x, y: data.y, ratio: 0.05 }, { duration: 500 });
		} else {
			camera.animatedReset({ duration: 500 });
		}
	});

	const unsubscribeReset = resetViewRequested.subscribe(() => {
		sigma?.getCamera().animatedReset({ duration: 500 });
	});

	onDestroy(() => {
		unsubscribeSelection();
		unsubscribeReset();
		sigma?.kill();
	});
</script>

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="loading">Loading maintainer graph…</p>
{/if}
<div bind:this={container} class="graph-container"></div>

<style>
	.graph-container {
		width: 100%;
		flex: 1 1 auto;
		min-height: 0;
	}

	.error {
		color: #c0392b;
	}
</style>
