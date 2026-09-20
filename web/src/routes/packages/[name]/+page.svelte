<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import DownloadsBarChart from '$lib/components/DownloadsBarChart.svelte';
	import { loadPackageProfile, type PackageProfile, type PackageStatus } from '$lib/site-data';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let profile = $state<PackageProfile | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	onMount(async () => {
		try {
			profile = await loadPackageProfile(data.name);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	function formatNumber(value: number): string {
		return value.toLocaleString();
	}

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
</script>

<div class="col-12 mt-4">
	<a href={resolve('/packages')} class="text-body-secondary small">&larr; Package statistics</a>
</div>

{#if error}
	<p class="error mt-3">{error}</p>
{:else if loading}
	<p class="text-body-secondary mt-3">Loading package profile…</p>
{:else if profile}
	<div class="d-flex align-items-start gap-3 mt-3 flex-wrap">
		<div
			class="d-flex align-items-center justify-content-center flex-shrink-0"
			style="width: 64px; height: 64px; border-radius: 15px; background: var(--bs-primary-bg-subtle, #b2dfdb); color: var(--bs-primary-text-emphasis, #004d40); font-weight: 800; font-size: 1.1rem;"
		>
			{profile.name.slice(0, 2)}
		</div>
		<div>
			<div class="d-flex align-items-center gap-2 flex-wrap">
				<h2 class="mb-0">{profile.name}</h2>
				<span class="badge {STATUS_BADGE_CLASSES[profile.status]}">{STATUS_LABELS[profile.status]}</span>
                {#if profile.license}
                    <span class="badge text-bg-secondary">{profile.license}</span>
                {/if}
                <div class="d-flex align-items-center gap-2 flex-wrap ms-2 mt-2">
                    {#each profile.feedstocks as feedstock (feedstock.name)}
                        <a
                            href={feedstock.url}
                            target="_blank"
                            rel="noreferrer"
                            class="btn btn-outline-secondary btn-sm text-decoration-none"
                        >
                            <i class="bi bi-github"></i> {feedstock.name}-feedstock
                        </a>
                    {/each}
                </div>
			</div>
			<!-- Static placeholder -- no version/description parsing or per-feedstock
			     commit-timestamp tracking exists yet (plan decision #7). -->
			<p class="text-body-secondary small mb-0 mt-2">Last updated unknown</p>
		</div>
	</div>

	<div class="d-flex mt-4">
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(profile.maintainer_count)}</span>
				<p class="mb-0">Maintainers</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{profile.dependent_feedstock_count === null ? '—' : formatNumber(profile.dependent_feedstock_count)}</span>
				<p class="mb-0">Dependent feedstocks</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(profile.direct_dependencies.length)}</span>
				<p class="mb-0">Direct dependencies</p>
			</div>
		</div>
		<div class="col card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(profile.downloads_last_month)}</span>
				<p class="mb-0">Downloads / month</p>
			</div>
		</div>
	</div>

	<div class="row mt-4">
		<div class="col-lg-7 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-3">Maintainers</h3>
					<div class="d-flex flex-wrap gap-2">
						{#each profile.maintainers as maintainer (maintainer.login)}
							{#if maintainer.name}
								<a
									href={resolve('/maintainers/[login]', {
										login: encodeURIComponent(maintainer.login)
									})}
									class="btn btn-outline-secondary btn-sm rounded-pill text-decoration-none"
								>
									@{maintainer.login}
								</a>
							{:else}
								<span class="btn btn-outline-secondary btn-sm rounded-pill disabled">
									{maintainer.login}
								</span>
							{/if}
						{/each}
						{#if profile.maintainers.length === 0}
							<p class="text-body-secondary small mb-0">No known maintainers.</p>
						{/if}
					</div>
				</div>
			</div>
		</div>
		<div class="col-lg-5 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-1">Downloads, last 12 months</h3>
					<p class="text-body-secondary small mb-2">Monthly conda-forge download totals.</p>
					{#if profile.downloads_monthly.length > 0}
						<DownloadsBarChart
							labels={profile.downloads_monthly.map((point) => point.month)}
							values={profile.downloads_monthly.map((point) => point.downloads)}
						/>
					{:else}
						<p class="text-body-secondary small mb-0">No download data available yet.</p>
					{/if}
				</div>
			</div>
		</div>
	</div>

	<div class="row">
		<div class="col-lg-6 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-3">Direct dependencies</h3>
					<div class="d-flex flex-wrap gap-2">
						{#each profile.direct_dependencies as dep (dep)}
							<a
								href={resolve('/packages/[name]', { name: encodeURIComponent(dep) })}
								class="badge text-bg-secondary text-decoration-none">{dep}</a
							>
						{/each}
						{#if profile.direct_dependencies.length === 0}
							<p class="text-body-secondary small mb-0">No known direct dependencies.</p>
						{/if}
					</div>
				</div>
			</div>
		</div>
		<div class="col-lg-6 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-3">Notable dependents</h3>
					<div class="d-flex flex-wrap gap-2">
						{#each profile.notable_dependents as dep (dep)}
							<a
								href={resolve('/packages/[name]', { name: encodeURIComponent(dep) })}
								class="badge text-bg-secondary text-decoration-none">{dep}</a
							>
						{/each}
						{#if profile.notable_dependents.length === 0}
							<p class="text-body-secondary small mb-0">No known notable dependents.</p>
						{/if}
					</div>
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
