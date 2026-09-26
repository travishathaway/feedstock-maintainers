<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { loadPackageOverview, type PackageOverview, type PackageStatus } from '$lib/site-data';
	import { formatNumber, formatPercent } from '$lib/format';

	let overview = $state<PackageOverview | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	onMount(async () => {
		try {
			overview = await loadPackageOverview();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	const STATUS_LABELS: Record<PackageStatus, string> = {
		at_risk: 'At risk',
		watch: 'Watch',
		healthy: 'Healthy'
	};

	const STATUS_BADGE_CLASSES: Record<PackageStatus, string> = {
		at_risk: 'text-bg-danger',
		watch: 'text-bg-warning',
		healthy: 'text-bg-success'
	};

	// Static mock data -- no per-feedstock commit-timestamp tracking exists yet (see the plan's
	// decision #7). Not wired to real data; kept purely as a visual placeholder matching the
	// mockup's shape.
	const mockRecentUpdates = [
		{ name: 'numpy', when: '2 hours ago', by: 'array-api-lynx' },
		{ name: 'pandas', when: '5 hours ago', by: 'recipe-smith' },
		{ name: 'scikit-learn', when: '1 day ago', by: 'sci-stack-mole' },
		{ name: 'opencv', when: '3 days ago', by: 'build-matrix' },
		{ name: 'gdal', when: '4 days ago', by: 'cross-compile-fox' },
		{ name: 'r-base', when: '6 days ago', by: 'lakehouse-vole' }
	];
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

	<div class="card text-bg-light mt-5">
		<div class="card-body">
			<h3 class="h5">High usage, thin maintainer bench</h3>
			<p class="small text-secondary">
				Ranked by download volume relative to maintainer count. These are the packages a bus
				factor of one or two would hurt the most.
			</p>
			<table class="table align-middle mb-0">
				<thead>
					<tr>
						<th>Package</th>
						<th class="text-end">Downloads/mo</th>
						<th class="text-end">Maintainers</th>
						<th>Status</th>
					</tr>
				</thead>
				<tbody>
					{#each overview.risk_packages as pkg (pkg.name)}
						<tr>
							<td>
								<a href={resolve('/packages/[name]', { name: encodeURIComponent(pkg.name) })}
									>{pkg.name}</a
								>
							</td>
							<td class="text-end">{formatNumber(pkg.downloads_last_month)}</td>
							<td class="text-end">{formatNumber(pkg.maintainer_count)}</td>
							<td>
								<span class="badge {STATUS_BADGE_CLASSES[pkg.status]}">{STATUS_LABELS[pkg.status]}</span>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>

	<div class="row mt-4">
		<div class="col-lg-6 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h5">Most depended-on transitive dependencies</h3>
					<p class="small text-secondary">
						Packages other feedstocks pull in indirectly, not just direct installs.
					</p>
					<table class="table align-middle mb-0">
						<thead>
							<tr>
								<th>Package</th>
								<th class="text-end">Dependent feedstocks</th>
								<th class="text-end">Maint.</th>
							</tr>
						</thead>
						<tbody>
							{#each overview.transitive_dependencies as dep (dep.name)}
								<tr>
									<td>
										<a href={resolve('/packages/[name]', { name: encodeURIComponent(dep.name) })}
											>{dep.name}</a
										>
									</td>
									<td class="text-end">{formatNumber(dep.dependent_feedstocks)}</td>
									<td class="text-end">{formatNumber(dep.maintainer_count)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		</div>
		<div class="col-lg-6 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h5">Recently updated feedstocks</h3>
					<p class="small text-secondary">
						Latest merged builds, most recent first.
						<span class="fst-italic">Placeholder data -- not yet wired to a real source.</span>
					</p>
					<table class="table align-middle mb-0">
						<thead>
							<tr>
								<th>Package</th>
								<th>Updated</th>
								<th>By</th>
							</tr>
						</thead>
						<tbody>
							{#each mockRecentUpdates as update (update.name)}
								<tr>
									<td>{update.name}</td>
									<td class="text-secondary">{update.when}</td>
									<td>@{update.by}</td>
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
