<script lang="ts">
	import { resolve } from '$app/paths';
	import { renderInlineMarkdown, renderMarkdown } from '$lib/markdown';
	import type { PackageAbout } from '$lib/site-data';

	interface Props {
		version: string | undefined;
		about: PackageAbout | null;
	}

	let { version, about }: Props = $props();

	let expanded = $state(false);
	let isClamped = $state(false);
	let descriptionEl: HTMLDivElement | undefined = $state();

	$effect(() => {
		about?.description;
		expanded = false;
	});

	$effect(() => {
		const text = about?.description;
		if (!descriptionEl || !text || expanded) return;
		isClamped = descriptionEl.scrollHeight > descriptionEl.clientHeight + 1;
	});
</script>

{#if !about}
	<p class="text-body-secondary small mb-0">No package metadata available yet.</p>
{:else}
	{#if version !== undefined && version !== about.version}
		<p class="text-body-secondary small fst-italic mb-2">
			Showing metadata for v{about.version} (latest known) — you're viewing v{version}.
		</p>
	{/if}

	{#if about.summary}
		<p class="mb-2">{@html renderInlineMarkdown(about.summary)}</p>
	{/if}

	{#if about.description}
		<div class="mb-3">
			<div
				bind:this={descriptionEl}
				class="text-body-secondary small mb-1 markdown-body"
				class:description-clamp={!expanded}
			>
				{@html renderMarkdown(about.description)}
			</div>
			{#if isClamped}
				<button
					type="button"
					class="btn btn-link btn-sm p-0 text-decoration-none"
					onclick={() => (expanded = !expanded)}
				>
					{expanded ? 'Show less' : 'Read more...'}
				</button>
			{/if}
		</div>
	{/if}

	<div class="d-flex flex-wrap gap-2 mb-3">
		{#if about.home}
			<a
				href={about.home}
				target="_blank"
				rel="noreferrer"
				class="btn btn-outline-secondary btn-sm text-decoration-none"
			>
				<i class="bi bi-house"></i> Homepage
			</a>
		{/if}
		{#if about.dev_url}
			<a
				href={about.dev_url}
				target="_blank"
				rel="noreferrer"
				class="btn btn-outline-secondary btn-sm text-decoration-none"
			>
				<i class="bi bi-code-slash"></i> Source
			</a>
		{/if}
		{#if about.doc_url}
			<a
				href={about.doc_url}
				target="_blank"
				rel="noreferrer"
				class="btn btn-outline-secondary btn-sm text-decoration-none"
			>
				<i class="bi bi-book"></i> Docs
			</a>
		{/if}
	</div>

	{#if about.recipe_maintainers.length > 0}
		<h4 class="h6 mb-2">Recipe maintainers</h4>
		<div class="d-flex flex-wrap gap-2">
			{#each about.recipe_maintainers as login (login)}
				<a
					href={resolve('/maintainers/[login]', { login: encodeURIComponent(login) })}
					class="btn btn-primary btn-sm rounded-pill text-decoration-none">@{login}</a
				>
			{/each}
		</div>
	{/if}
{/if}

<style>
	.description-clamp {
		display: -webkit-box;
		-webkit-line-clamp: 5;
		line-clamp: 5;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.markdown-body :global(p:last-child) {
		margin-bottom: 0;
	}

	.markdown-body :global(img) {
		max-width: 100%;
	}

	.markdown-body :global(pre) {
		overflow-x: auto;
	}

	.markdown-body :global(table) {
		max-width: 100%;
		display: block;
		overflow-x: auto;
	}
</style>
