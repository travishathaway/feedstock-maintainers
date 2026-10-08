<script lang="ts">
	import { resolve } from '$app/paths';
	import DownloadsBarChart from '$lib/components/DownloadsBarChart.svelte';
	import DependencyTree from '$lib/components/DependencyTree.svelte';
	import PackageAbout from '$lib/components/PackageAbout.svelte';
	import HealthHelp from '$lib/components/HealthHelp.svelte';
	import {
		HEALTH_COMPONENT_LABELS,
		HEALTH_TIER_BADGE_CLASSES,
		HEALTH_TIER_LABELS,
		describeComponent,
		formatRelativeTime
	} from '$lib/health';
	import {
		listAvailablePlatforms,
		listAvailableVersions,
		type PlatformOption
	} from '$lib/dependency-tree';
	import { loadPackageProfile, type PackageProfile } from '$lib/site-data';
	import { formatNumber, spdxLicenseUrl } from '$lib/format';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let profile = $state<PackageProfile | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let liveDirectDependencyCount = $state<number | undefined>(undefined);

	let selectedVersion = $state<string | undefined>(undefined);
	let availableVersions = $state<string[]>([]);
	let versionsLoading = $state(true);
	let versionsError = $state<string | undefined>(undefined);

	let selectedPlatform = $state<PlatformOption | undefined>(undefined);
	let availablePlatforms = $state<PlatformOption[]>([]);
	let platformsLoading = $state(true);
	let platformsError = $state<string | undefined>(undefined);

	$effect(() => {
		const name = data.name;
		profile = undefined;
		error = undefined;
		loading = true;
		liveDirectDependencyCount = undefined;
		loadPackageProfile(name)
			.then((result) => {
				if (name === data.name) profile = result;
			})
			.catch((e) => {
				if (name === data.name) error = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (name === data.name) loading = false;
			});
	});

	$effect(() => {
		const name = profile?.name;
		if (!name) return;

		selectedVersion = undefined;
		availableVersions = [];
		versionsError = undefined;
		versionsLoading = true;

		listAvailableVersions(name)
			.then((versions) => {
				if (name !== profile?.name) return;
				availableVersions = versions;
				selectedVersion = versions[0];
			})
			.catch((e) => {
				if (name !== profile?.name) return;
				versionsError = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (name !== profile?.name) return;
				versionsLoading = false;
			});
	});

	$effect(() => {
		const name = profile?.name;
		const version = selectedVersion;
		if (!name || !version) return;

		selectedPlatform = undefined;
		availablePlatforms = [];
		platformsError = undefined;
		platformsLoading = true;

		listAvailablePlatforms(name, version)
			.then((platforms) => {
				if (name !== profile?.name || version !== selectedVersion) return;
				availablePlatforms = platforms;
				selectedPlatform = platforms.includes('noarch') ? 'noarch' : platforms[0];
			})
			.catch((e) => {
				if (name !== profile?.name || version !== selectedVersion) return;
				platformsError = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (name !== profile?.name || version !== selectedVersion) return;
				platformsLoading = false;
			});
	});
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
                {#if profile.license}
                    {@const licenseUrl = spdxLicenseUrl(profile.license)}
                    {#if licenseUrl}
                        <a
                            href={licenseUrl}
                            target="_blank"
                            rel="noreferrer"
                            class="badge text-bg-secondary text-decoration-none"
                        >
                            {profile.license}  &nbsp;<i class="bi bi-box-arrow-up-right"></i>
                        </a>
                    {:else}
                        <span class="badge text-bg-secondary">{profile.license}</span>
                    {/if}
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
			<p class="text-body-secondary small mb-0 mt-2">
				{#if profile.health?.last_activity_at}
					Last activity {formatRelativeTime(profile.health.last_activity_at)}
				{:else}
					Last updated unknown
				{/if}
			</p>
		</div>
	</div>

	<!-- Mobile: condensed table -->
	<div class="card text-bg-light mt-4 d-md-none">
		<table class="table table-sm mb-0 align-middle">
			<tbody>
				<tr>
					<td class="text-body-secondary">Maintainers</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">{formatNumber(profile.maintainer_count)}</div>
						{#if profile.teams.length > 0}
							<div class="small text-secondary">managed by {profile.teams.join(', ')}</div>
						{/if}
						{#if profile.active_maintainer_count !== null}
							<div class="small text-secondary">
								{formatNumber(profile.active_maintainer_count)} active (12mo)
							</div>
						{/if}
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Dependent feedstocks</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">
							{profile.dependent_feedstock_count === null
								? '—'
								: formatNumber(profile.dependent_feedstock_count)}
						</div>
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Direct dependencies</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">
							{formatNumber(liveDirectDependencyCount ?? profile.direct_dependencies.length)}
						</div>
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Downloads / month</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">{formatNumber(profile.downloads_last_month)}</div>
					</td>
				</tr>
			</tbody>
		</table>
	</div>

	<!-- Tablet & up: original card row -->
	<div class="d-none d-md-flex mt-4">
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(profile.maintainer_count)}</span>
				<p class="mb-0">
					Maintainers
					{#if profile.teams.length > 0}
						<br /><span class="text-body-secondary small"
							>managed by {profile.teams.join(', ')}</span
						>
					{/if}
					{#if profile.active_maintainer_count !== null}
						<br /><span class="text-body-secondary small"
							>{formatNumber(profile.active_maintainer_count)} active (12mo)</span
						>
					{/if}
				</p>
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
				<span class="h3"
					>{formatNumber(liveDirectDependencyCount ?? profile.direct_dependencies.length)}</span
				>
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

	<div class="card text-bg-light mt-4">
		<div class="card-body">
			<h3 class="h5">Health<HealthHelp /></h3>
			{#if profile.health}
				{@const health = profile.health}
				<div class="d-flex align-items-baseline gap-3 flex-wrap mb-3">
					<span class="h2 mb-0">{health.score.toFixed(0)}<span class="fs-6 text-secondary"> / 100</span></span>
					<span class="badge {HEALTH_TIER_BADGE_CLASSES[health.tier]}"
						>{HEALTH_TIER_LABELS[health.tier]}</span
					>
					{#if profile.feedstocks.length > 1}
						<span class="small text-secondary">scored from {health.feedstock}-feedstock</span>
					{/if}
				</div>
				{#if health.exempt}
					<p class="small text-secondary">
						This feedstock is on the exemption list (it never goes stale by design, or is
						under constant attention but hard to build), so it is never labelled.
					</p>
				{/if}
				<div class="row mb-0">
					{#each Object.keys(HEALTH_COMPONENT_LABELS) as key (key)}
						{@const component = key as keyof typeof HEALTH_COMPONENT_LABELS}
						<div class="col-12 col-md-6 p-2 pe-5">
							<div class="d-flex justify-content-between small mb-2">
								<span class="fw-semibold">{HEALTH_COMPONENT_LABELS[component]}</span>
								<span class="text-secondary">{Math.round(health.components[component].score * 100)}%</span>
							</div>
							<div class="progress" style="height: 10px" aria-hidden="true">
								<div
									class="progress-bar bg-primary"
									style="width: {Math.round(health.components[component].score * 100)}%"
								></div>
							</div>
							<div class="small text-secondary mt-1 ps-2 pe-2">
							    {describeComponent(component, health)}
							</div>
						</div>
					{/each}
				</div>
			{:else}
				<p class="small text-secondary mb-0">
					Health signals are only collected for the most-downloaded and most-depended-on
					feedstocks, so there is no score for this package. That says nothing about how
					well it is maintained.
				</p>
			{/if}
		</div>
	</div>

	<div class="row row-cols-lg-auto g-2 align-items-center mt-4 mb-4">
		<div class="col-12">
			<div class="input-group input-group-sm">
				<label class="input-group-text" for="package-version-select">Version</label>
				<select
					id="package-version-select"
					class="form-select"
					disabled={versionsLoading || availableVersions.length === 0}
					bind:value={selectedVersion}
				>
					{#each availableVersions as version (version)}
						<option value={version}>{version}</option>
					{/each}
				</select>
			</div>
		</div>
		<div class="col-12">
			<div class="input-group input-group-sm">
				<label class="input-group-text" for="package-platform-select">Platform</label>
				<select
					id="package-platform-select"
					class="form-select"
					disabled={platformsLoading || availablePlatforms.length === 0}
					bind:value={selectedPlatform}
				>
					{#each availablePlatforms as platform (platform)}
						<option value={platform}>{platform}</option>
					{/each}
				</select>
			</div>
		</div>
		{#if versionsError}
			<div class="col-12">
				<p class="error small mb-0">Could not load versions: {versionsError}</p>
			</div>
		{:else if platformsError}
			<div class="col-12">
				<p class="error small mb-0">Could not load platforms: {platformsError}</p>
			</div>
		{/if}
	</div>

	<div class="row mt-4">
		<div class="col-lg-7 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-3">About</h3>
					<PackageAbout version={selectedVersion} about={profile.about} />
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
					<DependencyTree
						packageName={profile.name}
						version={selectedVersion}
						platform={selectedPlatform}
						onResolved={(count) => (liveDirectDependencyCount = count)}
					/>
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
								class="btn btn-success btn-sm rounded-pill text-decoration-none">{dep}</a
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
