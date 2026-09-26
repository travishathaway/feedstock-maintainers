<script lang="ts">
	import type { PackageAbout } from '$lib/site-data';

	interface Props {
		version: string | undefined;
		about: PackageAbout | null;
	}

	let { version, about }: Props = $props();
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
		<p class="mb-2">{about.summary}</p>
	{/if}

	{#if about.description}
		<p class="text-body-secondary small mb-3">{about.description}</p>
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
				<span class="badge text-bg-secondary">@{login}</span>
			{/each}
		</div>
	{/if}
{/if}
