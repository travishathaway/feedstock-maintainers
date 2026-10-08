<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Avatar from './Avatar.svelte';
	import { search, type SearchResult } from '$lib/search';
	import { loadSearchIndex, type SearchIndex } from '$lib/site-data';

	let query = $state('');
	let index = $state<SearchIndex | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let open = $state(false);
	let active = $state(0);

	const results = $derived(index && open ? search(index, query) : []);
	const listId = 'site-search-results';

	// Lazy: ~hundreds of KB, so only fetched once the visitor shows intent.
	async function ensureIndex() {
		if (index || loading) return;
		loading = true;
		failed = false;
		try {
			index = await loadSearchIndex();
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	function onFocus() {
		open = true;
		void ensureIndex();
	}

	function optionId(i: number): string {
		return `site-search-option-${i}`;
	}

	function select(result: SearchResult) {
		open = false;
		query = '';
		if (result.kind === 'maintainer') {
			void goto(resolve('/maintainers/[login]', { login: encodeURIComponent(result.login) }));
		} else {
			void goto(resolve('/packages/[name]', { name: encodeURIComponent(result.name) }));
		}
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			open = false;
		} else if (event.key === 'ArrowDown') {
			event.preventDefault();
			open = true;
			if (results.length) active = (active + 1) % results.length;
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			if (results.length) active = (active - 1 + results.length) % results.length;
		} else if (event.key === 'Enter' && results[active]) {
			event.preventDefault();
			select(results[active]);
		}
	}

	function onFocusOut(event: FocusEvent) {
		const next = event.relatedTarget as Node | null;
		if (!next || !(event.currentTarget as HTMLElement).contains(next)) open = false;
	}
</script>

<div class="site-search-strip bg-white border-bottom py-3 flex-shrink-0">
	<div class="container">
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="site-search mx-auto position-relative" onfocusout={onFocusOut}>
			<div class="input-group input-group-lg site-search-group">
				<span class="input-group-text bg-white border-end-0 ps-3">
					<i class="bi bi-search text-secondary"></i>
				</span>
				<input
					type="search"
					class="form-control border-start-0 border-end-0 shadow-none"
					placeholder="Search maintainers or packages…"
					autocomplete="off"
					spellcheck="false"
					role="combobox"
					aria-label="Search maintainers or packages"
					aria-expanded={open && query.trim() !== ''}
					aria-controls={listId}
					aria-autocomplete="list"
					aria-activedescendant={results.length ? optionId(active) : undefined}
					bind:value={query}
					oninput={() => {
						open = true;
						active = 0;
					}}
					onfocus={onFocus}
					onkeydown={onKeydown}
				/>
				<span class="input-group-text bg-white border-start-0 pe-3">
					{#if loading}
						<span class="spinner-border spinner-border-sm text-secondary" aria-hidden="true"></span>
					{:else if query}
						<button
							type="button"
							class="btn-close"
							aria-label="Clear search"
							onclick={() => (query = '')}
						></button>
					{/if}
				</span>
			</div>

			{#if open && query.trim()}
				<ul
					id={listId}
					class="list-group site-search-results shadow position-absolute w-100"
					role="listbox"
				>
					{#each results as result, i (result.kind + ':' + (result.kind === 'maintainer' ? result.login : result.name))}
						<!-- Keyboard handling lives on the combobox input (aria-activedescendant pattern). -->
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<li
							id={optionId(i)}
							role="option"
							aria-selected={i === active}
							class="list-group-item list-group-item-action d-flex align-items-center gap-3"
							class:active-option={i === active}
							onmousedown={(e) => e.preventDefault()}
							onclick={() => select(result)}
							onmousemove={() => (active = i)}
						>
							{#if result.kind === 'maintainer'}
								<Avatar name={result.name || result.login} avatarUrl={result.avatarUrl} size={28} />
								<span class="text-truncate">
									<span class="fw-semibold">{result.name || result.login}</span>
									{#if result.name}<span class="text-secondary ms-2">@{result.login}</span>{/if}
								</span>
								<span class="ms-auto text-secondary small flex-shrink-0">
									<i class="bi bi-person-fill"></i> Maintainer
								</span>
							{:else}
								<i class="bi bi-box-seam fs-5 text-secondary flex-shrink-0 text-center" style="width: 28px;"></i>
								<span class="fw-semibold text-truncate">{result.name}</span>
								<span class="ms-auto text-secondary small flex-shrink-0">Package</span>
							{/if}
						</li>
					{:else}
						<li class="list-group-item text-secondary">
							{#if failed}Search is unavailable right now.{:else if loading}Loading…{:else}No matches for “{query.trim()}”.{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</div>
</div>

<style>
	.site-search {
		max-width: 584px;
	}
	.site-search-group {
		border-radius: 2rem;
		border: 1px solid var(--bs-border-color);
		overflow: hidden;
		transition: box-shadow 0.15s;
	}
	.site-search-group:hover,
	.site-search-group:focus-within {
		box-shadow: 0 1px 6px rgba(32, 33, 36, 0.28);
	}
	.site-search-group :global(.input-group-text),
	.site-search-group :global(.form-control) {
		border: 0;
	}
	.site-search-results {
		top: calc(100% + 0.25rem);
		z-index: 1030;
		max-height: 70vh;
		overflow-y: auto;
		border-radius: 1rem;
	}
	.active-option {
		background-color: var(--bs-tertiary-bg);
	}
</style>
