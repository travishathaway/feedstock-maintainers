<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import Avatar from '$lib/components/Avatar.svelte';
	import FeedstockCountChart from '$lib/components/FeedstockCountChart.svelte';
	import { loadMaintainerOverview, type MaintainerOverview } from '$lib/site-data';
	import { formatNumber, formatPercent } from '$lib/format';

	let overview = $state<MaintainerOverview | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	onMount(async () => {
		try {
			overview = await loadMaintainerOverview();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});
</script>

<div class="col-12">
    <h2 class="mt-5">Maintainer statistics</h2>
    <p class="text-body-secondary " style="max-width: 80ch">
        Who keeps conda-forge running: how many people maintain feedstocks, how concentrated that work is,
        and how the maintainer base has grown.
    </p>
</div>

<hr />

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="text-body-secondary">Loading maintainer statistics…</p>
{:else if overview}
	<div class="d-flex mt-4">
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(overview.stats.maintainer_count)}</span>
				<p class="">Maintainers</p>
				<p class="small text-secondary">Across {formatNumber(overview.stats.feedstock_count)} feedstocks</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(overview.stats.feedstock_count)}</span>
				<p class="">Feedstocks</p>
				<p class="small text-secondary">Live feedstocks on conda-forge</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(overview.stats.avg_maintainers_per_feedstock)}</span>
				<p class="">Average maintainers per feedstock</p>
				<p class="small text-secondary">
					Median is {formatNumber(overview.stats.median_maintainers_per_feedstock)}
				</p>
			</div>
		</div>
		<div class="col card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(overview.stats.single_maintainer_feedstock_count)}</span>
				<p class="">Single maintainer feedstocks</p>
				<p class="">
					<span class="badge text-bg-warning"
						>{formatPercent(overview.stats.single_maintainer_feedstock_pct)} of all feedstocks</span
					>
				</p>
			</div>
		</div>
	</div>

	<div class="d-flex mt-5 mb-5 ps-5 pe-5" style="min-height: 300px;">
		<div class="col">
			<h3 class="mb-3">Maintainer growth since 2016</h3>
			<FeedstockCountChart />
		</div>
	</div>

	<div class="row mt-5">
		<div class="col-lg-5 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h5 mb-3">Top maintainers by feedstocks managed</h3>
					<table class="table align-middle mb-0">
						<thead>
							<tr>
								<th style="width: 2.5rem;"></th>
								<th>Maintainer</th>
								<th class="text-end">Feedstocks</th>
							</tr>
						</thead>
						<tbody>
						{#each overview.top_maintainers as maintainer, i (maintainer.login)}
							<tr>
								<td>
									<span class="badge rounded-pill text-bg-secondary-subtle text-secondary-emphasis"
										>{i + 1}</span
									>
								</td>
								<td>
									<div class="d-flex align-items-center gap-2">
										<Avatar
											name={maintainer.name ?? maintainer.login}
											avatarUrl={maintainer.avatar_url}
											size={28}
										/>
										{#if maintainer.name}
											<a
												href={resolve('/maintainers/[login]', {
													login: encodeURIComponent(maintainer.login)
												})}>@{maintainer.login}</a
											>
										{:else}
											@{maintainer.login}
										{/if}
									</div>
								</td>
								<td class="text-end">{formatNumber(maintainer.feedstock_count)}</td>
							</tr>
						{/each}
						</tbody>
					</table>
				</div>
			</div>
		</div>
		<div class="col-lg-7 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h5 mb-3">Most popular packages</h3>
					<table class="table align-middle mb-0">
						<thead>
							<tr>
								<th>Package</th>
								<th class="text-end">Downloads/mo</th>
								<th class="text-end">Maint.</th>
								<th>Top maintainer</th>
							</tr>
						</thead>
						<tbody>
						{#each overview.popular_packages as pkg (pkg.name)}
							<tr>
								<td>
									<a href={resolve('/packages/[name]', { name: encodeURIComponent(pkg.name) })}
										>{pkg.name}</a
									>
								</td>
								<td class="text-end">{formatNumber(pkg.downloads_last_month)}</td>
								<td class="text-end">{formatNumber(pkg.maintainer_count)}</td>
								<td>
									{#if pkg.top_maintainer_login}
										<a
											href={resolve('/maintainers/[login]', {
												login: encodeURIComponent(pkg.top_maintainer_login)
											})}>@{pkg.top_maintainer_login}</a
										>
									{:else}
										<span class="text-body-secondary">—</span>
									{/if}
								</td>
							</tr>
						{/each}
						</tbody>
					</table>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}
</style>
