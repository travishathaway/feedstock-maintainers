<script lang="ts">
	import PackageList from '$lib/components/PackageList.svelte';
	import {
		SELECTABLE_PLATFORMS,
		detectPlatform,
		parseEnvironment,
		toRawUrl,
		type InspectInfo,
		type SolvePlatform
	} from '$lib/env-spec';
	import { HEALTH_TIER_LABELS } from '$lib/health';
	import type { HealthTier, PackageListRow } from '$lib/site-data';

	let { packages }: { packages: PackageListRow[] } = $props();

	type Mode = 'paste' | 'upload' | 'url';
	let mode = $state<Mode>('paste');
	let text = $state('');
	let url = $state('');
	let file = $state<File | undefined>(undefined);
	let platform = $state<SolvePlatform>(detectPlatform());
	let solving = $state(false);
	let error = $state<string | undefined>(undefined);
	let warnings = $state<string[]>([]);
	let rows = $state<PackageListRow[] | undefined>(undefined);
	let info = $state<Map<string, InspectInfo>>(new Map());

	const byName = $derived(new Map(packages.map((row) => [row.name, row])));

	const tierCounts = $derived.by(() => {
		const counts: Record<HealthTier, number> = { active: 0, quiet: 0, needs_attention: 0, exempt: 0 };
		for (const row of rows ?? []) counts[row.health_tier] += 1;
		return counts;
	});

	async function readInput(): Promise<{ content: string; filename?: string }> {
		if (mode === 'upload') {
			if (!file) throw new Error('Choose an environment.yml or pixi.toml file first.');
			return { content: await file.text(), filename: file.name };
		}
		if (mode === 'url') {
			if (!url.trim()) throw new Error('Enter a link to an environment file.');
			let raw: string;
			try {
				raw = toRawUrl(url);
			} catch {
				throw new Error('That does not look like a valid URL.');
			}
			let response: Response;
			try {
				response = await fetch(raw);
			} catch {
				throw new Error(
					'Could not fetch that link (the host may not allow cross-origin requests). Try a raw file URL, or paste or upload the file instead.'
				);
			}
			if (!response.ok) {
				throw new Error(`Fetching the link failed (HTTP ${response.status}). Try pasting the file instead.`);
			}
			return { content: await response.text(), filename: new URL(raw).pathname };
		}
		return { content: text };
	}

	async function solve() {
		error = undefined;
		warnings = [];
		rows = undefined;
		solving = true;
		try {
			const { content, filename } = await readInput();
			const parsed = await parseEnvironment(content, filename);
			warnings = parsed.warnings;
			if (parsed.specs.length === 0) throw new Error('No conda dependencies were found.');
			if (parsed.platforms.length && !parsed.platforms.includes(platform)) {
				const preferred = parsed.platforms.find((p): p is SolvePlatform =>
					(SELECTABLE_PLATFORMS as readonly string[]).includes(p)
				);
				if (preferred) platform = preferred;
			}
			// Loaded lazily so rattler's wasm never runs during prerender.
			const { solveEnvironment } = await import('$lib/env-solve');
			const solved = await solveEnvironment(parsed.specs, parsed.channels, platform);

			const nextInfo = new Map<string, InspectInfo>();
			const nextRows: PackageListRow[] = [];
			for (const pkg of solved) {
				const known = byName.get(pkg.name);
				nextInfo.set(pkg.name, { version: pkg.version, requested: pkg.requested, scored: !!known });
				nextRows.push(
					known ?? {
						name: pkg.name,
						downloads_last_month: 0,
						maintainer_count: 0,
						active_maintainer_count: null,
						last_activity_at: null,
						health_score: 0,
						health_tier: 'exempt',
						dependent_feedstock_count: null,
						transitive_only_ratio: null
					}
				);
			}
			info = nextInfo;
			rows = nextRows;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			solving = false;
		}
	}
</script>

<div class="card text-bg-light mt-4">
	<div class="card-body">
		<p class="small text-secondary" style="max-width: 90ch">
			Solve your own environment and see the health of every package in it. Paste match specs or an
			<code>environment.yml</code> / <code>pixi.toml</code>, upload one, or link to one. Solving runs
			in your browser; nothing is uploaded.
		</p>

		<div class="btn-group btn-group-sm mb-3" role="group" aria-label="Input method">
			{#each [['paste', 'Paste'], ['upload', 'Upload'], ['url', 'Link']] as [value, label] (value)}
				<button
					type="button"
					class="btn btn-outline-secondary"
					class:active={mode === value}
					onclick={() => (mode = value as Mode)}>{label}</button
				>
			{/each}
		</div>

		{#if mode === 'paste'}
			<label class="form-label small mb-1" for="inspect-text">Match specs or file contents</label>
			<textarea
				id="inspect-text"
				class="form-control form-control-sm font-monospace mb-3"
				rows="6"
				placeholder={'python 3.12\nnumpy\npandas >=2'}
				bind:value={text}
			></textarea>
		{:else if mode === 'upload'}
			<label class="form-label small mb-1" for="inspect-file">environment.yml or pixi.toml</label>
			<input
				id="inspect-file"
				type="file"
				class="form-control form-control-sm mb-3"
				accept=".yml,.yaml,.toml"
				onchange={(event) => (file = event.currentTarget.files?.[0])}
			/>
		{:else}
			<label class="form-label small mb-1" for="inspect-url">Link to a file</label>
			<input
				id="inspect-url"
				type="url"
				class="form-control form-control-sm mb-1"
				placeholder="https://github.com/org/repo/blob/main/environment.yml"
				bind:value={url}
			/>
			<p class="small text-secondary mb-3">GitHub file links are converted to raw links.</p>
		{/if}

		<div class="row g-2 align-items-end">
			<div class="col-6 col-md-3">
				<label class="form-label small mb-1" for="inspect-platform">Platform</label>
				<select id="inspect-platform" class="form-select form-select-sm" bind:value={platform}>
					{#each SELECTABLE_PLATFORMS as option (option)}
						<option value={option}>{option}</option>
					{/each}
				</select>
			</div>
			<div class="col-auto">
				<button type="button" class="btn btn-primary btn-sm" disabled={solving} onclick={solve}>
					{solving ? 'Solving…' : 'Solve & inspect'}
				</button>
			</div>
		</div>
		{#if solving}
			<p class="small text-secondary mt-2 mb-0">
				Fetching repodata and solving; the first solve can take a few seconds.
			</p>
		{/if}
		{#if error}
			<div class="alert alert-danger mt-3 mb-0 small" style="white-space: pre-wrap">{error}</div>
		{/if}
		{#each warnings as warning (warning)}
			<div class="alert alert-warning mt-3 mb-0 small">{warning}</div>
		{/each}
	</div>
</div>

{#if rows}
	<p class="small text-secondary mt-3 mb-0">
		{rows.length} packages solved for {platform}:
		{#each Object.entries(HEALTH_TIER_LABELS) as [tier, label], i (tier)}
			{i ? ' · ' : ''}{tierCounts[tier as HealthTier]} {label.toLowerCase()}
		{/each}.
		Packages outside the scored list show as "Not scored", which says nothing about their health.
	</p>
	<PackageList packages={rows} inspect={info} />
{/if}
