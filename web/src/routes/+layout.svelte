<script lang="ts">
	import { onMount } from 'svelte';
	import favicon from '$lib/assets/favicon.svg';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import 'bootstrap/dist/css/bootstrap.min.css';
	import 'bootstrap-icons/font/bootstrap-icons.css';
	import '@fontsource-variable/inter';
	import '@fontsource-variable/jetbrains-mono';
	import '$lib/styles/global.css';

	let { children } = $props();

	onMount(async () => {
		// Bootstrap's JS attaches document-level data-API listeners at import time --
		// must stay client-only, same reason `sigma` is dynamically imported in
		// MaintainerGraph.svelte.
		await import('bootstrap/dist/js/bootstrap.bundle.min.js');
	});
</script>

<svelte:head>
	<title>conda-forge/feedstock-maintainers</title>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="d-flex flex-column vh-100">
	<nav class="navbar navbar-expand-lg navbar-dark bg-dark px-3 flex-shrink-0">
		<a class="navbar-brand font-monospace mb-0 text-truncate" href="/" style="min-width: 0;">
			conda-forge/feedstock-maintainers
		</a>
		<a
			class="btn btn-outline-light btn-sm ms-auto flex-shrink-0"
			href="https://github.com/travishathaway/feedstock-maintainers"
			target="_blank"
			rel="noreferrer"
		>
			<i class="bi bi-github"></i><span class="d-none d-sm-inline"> View on GitHub</span>
		</a>
	</nav>

	<div class="container-fluid flex-grow-1" style="min-height: 0;">
		<div class="row h-100">
			<Sidebar />
			<main class="col-12 col-md-9 h-100 d-flex flex-column py-3">
				{@render children()}
			</main>
		</div>
	</div>
</div>
