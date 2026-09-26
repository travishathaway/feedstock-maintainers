<script lang="ts">
	import { resolve } from '$app/paths';
	import Avatar from '$lib/components/Avatar.svelte';
	import EgoNetwork from '$lib/components/EgoNetwork.svelte';
	import { loadMaintainerProfile, type MaintainerProfile } from '$lib/site-data';
	import { formatNumber } from '$lib/format';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let profile = $state<MaintainerProfile | undefined>(undefined);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);
	let showAllPackages = $state(false);

	$effect(() => {
		const login = data.login;
		profile = undefined;
		error = undefined;
		loading = true;
		showAllPackages = false;
		loadMaintainerProfile(login)
			.then((result) => {
				if (login === data.login) profile = result;
			})
			.catch((e) => {
				if (login === data.login) error = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (login === data.login) loading = false;
			});
	});

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

	<!-- Mobile: condensed table -->
	<div class="card text-bg-light mt-4 d-md-none">
		<table class="table table-sm mb-0 align-middle">
			<tbody>
				<tr>
					<td class="text-body-secondary">Feedstocks maintained</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">{formatNumber(profile.feedstock_count)}</div>
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Co-maintainers</td>
					<td class="text-end">
						<div class="fw-semibold fs-4">{formatNumber(profile.co_maintainer_count)}</div>
					</td>
				</tr>
				<tr>
					<td class="text-body-secondary">Most shared with</td>
					<td class="text-end">
						{#if profile.most_shared_with}
							<div class="fw-semibold fs-5">@{profile.most_shared_with.login}</div>
							<div class="small text-secondary">
								{formatNumber(profile.most_shared_with.shared_feedstocks)} feedstocks
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
		<div class="col card text-bg-light">
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
								class="btn btn-success btn-sm rounded-pill text-decoration-none">{pkg}</a
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
									class="btn btn-primary btn-sm rounded-pill text-decoration-none"
								>
									@{co.login}
									<span>· {formatNumber(co.shared_feedstocks)}</span>
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
				<div class="row">
					<div class="col-md-4 mb-3 mb-md-0">
						<h3 class="h5">Collaboration network</h3>
						<p class="small text-secondary mb-0">
							People who co-maintain at least one feedstock with @{profile.login}, and their own
							co-maintainers. Node color/size indicates distance from @{profile.login}; edge
							thickness reflects shared feedstocks. Click a node to visit that maintainer's
							profile.
						</p>
					</div>
					<div class="col-md-8">
						<EgoNetwork login={profile.login} egoNetwork={profile.ego_network} />
					</div>
				</div>
			</div>
		</div>
	{/if}
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}
</style>
