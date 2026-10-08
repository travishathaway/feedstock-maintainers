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
	let modalOpen = $state(false);
	let modalInput = $state<HTMLInputElement | null>(null);

	const results = $derived(index && open ? search(index, query) : []);

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

	$effect(() => {
		if (modalOpen) modalInput?.focus();
	});

	function onFocus() {
		open = true;
		void ensureIndex();
	}

	function optionId(prefix: string, i: number): string {
		return `${prefix}-option-${i}`;
	}

	function openModal() {
		modalOpen = true;
		open = true;
		void ensureIndex();
	}

	function closeModal() {
		modalOpen = false;
		open = false;
	}

	function select(result: SearchResult) {
		open = false;
		modalOpen = false;
		query = '';
		if (result.kind === 'maintainer') {
			void goto(resolve('/maintainers/[login]', { login: encodeURIComponent(result.login) }));
		} else {
			void goto(resolve('/packages/[name]', { name: encodeURIComponent(result.name) }));
		}
	}

	function onInput() {
		open = true;
		active = 0;
	}

	function inputAttrs(prefix: string) {
		return {
			type: 'search',
			class: 'form-control border-start-0 border-end-0 shadow-none',
			placeholder: 'Search maintainers or packages…',
			autocomplete: 'off',
			spellcheck: false,
			role: 'combobox',
			'aria-label': 'Search maintainers or packages',
			'aria-expanded': open && query.trim() !== '',
			'aria-controls': `${prefix}-results`,
			'aria-autocomplete': 'list',
			'aria-activedescendant': results.length ? optionId(prefix, active) : undefined
		} as const;
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			closeModal();
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
		if (modalOpen) return;
		if (!next || !(event.currentTarget as HTMLElement).contains(next)) open = false;
	}
</script>

<!-- Shared by the navbar field (large screens) and the modal (small screens). -->
{#snippet field(prefix: string, large: boolean, bindInput: boolean)}
	<div class="input-group site-search-group" class:input-group-lg={large}>
		<span class="input-group-text bg-white border-end-0 ps-3">
			<i class="bi bi-search text-secondary"></i>
		</span>
		{#if bindInput}
			<input
				bind:this={modalInput}
				{...inputAttrs(prefix)}
				bind:value={query}
				oninput={onInput}
				onfocus={onFocus}
				onkeydown={onKeydown}
			/>
		{:else}
			<input
				{...inputAttrs(prefix)}
				bind:value={query}
				oninput={onInput}
				onfocus={onFocus}
				onkeydown={onKeydown}
			/>
		{/if}
		<span class="input-group-text bg-white border-start-0 pe-3">
			{#if loading}
				<span class="spinner-border spinner-border-sm text-secondary" aria-hidden="true"></span>
			{:else if query}
				<button type="button" class="btn-close" aria-label="Clear search" onclick={() => (query = '')}
				></button>
			{/if}
		</span>
	</div>
{/snippet}

{#snippet resultList(prefix: string, floating: boolean)}
	<ul
		id="{prefix}-results"
		class="list-group site-search-results"
		class:shadow={floating}
		class:position-absolute={floating}
		class:w-100={floating}
		class:floating
		role="listbox"
	>
		{#each results as result, i (result.kind + ':' + (result.kind === 'maintainer' ? result.login : result.name))}
			<!-- Keyboard handling lives on the combobox input (aria-activedescendant pattern). -->
			<!-- svelte-ignore a11y_click_events_have_key_events -->
			<li
				id={optionId(prefix, i)}
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
{/snippet}

<!-- Large screens: inline field with a dropdown. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="site-search d-none d-lg-block ms-auto me-3 position-relative" onfocusout={onFocusOut}>
	{@render field('site-search', false, false)}
	{#if open && query.trim() && !modalOpen}
		{@render resultList('site-search', true)}
	{/if}
</div>

<!-- Small screens: a button that opens the search in a modal. -->
<button
	type="button"
	class="btn btn-outline-secondary btn-sm ms-auto me-2 d-lg-none flex-shrink-0"
	aria-label="Search"
	onclick={openModal}
>
	<i class="bi bi-search"></i>
</button>

{#if modalOpen}
	<div class="modal-backdrop fade show"></div>
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="modal fade show d-block"
		tabindex="-1"
		role="dialog"
		aria-modal="true"
		aria-label="Search"
		onclick={(e) => e.target === e.currentTarget && closeModal()}
	>
		<div class="modal-dialog modal-dialog-scrollable">
			<div class="modal-content">
				<div class="modal-body">
					<div class="d-flex align-items-center gap-2 mb-2">
						<div class="flex-grow-1">{@render field('modal-search', false, true)}</div>
						<button type="button" class="btn btn-link text-secondary" onclick={closeModal}>
							Cancel
						</button>
					</div>
					{#if query.trim()}
						{@render resultList('modal-search', false)}
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	.site-search {
		flex: 0 1 360px;
		min-width: 0;
	}
	.site-search-group :global(.form-control) {
		background: #fff;
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
		max-height: 70vh;
		overflow-y: auto;
		border-radius: 1rem;
	}
	.site-search-results.floating {
		top: calc(100% + 0.25rem);
		z-index: 1030;
	}
	.active-option {
		background-color: var(--bs-tertiary-bg);
	}
</style>
