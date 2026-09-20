<script lang="ts">
	import { onMount } from 'svelte';
	import HistoryChart from './HistoryChart.svelte';
	import { loadMaintainerHistory } from '$lib/history';

	let labels = $state<string[]>([]);
	let values = $state<number[]>([]);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	onMount(async () => {
		try {
			const points = await loadMaintainerHistory();
			labels = points.map((point) => point.date);
			values = points.map((point) => point.unique_maintainer_count);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});
</script>

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="text-body-secondary">Loading maintainer count history…</p>
{:else}
	<HistoryChart {labels} {values} label="Unique maintainers" />
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}
</style>
