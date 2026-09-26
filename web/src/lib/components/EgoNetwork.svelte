<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Graph from 'graphology';
	import type { Sigma as SigmaType } from 'sigma';
	import { layoutGraph } from '$lib/graph';
	import type { EgoNetwork } from '$lib/site-data';

	interface Props {
		login: string;
		egoNetwork: EgoNetwork;
	}

	let { login, egoNetwork }: Props = $props();

	let container: HTMLDivElement;
	let sigma: SigmaType | undefined;
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	const CENTER_COLOR = '#ef6c00';
	const ONE_HOP_COLOR = '#00695c';
	const TWO_HOP_COLOR = '#80cbc4';
	const COLOR_BY_DISTANCE: Record<number, string> = {
		0: CENTER_COLOR,
		1: ONE_HOP_COLOR,
		2: TWO_HOP_COLOR
	};
	const SIZE_BY_DISTANCE: Record<number, number> = { 0: 14, 1: 9, 2: 6 };

	onMount(async () => {
		try {
			// Dynamically imported: sigma touches WebGL globals at module-eval time, which
			// doesn't exist during SvelteKit's prerender/SSR pass.
			const { Sigma } = await import('sigma');

			const graph = new Graph({ type: 'undirected', multi: false, allowSelfLoops: false });
			for (const node of egoNetwork.nodes) {
				graph.addNode(node.key, { label: node.label, distance: node.distance });
			}
			for (const edge of egoNetwork.edges) {
				graph.addEdge(edge.source, edge.target, { weight: edge.weight });
			}
			layoutGraph(graph);

			sigma = new Sigma(graph, container, {
				enableCameraZooming: false,
				nodeReducer: (node, attrs) => {
					const distance = attrs.distance as number;
					return {
						...attrs,
						color: COLOR_BY_DISTANCE[distance] ?? TWO_HOP_COLOR,
						size: SIZE_BY_DISTANCE[distance] ?? SIZE_BY_DISTANCE[2],
						zIndex: distance === 0 ? 2 : 1
					};
				},
				edgeReducer: (edge, attrs) => {
					const size = Math.max(1, Math.log1p((attrs.weight as number) ?? 1));
					return { ...attrs, size, color: attrs.color ?? '#ccc' };
				}
			});

			sigma.on('clickNode', ({ node }) => {
				if (node === login) return;
				goto(resolve('/maintainers/[login]', { login: encodeURIComponent(node) }));
			});
			sigma.on('enterNode', ({ node }) => {
				container.style.cursor = node === login ? 'default' : 'pointer';
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

	onDestroy(() => {
		sigma?.kill();
	});
</script>

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="text-body-secondary small">Loading network…</p>
{/if}
<div bind:this={container} class="graph-container"></div>

<style>
	.graph-container {
		width: 100%;
		height: 440px;
		cursor: default;
	}

	.error {
		color: var(--bs-danger);
	}
</style>
