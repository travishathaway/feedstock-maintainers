<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { Sigma as SigmaType } from 'sigma';
	import type Graph from 'graphology';
	import { loadMaintainerGraph } from '$lib/graph';
	import {
		usernames,
		selectedUsername,
		resetViewRequested,
		degreesOfSeparation,
		selectedNodeAttributes
	} from '$lib/stores/graph';

	let container: HTMLDivElement;
	let sigma: SigmaType | undefined;
	let graph: Graph | undefined;
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let selected: string | null = null;
	let maxDegrees = 2;
	let visibleNodes: Set<string> | null = null;

	const FADED_EDGE_COLOR = '#f0f0f0';
	const SELECTED_COLOR = '#e67e22';
	const DEFAULT_NODE_COLOR = '#5B8DEF';

	function recomputeVisibleNodes() {
		if (!graph || !selected) {
			visibleNodes = null;
			return;
		}
		const visited = new Set<string>([selected]);
		let frontier = [selected];
		for (let depth = 0; depth < maxDegrees && frontier.length; depth++) {
			const next: string[] = [];
			for (const n of frontier) {
				graph.forEachNeighbor(n, (neighbor) => {
					if (!visited.has(neighbor)) {
						visited.add(neighbor);
						next.push(neighbor);
					}
				});
			}
			frontier = next;
		}
		visibleNodes = visited;
	}

	onMount(async () => {
		try {
			// Dynamically imported: sigma touches WebGL globals at module-eval time, which
			// doesn't exist during SvelteKit's prerender/SSR pass.
			const [{ Sigma }, loadedGraph] = await Promise.all([import('sigma'), loadMaintainerGraph()]);
			graph = loadedGraph;
			usernames.set(graph.nodes()); // node key === GitHub login

			sigma = new Sigma(graph, container, {
				nodeReducer: (node, attrs) => {
					if (!selected) return { ...attrs, color: attrs.color ?? DEFAULT_NODE_COLOR };
					if (visibleNodes && !visibleNodes.has(node)) return { ...attrs, hidden: true };
					if (node === selected) return { ...attrs, color: SELECTED_COLOR, zIndex: 1 };
					return { ...attrs, color: attrs.color ?? DEFAULT_NODE_COLOR };
				},
				edgeReducer: (edge, attrs) => {
					const size = Math.max(1, Math.log1p(attrs.weight ?? 1));
					if (!selected) return { ...attrs, size, color: attrs.color ?? '#ccc' };
					const [s, t] = graph!.extremities(edge);
					if (visibleNodes && (!visibleNodes.has(s) || !visibleNodes.has(t))) {
						return { ...attrs, hidden: true };
					}
					const touchesSelected = s === selected || t === selected;
					return { ...attrs, size, color: touchesSelected ? (attrs.color ?? '#ccc') : FADED_EDGE_COLOR };
				}
			});

			sigma.on('clickNode', ({ node }) => selectedUsername.set(node));
			sigma.on('clickStage', () => selectedUsername.set(null));
			sigma.on('enterNode', () => {
				container.style.cursor = 'pointer';
			});
			sigma.on('leaveNode', () => {
				container.style.cursor = 'default';
			});
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	const unsubscribeSelection = selectedUsername.subscribe((value) => {
		selected = value;
		selectedNodeAttributes.set(value && graph ? graph.getNodeAttributes(value) : null);
		recomputeVisibleNodes();
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

	const unsubscribeDegrees = degreesOfSeparation.subscribe((value) => {
		maxDegrees = value;
		recomputeVisibleNodes();
		sigma?.refresh();
	});

	const unsubscribeReset = resetViewRequested.subscribe(() => {
		sigma?.getCamera().animatedReset({ duration: 500 });
	});

	onDestroy(() => {
		unsubscribeSelection();
		unsubscribeDegrees();
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
		flex: 1 1 auto;
		min-width: 0;
		min-height: 0;
		cursor: default;
	}

	.error {
		color: #c0392b;
	}
</style>
