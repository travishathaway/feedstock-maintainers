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
		centerLabel: string;
		egoNetwork: EgoNetwork;
	}

	let { login, centerLabel, egoNetwork }: Props = $props();

	let container: HTMLDivElement;
	let sigma: SigmaType | undefined;
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let degree = $state<1 | 2>(1);

	const visibleKeys = $derived(
		new Set(egoNetwork.nodes.filter((node) => node.distance <= degree).map((node) => node.key))
	);

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
				nodeReducer: (node, attrs) => {
					if (!visibleKeys.has(node)) return { ...attrs, hidden: true };
					const distance = attrs.distance as number;
					return {
						...attrs,
						color: COLOR_BY_DISTANCE[distance] ?? TWO_HOP_COLOR,
						size: SIZE_BY_DISTANCE[distance] ?? SIZE_BY_DISTANCE[2],
						zIndex: distance === 0 ? 2 : 1
					};
				},
				edgeReducer: (edge, attrs) => {
					const [s, t] = graph.extremities(edge);
					if (!visibleKeys.has(s) || !visibleKeys.has(t)) return { ...attrs, hidden: true };
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

	$effect(() => {
		visibleKeys;
		sigma?.refresh();
	});

	onDestroy(() => {
		sigma?.kill();
	});
</script>

<div class="d-flex gap-3 flex-wrap">
	<div class="flex-shrink-0" style="min-width: 160px;">
		<span class="form-label small fw-semibold d-block mb-2">Degrees of separation</span>
		<div class="btn-group btn-group-sm" role="group" aria-label="Degrees of separation">
			<button
				type="button"
				class="btn {degree === 1 ? 'btn-secondary' : 'btn-outline-secondary'}"
				onclick={() => (degree = 1)}
			>
				1
			</button>
			<button
				type="button"
				class="btn {degree === 2 ? 'btn-secondary' : 'btn-outline-secondary'}"
				onclick={() => (degree = 2)}
			>
				2
			</button>
		</div>
		<p class="small text-secondary mt-2 mb-0">
			{degree === 1 ? `Direct co-maintainers of ${centerLabel}.` : "Includes co-maintainers' co-maintainers."}
		</p>
	</div>
	<div class="flex-grow-1" style="min-width: 0;">
		{#if error}
			<p class="error">{error}</p>
		{:else if loading}
			<p class="text-body-secondary small">Loading network…</p>
		{/if}
		<div bind:this={container} class="graph-container"></div>
	</div>
</div>

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
