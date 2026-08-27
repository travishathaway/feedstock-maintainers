<script lang="ts">
	import MaintainerGraph from '$lib/components/MaintainerGraph.svelte';
</script>

<h1 class="h5">Feedstock maintainers</h1>
<p class="text-secondary small">
	Nodes are maintainers; an edge means two maintainers co-maintain at least one feedstock.
</p>

<MaintainerGraph />
