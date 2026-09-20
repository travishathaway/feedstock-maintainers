<script lang="ts">
	interface Props {
		name: string;
		avatarUrl?: string | null;
		size?: number;
	}

	let { name, avatarUrl = null, size = 32 }: Props = $props();

	// Fallback for maintainers with no GitHub avatar_url (shouldn't normally happen for a
	// profiled maintainer, but defensive since the field is nullable in the data).
	function initials(text: string): string {
		const parts = text.trim().split(/\s+/).filter(Boolean);
		if (parts.length >= 2) {
			return (parts[0][0] + parts[1][0]).toUpperCase();
		}
		return text.slice(0, 2).toUpperCase();
	}
</script>

{#if avatarUrl}
	<img
		src={avatarUrl}
		alt={name}
		width={size}
		height={size}
		class="rounded-circle flex-shrink-0"
		style="object-fit: cover;"
		loading="lazy"
	/>
{:else}
	<div
		class="rounded-circle d-flex align-items-center justify-content-center flex-shrink-0"
		style="width: {size}px; height: {size}px; background: var(--bs-primary); color: #fff; font-weight: 800; font-size: {Math.max(10, size * 0.35)}px;"
	>
		{initials(name)}
	</div>
{/if}
