<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import HealthHelp from '$lib/components/HealthHelp.svelte';
	import {
		HEALTH_TIER_BADGE_CLASSES,
		HEALTH_TIER_LABELS,
		formatRelativeTime
	} from '$lib/health';
	import { formatNumber } from '$lib/format';
	import { scoreText } from '$lib/search';
	import type { InspectInfo } from '$lib/env-spec';
	import type { HealthTier, PackageListRow } from '$lib/site-data';

	// `inspect` is set when the table shows a solved environment rather than the whole health
	// list: rows then carry a version and a requested/dependency marker, and packages without
	// health data are kept (as "Not scored") instead of being absent.
	let {
		packages,
		inspect
	}: { packages: PackageListRow[]; inspect?: Map<string, InspectInfo> } = $props();

	// A package counts as "transitive only" when at least this share of the feedstocks that
	// depend on it do so only through other dependencies, never directly -- the "hidden"
	// load-bearing packages (see `find_transitive_only_dependencies`).
	const TRANSITIVE_ONLY_MIN_RATIO = 0.9;
	const PAGE_SIZE = 50;

	type SortKey =
		| 'health_score'
		| 'dependent_feedstock_count'
		| 'downloads_last_month'
		| 'last_activity_at'
		| 'active_maintainer_count'
		| 'maintainer_count';
	type SortSpec = { key: SortKey; dir: 1 | -1 };

	// Each preset is an ordered list of keys; later keys break ties in earlier ones.
	const SORT_PRESETS: { id: string; label: string; specs: SortSpec[] }[] = [
		{
			id: 'attention',
			label: 'Needs attention (lowest health, most dependents)',
			specs: [
				{ key: 'health_score', dir: 1 },
				{ key: 'dependent_feedstock_count', dir: -1 }
			]
		},
		{ id: 'health', label: 'Health score (lowest first)', specs: [{ key: 'health_score', dir: 1 }] },
		{
			id: 'dependents',
			label: 'Dependents (most first)',
			specs: [{ key: 'dependent_feedstock_count', dir: -1 }]
		},
		{
			id: 'dependents-health',
			label: 'Dependents (most first), then lowest health',
			specs: [
				{ key: 'dependent_feedstock_count', dir: -1 },
				{ key: 'health_score', dir: 1 }
			]
		},
		{
			id: 'downloads',
			label: 'Downloads (most first)',
			specs: [{ key: 'downloads_last_month', dir: -1 }]
		},
		{ id: 'updated', label: 'Last updated (oldest first)', specs: [{ key: 'last_activity_at', dir: 1 }] },
		{
			id: 'active-maintainers',
			label: 'Active maintainers (fewest first)',
			specs: [{ key: 'active_maintainer_count', dir: 1 }]
		},
		{
			id: 'maintainers',
			label: 'Listed maintainers (fewest first)',
			specs: [{ key: 'maintainer_count', dir: 1 }]
		}
	];

	let query = $state('');
	let tier = $state<HealthTier | 'all'>('all');
	let transitiveOnly = $state(false);
	let requestedOnly = $state(false);
	let sortId = $state('attention');
	let visible = $state(PAGE_SIZE);

	function sortValue(row: PackageListRow, key: SortKey): number | null {
		// Unscored rows have placeholder metrics; treat them as missing so they sort last.
		if (inspect && inspect.get(row.name)?.scored === false) return null;
		if (key === 'last_activity_at') {
			return row.last_activity_at ? new Date(row.last_activity_at).getTime() : null;
		}
		return row[key];
	}

	const filtered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const rows = packages.filter(
			(row) =>
				(tier === 'all' || row.health_tier === tier) &&
				(!requestedOnly || inspect?.get(row.name)?.requested) &&
				(!transitiveOnly ||
					(row.transitive_only_ratio !== null &&
						row.transitive_only_ratio >= TRANSITIVE_ONLY_MIN_RATIO)) &&
				(!q || scoreText(row.name, q) !== null)
		);
		const specs = (SORT_PRESETS.find((preset) => preset.id === sortId) ?? SORT_PRESETS[0]).specs;
		return rows.sort((a, b) => {
			for (const { key, dir } of specs) {
				const av = sortValue(a, key);
				const bv = sortValue(b, key);
				// Missing values always sort last, whichever direction is chosen.
				if (av === null && bv === null) continue;
				if (av === null) return 1;
				if (bv === null) return -1;
				if (av !== bv) return (av - bv) * dir;
			}
			return a.name.localeCompare(b.name);
		});
	});

	// Reset pagination whenever the result set changes shape.
	$effect(() => {
		void [query, tier, transitiveOnly, requestedOnly, sortId];
		visible = PAGE_SIZE;
	});

	const shown = $derived(filtered.slice(0, visible));

	// The header sticks just below the (sticky) site navbar. Its height changes when the navbar
	// wraps on narrow screens, so measure it rather than hard-coding a number.
	let stickyOffset = $state(0);

	onMount(() => {
		const nav = document.querySelector('nav.navbar');
		if (!nav) return;
		const update = () => (stickyOffset = nav.getBoundingClientRect().height);
		update();
		const observer = new ResizeObserver(update);
		observer.observe(nav);
		return () => observer.disconnect();
	});
</script>

<div class="card text-bg-light mt-4">
	<div class="card-body">
		<div class="row g-2 align-items-end mb-3">
			<div class="col-12 col-md-3">
				<label class="form-label small mb-1" for="package-filter-query">Filter by name</label>
				<input
					id="package-filter-query"
					type="search"
					class="form-control form-control-sm"
					placeholder="e.g. numpy"
					bind:value={query}
				/>
			</div>
			<div class="col-6 col-md-2">
				<label class="form-label small mb-1" for="package-filter-tier">Status</label>
				<select id="package-filter-tier" class="form-select form-select-sm" bind:value={tier}>
					<option value="all">All</option>
					{#each Object.entries(HEALTH_TIER_LABELS) as [value, label] (value)}
						<option {value}>{label}</option>
					{/each}
				</select>
			</div>
			<div class="col-6 col-md-4">
				<label class="form-label small mb-1" for="package-sort-key">Sort by</label>
				<select id="package-sort-key" class="form-select form-select-sm" bind:value={sortId}>
					{#each SORT_PRESETS as preset (preset.id)}
						<option value={preset.id}>{preset.label}</option>
					{/each}
				</select>
			</div>
			<div class="col-12 col-md-3">
				<div class="form-check form-switch mb-1">
					{#if inspect}
						<input
							id="package-filter-requested"
							class="form-check-input"
							type="checkbox"
							role="switch"
							bind:checked={requestedOnly}
						/>
						<label class="form-check-label small" for="package-filter-requested">
							Requested packages only
						</label>
					{:else}
						<input
							id="package-filter-transitive"
							class="form-check-input"
							type="checkbox"
							role="switch"
							bind:checked={transitiveOnly}
						/>
						<label class="form-check-label small" for="package-filter-transitive">
							Transitive dependencies only
						</label>
					{/if}
				</div>
			</div>
		</div>

		<p class="small text-secondary mb-2">
			{formatNumber(filtered.length)} of {formatNumber(packages.length)} packages
			{#if transitiveOnly}
				· at least {Math.round(TRANSITIVE_ONLY_MIN_RATIO * 100)}% of dependents reach them only
				indirectly
			{/if}
		</p>

		<div style="--sticky-offset: {stickyOffset}px">
			<table class="table align-middle mb-0">
				<thead>
					<tr>
						<th>Package</th>
						{#if inspect}<th>Version</th>{/if}
						<th>Last updated</th>
						<th class="text-end">Active maint.</th>
						<th class="text-end">Listed maint.</th>
						<th class="text-end">Dependents</th>
						<th class="text-end text-nowrap">Health<HealthHelp /></th>
					</tr>
				</thead>
				<tbody>
					{#each shown as row (row.name)}
						{@const info = inspect?.get(row.name)}
						<tr>
							<td>
								{#if info && !info.scored}
									{row.name}
								{:else}
									<a href={resolve('/packages/[name]', { name: encodeURIComponent(row.name) })}
										>{row.name}</a
									>
								{/if}
								{#if info?.requested}<span class="badge text-bg-light border ms-1">requested</span>{/if}
							</td>
							{#if inspect}<td class="text-secondary text-nowrap">{info?.version}</td>{/if}
							{#if info && !info.scored}
								<td class="text-secondary">—</td>
								<td class="text-end text-secondary">—</td>
								<td class="text-end text-secondary">—</td>
								<td class="text-end text-secondary">—</td>
								<td class="text-end text-nowrap">
									<span class="badge text-bg-light border">Not scored</span>
								</td>
							{:else}
								<td class="text-secondary text-nowrap">{formatRelativeTime(row.last_activity_at)}</td>
								<td class="text-end">
									{row.active_maintainer_count === null
										? '—'
										: formatNumber(row.active_maintainer_count)}
								</td>
								<td class="text-end">{formatNumber(row.maintainer_count)}</td>
								<td class="text-end">
									{row.dependent_feedstock_count === null
										? '—'
										: formatNumber(row.dependent_feedstock_count)}
								</td>
								<td class="text-end text-nowrap">
									<span class="me-1">{row.health_score.toFixed(0)}</span>
									<span class="badge {HEALTH_TIER_BADGE_CLASSES[row.health_tier]}"
										>{HEALTH_TIER_LABELS[row.health_tier]}</span
									>
								</td>
							{/if}
						</tr>
					{:else}
						<tr>
							<td colspan={inspect ? 7 : 6} class="text-center text-body-secondary py-4">
								No packages match these filters.
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		{#if filtered.length > visible}
			<div class="text-center mt-3">
				<button
					type="button"
					class="btn btn-outline-secondary btn-sm"
					onclick={() => (visible += PAGE_SIZE)}
				>
					Show more ({formatNumber(filtered.length - visible)} remaining)
				</button>
			</div>
		{/if}
	</div>
</div>

<style>
	/* Opaque background (matching the card) so rows scrolling underneath don't show through, and
	   a box-shadow instead of a border because borders don't travel with sticky cells. */
	thead th {
		position: sticky;
		top: var(--sticky-offset, 0);
		z-index: 1;
		background-color: var(--bs-light);
		box-shadow: inset 0 -1px 0 var(--bs-border-color);
	}
</style>
