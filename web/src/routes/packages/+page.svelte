<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import EnvironmentInspector from '$lib/components/EnvironmentInspector.svelte';
	import PackageList from '$lib/components/PackageList.svelte';
	import {
		loadPackageList,
		loadPackageOverview,
		type PackageList as PackageListData,
		type PackageOverview
	} from '$lib/site-data';
	import { formatNumber, formatPercent } from '$lib/format';

	let overview = $state<PackageOverview | undefined>(undefined);
	let packageList = $state<PackageListData | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let listError = $state<string | undefined>(undefined);
	let loading = $state(true);
	let tab = $state<'all' | 'inspect'>('all');

	function selectTab(next: 'all' | 'inspect') {
		tab = next;
		const url = new URL(page.url);
		if (next === 'all') url.searchParams.delete('tab');
		else url.searchParams.set('tab', next);
		replaceState(url, page.state);
	}

	onMount(async () => {
		if (new URL(location.href).searchParams.get('tab') === 'inspect') tab = 'inspect';
		// The overview cards are required; the list is best-effort (it is empty/absent until
		// health data has been collected) and must not take the whole page down with it.
		const [overviewResult, listResult] = await Promise.allSettled([
			loadPackageOverview(),
			loadPackageList()
		]);
		if (overviewResult.status === 'fulfilled') overview = overviewResult.value;
		else error = String(overviewResult.reason?.message ?? overviewResult.reason);
		if (listResult.status === 'fulfilled') packageList = listResult.value;
		else listError = String(listResult.reason?.message ?? listResult.reason);
		loading = false;
	});
</script>

<div class="col-12">
    <h2 class="mt-5">Package statistics</h2>
    <p class="text-body-secondary " style="max-width: 90ch">
        Where usage and maintenance capacity are furthest apart — heavily used packages with thin
        maintainer benches, and the infrastructure everything else quietly depends on.
    </p>
</div>

<hr />

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="text-body-secondary">Loading package statistics…</p>
{:else if overview}
	<!-- Mobile: condensed table -->
	<div class="card text-bg-light mt-4 d-md-none">
		<table class="table table-sm mb-0 align-middle">
			<tbody>
				<tr>
					<td class="text-body-secondary">Total packages</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">{formatNumber(overview.stats.package_count)}</div>
						<div class="small text-secondary">Live feedstocks on conda-forge</div>
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Packages with ≤2 maintainers</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">
							{formatNumber(overview.stats.packages_le2_maintainers_count)}
						</div>
						<span class="badge text-bg-warning"
							>{formatPercent(overview.stats.packages_le2_maintainers_pct)} of all packages</span
						>
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Most depended-on package</td>
					<td class="text-end">
						{#if overview.stats.most_depended_on}
							<div class="fw-semibold fs-5">
								<a
									href={resolve('/packages/[name]', {
										name: encodeURIComponent(overview.stats.most_depended_on.name)
									})}>{overview.stats.most_depended_on.name}</a
								>
							</div>
							<div class="small text-secondary">
								{formatNumber(overview.stats.most_depended_on.transitive_dependents)} feedstocks · {formatNumber(
									overview.stats.most_depended_on.maintainer_count
								)} maintainers
							</div>
						{:else}
							<div class="fw-semibold fs-4">—</div>
						{/if}
					</td>
				</tr>
			</tbody>
		</table>
	</div>

	<!-- Tablet & up: original card row -->
	<div class="d-none d-md-flex mt-4">
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(overview.stats.package_count)}</span>
				<p class="">Total packages</p>
				<p class="small text-secondary">Live feedstocks on conda-forge</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(overview.stats.packages_le2_maintainers_count)}</span>
				<p class="">Packages with ≤2 maintainers</p>
				<p class="">
					<span class="badge text-bg-warning"
						>{formatPercent(overview.stats.packages_le2_maintainers_pct)} of all packages</span
					>
				</p>
			</div>
		</div>
		<div class="col card text-bg-light">
			<div class="card-body">
				{#if overview.stats.most_depended_on}
					<span class="h4">
						<a
							href={resolve('/packages/[name]', {
								name: encodeURIComponent(overview.stats.most_depended_on.name)
							})}>{overview.stats.most_depended_on.name}</a
						>
					</span>
					<p class="">Most depended-on package</p>
					<p class="small text-secondary">
						{formatNumber(overview.stats.most_depended_on.transitive_dependents)} feedstocks · {formatNumber(
							overview.stats.most_depended_on.maintainer_count
						)} maintainers
					</p>
				{:else}
					<span class="h3">—</span>
					<p class="">Most depended-on package</p>
				{/if}
			</div>
		</div>
	</div>

	<ul class="nav nav-tabs mt-5">
		<li class="nav-item">
			<button
				type="button"
				class="nav-link"
				class:active={tab === 'all'}
				onclick={() => selectTab('all')}>All</button
			>
		</li>
		<li class="nav-item">
			<button
				type="button"
				class="nav-link"
				class:active={tab === 'inspect'}
				onclick={() => selectTab('inspect')}>Inspect</button
			>
		</li>
	</ul>

	{#if tab === 'all'}
	<h3 class="h5 mt-4 mb-0">Browse packages</h3>
	<p class="small text-secondary mb-0" style="max-width: 90ch">
		Packages from the most-downloaded and most-depended-on feedstocks, with a relative health
		score. It is a prompt to take a look, not a verdict.
	</p>
	{#if packageList && packageList.packages.length > 0}
		<PackageList packages={packageList.packages} />
	{:else if listError}
		<p class="text-body-secondary mt-3">Package list unavailable ({listError}).</p>
	{:else}
		<p class="text-body-secondary mt-3">
			Health data hasn't been collected yet — check back after the next data refresh.
		</p>
	{/if}
	{:else if packageList}
		<EnvironmentInspector packages={packageList.packages} />
	{:else}
		<p class="text-body-secondary mt-3">Health data is needed to inspect an environment.</p>
	{/if}
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}
</style>
