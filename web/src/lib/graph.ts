import Graph from 'graphology';
import circular from 'graphology-layout/circular';
import forceAtlas2 from 'graphology-layout-forceatlas2';

// Set at build time in CI (see .github/workflows/pages.yml). Empty in local
// dev, where the dev server already serves web/static/* from the site root.
const SITE_BASE_URL = import.meta.env.VITE_SITE_BASE_URL ?? '';

function resolveGraphDataUrl(): string {
	if (!SITE_BASE_URL) {
		return '/data/maintainer-graph.json';
	}
	return `${SITE_BASE_URL.replace(/\/+$/, '')}/data/maintainer-graph.json`;
}

export async function loadMaintainerGraph(url = resolveGraphDataUrl()): Promise<Graph> {
	const res = await fetch(url);
	if (!res.ok) {
		throw new Error(
			`Failed to load graph data (${res.status}). Have you run \`pixi run generate-graph-data\`?`
		);
	}

	const graph = Graph.from(await res.json());

	circular.assign(graph); // seeds x/y -- forceAtlas2 needs a starting layout, it doesn't invent one
	forceAtlas2.assign(graph, {
		iterations: graph.order > 1500 ? 100 : 300,
		settings: forceAtlas2.inferSettings(graph)
	});

	return graph;
}
