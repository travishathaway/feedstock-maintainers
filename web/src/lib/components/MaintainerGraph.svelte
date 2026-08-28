<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import type { Network, Options, AnimationOptions } from 'vis-network';
	import type { DataSet } from 'vis-data';
	import type Graph from 'graphology';
	import { loadMaintainerGraph } from '$lib/graph';
	import {
		usernames,
		selectedUsername,
		resetViewRequested,
		degreesOfSeparation,
		selectedNodeAttributes
	} from '$lib/stores/graph';

	interface NodeItem {
		id: string;
		x: number;
		y: number;
		color: string;
		label?: string;
		size?: number;
		hidden?: boolean;
	}

	interface EdgeItem {
		id: string;
		from: string;
		to: string;
		color: string;
		width: number;
		hidden?: boolean;
	}

	interface NodeOverride {
		color?: string;
		hidden?: boolean;
	}

	let container: HTMLDivElement;
	let network: Network | undefined;
	let graph: Graph | undefined;
	let nodesDataSet: DataSet<NodeItem> | undefined;
	let edgesDataSet: DataSet<EdgeItem> | undefined;
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let selected: string | null = null;
	let maxDegrees = 2;
	let visibleNodes: Set<string> | null = null;
	let dataBounds: { minX: number; maxX: number; minY: number; maxY: number } | undefined;

	// vis-network's own fit() derives its range from each node's on-canvas bounding
	// box, which is only populated for nodes that have actually been drawn -- on an
	// 8k-node graph that's initially almost none of them, so fit() sees a near-empty
	// range and produces a wildly wrong zoom. Compute the view ourselves from the
	// data's real x/y extents instead.
	function fitToData(animation: false | AnimationOptions = false) {
		if (!network || !dataBounds) return;
		const canvas = container.querySelector('canvas');
		const width = canvas?.clientWidth || container.clientWidth;
		const height = canvas?.clientHeight || container.clientHeight;
		if (!width || !height) return;
		const xDistance = Math.max(1, (dataBounds.maxX - dataBounds.minX) * 1.1);
		const yDistance = Math.max(1, (dataBounds.maxY - dataBounds.minY) * 1.1);
		const scale = Math.min(width / xDistance, height / yDistance);
		network.moveTo({
			position: {
				x: (dataBounds.minX + dataBounds.maxX) / 2,
				y: (dataBounds.minY + dataBounds.maxY) / 2
			},
			scale,
			animation
		});
	}

	const FADED_EDGE_COLOR = '#f0f0f0';
	const SELECTED_COLOR = '#e67e22';
	const DEFAULT_NODE_COLOR = '#5B8DEF';
	const DEFAULT_EDGE_COLOR = '#ccc';

	const baseNodeColor = new Map<string, string>();
	const baseEdgeColor = new Map<string, string>();
	let lastNodeOverrides = new Map<string, NodeOverride>();
	let lastEdgeOverrides = new Map<string, NodeOverride>();

	function overridesEqual(a: NodeOverride | undefined, b: NodeOverride | undefined): boolean {
		return (a?.color ?? null) === (b?.color ?? null) && (a?.hidden ?? false) === (b?.hidden ?? false);
	}

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

	// vis-network has no per-frame reducer like sigma; recompute the full override
	// state (cheap -- a plain JS pass over the graph) but only push the entries that
	// actually changed since last time into DataSet.updateOnly, so we're not forcing
	// a canvas redraw pass over the whole graph on every selection change.
	function applyHighlight() {
		if (!graph || !nodesDataSet || !edgesDataSet) return;

		const nextNodeOverrides = new Map<string, NodeOverride>();
		const nodeUpdates: Array<{ id: string; color: string; hidden: boolean }> = [];

		for (const node of graph.nodes()) {
			let override: NodeOverride | undefined;
			if (selected) {
				if (visibleNodes && !visibleNodes.has(node)) override = { hidden: true };
				else if (node === selected) override = { color: SELECTED_COLOR };
			}
			if (override) nextNodeOverrides.set(node, override);
			if (!overridesEqual(lastNodeOverrides.get(node), override)) {
				nodeUpdates.push({
					id: node,
					color: override?.color ?? baseNodeColor.get(node) ?? DEFAULT_NODE_COLOR,
					hidden: override?.hidden ?? false
				});
			}
		}

		const nextEdgeOverrides = new Map<string, NodeOverride>();
		const edgeUpdates: Array<{ id: string; color: string; hidden: boolean }> = [];

		for (const edge of graph.edges()) {
			let override: NodeOverride | undefined;
			if (selected) {
				const [s, t] = graph.extremities(edge);
				if (visibleNodes && (!visibleNodes.has(s) || !visibleNodes.has(t))) {
					override = { hidden: true };
				} else if (s !== selected && t !== selected) {
					override = { color: FADED_EDGE_COLOR };
				}
			}
			if (override) nextEdgeOverrides.set(edge, override);
			if (!overridesEqual(lastEdgeOverrides.get(edge), override)) {
				edgeUpdates.push({
					id: edge,
					color: override?.color ?? baseEdgeColor.get(edge) ?? DEFAULT_EDGE_COLOR,
					hidden: override?.hidden ?? false
				});
			}
		}

		if (nodeUpdates.length) nodesDataSet.updateOnly(nodeUpdates);
		if (edgeUpdates.length) edgesDataSet.updateOnly(edgeUpdates);

		lastNodeOverrides = nextNodeOverrides;
		lastEdgeOverrides = nextEdgeOverrides;
	}

	onMount(async () => {
		try {
			// Dynamically imported: vis-network/vis-data touch canvas/DOM globals at
			// module-eval time, which don't exist during SvelteKit's prerender/SSR pass.
			const [{ Network }, { DataSet }, loadedGraph] = await Promise.all([
				import('vis-network'),
				import('vis-data'),
				loadMaintainerGraph()
			]);
			graph = loadedGraph;
			usernames.set(graph.nodes()); // node key === GitHub login

			const nodeItems: NodeItem[] = graph.nodes().map((id) => {
				const attrs = graph!.getNodeAttributes(id);
				const color = attrs.color ?? DEFAULT_NODE_COLOR;
				baseNodeColor.set(id, color);
				// Only set `size` when the data actually provides one -- an explicit
				// `size: undefined` on a DataSet item overwrites vis-network's global
				// default size instead of falling back to it.
				const item: NodeItem = { id, x: attrs.x, y: attrs.y, color, label: attrs.label };
				if (typeof attrs.size === 'number') item.size = attrs.size;
				return item;
			});

			const edgeItems: EdgeItem[] = graph.edges().map((id) => {
				const attrs = graph!.getEdgeAttributes(id);
				const [source, target] = graph!.extremities(id);
				const color = attrs.color ?? DEFAULT_EDGE_COLOR;
				baseEdgeColor.set(id, color);
				return {
					id,
					from: source,
					to: target,
					color,
					width: Math.max(1, Math.log1p(attrs.weight ?? 1))
				};
			});

			dataBounds = nodeItems.reduce(
				(acc, n) => ({
					minX: Math.min(acc.minX, n.x),
					maxX: Math.max(acc.maxX, n.x),
					minY: Math.min(acc.minY, n.y),
					maxY: Math.max(acc.maxY, n.y)
				}),
				{ minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity }
			);

			nodesDataSet = new DataSet(nodeItems);
			edgesDataSet = new DataSet(edgeItems);

			const options: Options = {
				physics: { enabled: false },
				interaction: { hover: true },
				nodes: { shape: 'dot', size: 4 }
			};
			network = new Network(container, { nodes: nodesDataSet, edges: edgesDataSet }, options);

			network.on('click', (params: { nodes: string[]; edges: string[] }) => {
				if (params.nodes.length) selectedUsername.set(params.nodes[0]);
				else if (params.edges.length === 0) selectedUsername.set(null);
			});
			network.on('hoverNode', () => {
				container.style.cursor = 'pointer';
			});
			network.on('blurNode', () => {
				container.style.cursor = 'default';
			});

			// Unlike sigma, vis-network doesn't auto-frame the data on construction --
			// without this we start zoomed in on a tiny slice of the layout's coordinate space.
			fitToData();
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
		applyHighlight();

		if (!network) return;
		if (value) {
			network.focus(value, { scale: 1.5, animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
		} else {
			fitToData({ duration: 500, easingFunction: 'easeInOutQuad' });
		}
	});

	const unsubscribeDegrees = degreesOfSeparation.subscribe((value) => {
		maxDegrees = value;
		recomputeVisibleNodes();
		applyHighlight();
	});

	const unsubscribeReset = resetViewRequested.subscribe(() => {
		fitToData({ duration: 500, easingFunction: 'easeInOutQuad' });
	});

	onDestroy(() => {
		unsubscribeSelection();
		unsubscribeDegrees();
		unsubscribeReset();
		network?.destroy();
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
