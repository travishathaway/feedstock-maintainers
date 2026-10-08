<script lang="ts">
	import { resolve } from '$app/paths';

	let { label = 'How is this health score calculated?' }: { label?: string } = $props();
</script>

<a
	href={resolve('/health')}
	class="health-help text-body-secondary ms-1"
	title={label}
	aria-label={label}
>
	<i class="bi bi-question-circle"></i>
</a>

<style>
	.health-help {
		text-decoration: none;
		font-size: 0.85em;
		vertical-align: middle;
	}
	.health-help:hover {
		color: var(--bs-primary) !important;
	}
</style>
