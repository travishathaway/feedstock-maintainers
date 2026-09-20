<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import Avatar from '$lib/components/Avatar.svelte';
	import EgoNetwork from '$lib/components/EgoNetwork.svelte';
	import { loadMaintainerProfile, type MaintainerProfile } from '$lib/site-data';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let profile = $state<MaintainerProfile | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let showAllPackages = $state(false);

	onMount(async () => {
		try {
			profile = await loadMaintainerProfile(data.login);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	function formatNumber(value: number): string {
		return value.toLocaleString();
	}

	const VISIBLE_PACKAGE_COUNT = 8;
</script>

<div class="col-12 mt-4">
	<a href={resolve('/')} class="text-body-secondary small">&larr; Maintainer statistics</a>
</div>

{#if error}
	<p class="error mt-3">{error}</p>
{:else if loading}
	<p class="text-body-secondary mt-3">Loading maintainer profile…</p>
{:else if profile}
	<div class="d-flex align-items-center gap-3 mt-3 flex-wrap">
		<Avatar name={profile.name} avatarUrl={profile.avatar_url} size={64} />
		<div>
			<h2 class="mb-0">@{profile.login}</h2>
			<p class="text-body-secondary small mb-0">
				<!-- Static placeholder -- no per-maintainer join-date data exists yet (plan decision #7). -->
				Maintains conda-forge feedstocks
			</p>
		</div>
	</div>

	<div class="d-flex mt-4">
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(profile.feedstock_count)}</span>
				<p class="mb-0">Feedstocks maintained</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				<span class="h3">{formatNumber(profile.co_maintainer_count)}</span>
				<p class="mb-0">Co-maintainers</p>
			</div>
		</div>
		<div class="col me-4 card text-bg-light">
			<div class="card-body">
				{#if profile.most_shared_with}
					<span class="h5">@{profile.most_shared_with.login}</span>
					<p class="mb-0 small text-secondary">
						Most shared with · {formatNumber(profile.most_shared_with.shared_feedstocks)} feedstocks
					</p>
				{:else}
					<span class="h5">—</span>
					<p class="mb-0 small text-secondary">Most shared with</p>
				{/if}
			</div>
		</div>
	</div>

	<div class="row mt-4">
		<div class="col-lg-6 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-3">Feedstocks maintained</h3>
					<div class="d-flex flex-wrap gap-2">
						{#each (showAllPackages ? profile.packages : profile.packages.slice(0, VISIBLE_PACKAGE_COUNT)) as pkg (pkg)}
							<a
								href={resolve('/packages/[name]', { name: encodeURIComponent(pkg) })}
								class="badge text-bg-secondary text-decoration-none">{pkg}</a
							>
						{/each}
					</div>
					{#if profile.packages.length > VISIBLE_PACKAGE_COUNT}
						<button
							type="button"
							class="btn btn-link btn-sm ps-0 mt-2"
							onclick={() => (showAllPackages = !showAllPackages)}
						>
							{showAllPackages
								? 'Show fewer'
								: `+${profile.packages.length - VISIBLE_PACKAGE_COUNT} more feedstocks`}
						</button>
					{/if}
				</div>
			</div>
		</div>
		<div class="col-lg-6 mb-4">
			<div class="card text-bg-light h-100">
				<div class="card-body">
					<h3 class="h6 mb-3">Co-maintainers</h3>
					<div class="d-flex flex-wrap gap-2">
						{#each profile.co_maintainers as co (co.login)}
							{#if co.name}
								<a
									href={resolve('/maintainers/[login]', { login: encodeURIComponent(co.login) })}
									class="btn btn-outline-secondary btn-sm rounded-pill text-decoration-none"
								>
									@{co.login}
									<span class="text-body-secondary">· {formatNumber(co.shared_feedstocks)}</span>
								</a>
							{:else}
								<span class="btn btn-outline-secondary btn-sm rounded-pill disabled">
									@{co.login}
									<span class="text-body-secondary">· {formatNumber(co.shared_feedstocks)}</span>
								</span>
							{/if}
						{/each}
						{#if profile.co_maintainers.length === 0}
							<p class="text-body-secondary small mb-0">No co-maintainers yet.</p>
						{/if}
					</div>
				</div>
			</div>
		</div>
	</div>

	{#if profile.co_maintainers.length > 0}
		<div class="card text-bg-light mt-2 mb-4">
			<div class="card-body">
				<h3 class="h5">Collaboration network</h3>
				<p class="small text-secondary">
					People who co-maintain at least one feedstock with @{profile.login}, and their own
					co-maintainers. Node color/size indicates distance from @{profile.login}; edge thickness
					reflects shared feedstocks. Click a node to visit that maintainer's profile.
				</p>
				<EgoNetwork login={profile.login} centerLabel={profile.name} egoNetwork={profile.ego_network} />
			</div>
		</div>
	{/if}
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}
</style>
