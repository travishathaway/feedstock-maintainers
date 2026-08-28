<script lang="ts">
	import { selectedUsername, selectedNodeAttributes } from '$lib/stores/graph';

	function close() {
		selectedUsername.set(null);
	}

	function formatMetric(value: number | undefined): string {
		return typeof value === 'number' ? value.toFixed(4) : '—';
	}
</script>

{#if $selectedNodeAttributes}
	{@const attrs = $selectedNodeAttributes}
	<div class="node-detail-panel border-start h-100 overflow-auto py-3 px-3">
		<div class="d-flex justify-content-between align-items-start mb-3">
			<h6 class="text-uppercase text-secondary fw-semibold small mb-0 d-flex align-items-center gap-2">
				<i class="bi bi-person-badge"></i> Maintainer
			</h6>
			<button
				type="button"
				class="btn-close"
				aria-label="Close"
				onclick={close}
			></button>
		</div>

		<div class="text-center mb-3">
			{#if attrs.avatarUrl}
				<img src={attrs.avatarUrl} alt="{attrs.login}'s avatar" class="rounded-circle mb-2" width="80" height="80" />
			{/if}
			<div class="fw-semibold">{attrs.label ?? attrs.login}</div>
			{#if attrs.githubUrl}
				<a href={attrs.githubUrl} target="_blank" rel="noreferrer" class="small">
					<i class="bi bi-github"></i> @{attrs.login}
				</a>
			{/if}
		</div>

		<dl class="row small mb-3">
			{#if attrs.company}
				<dt class="col-6">Company</dt>
				<dd class="col-6 text-truncate">{attrs.company}</dd>
			{/if}
			{#if attrs.location}
				<dt class="col-6">Location</dt>
				<dd class="col-6 text-truncate">{attrs.location}</dd>
			{/if}
			<dt class="col-6">Total feedstocks</dt>
			<dd class="col-6">{attrs.feedstockCount}</dd>
		</dl>

		<h6 class="text-uppercase text-secondary fw-semibold small mb-2 d-flex align-items-center gap-2">
			<i class="bi bi-diagram-3"></i> Connectedness
		</h6>
		<dl class="row small">
			<dt class="col-7">Degree centrality</dt>
			<dd class="col-5 text-end font-monospace">{formatMetric(attrs.degreeCentrality)}</dd>
			<dt class="col-7">Weighted degree</dt>
			<dd class="col-5 text-end font-monospace">{attrs.weightedDegree ?? '—'}</dd>
			<dt class="col-7">Betweenness</dt>
			<dd class="col-5 text-end font-monospace">{formatMetric(attrs.betweennessCentrality)}</dd>
			<dt class="col-7">PageRank</dt>
			<dd class="col-5 text-end font-monospace">{formatMetric(attrs.pagerank)}</dd>
		</dl>
	</div>
{/if}

<style>
	.node-detail-panel {
		width: 320px;
		flex: 0 0 320px;
	}
</style>
