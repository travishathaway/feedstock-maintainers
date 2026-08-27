import Graph from 'graphology';
import circular from 'graphology-layout/circular';
import forceAtlas2 from 'graphology-layout-forceatlas2';

export async function loadMaintainerGraph(url = '/data/maintainer-graph.json'): Promise<Graph> {
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
