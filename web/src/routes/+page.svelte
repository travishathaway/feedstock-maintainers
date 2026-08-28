<script lang="ts">
	import MaintainerGraph from '$lib/components/MaintainerGraph.svelte';
	import NodeDetailPanel from '$lib/components/NodeDetailPanel.svelte';
</script>

<h1 class="h5">Feedstock maintainers</h1>
<p class="text-secondary small">
	Nodes are maintainers; an edge means two maintainers co-maintain at least one feedstock.
</p>

<div class="d-flex flex-grow-1" style="min-height: 0;">
	<MaintainerGraph />
	<NodeDetailPanel />
</div>
