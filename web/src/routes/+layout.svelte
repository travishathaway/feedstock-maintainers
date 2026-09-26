<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import anvilLogo from '$lib/assets/conda-forge-anvil.png';
	import favicon from '$lib/assets/conda-forge-favicon.ico';
	import Footer from '$lib/components/Footer.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import '$lib/styles/bootstrap-theme.scss';
	import 'bootstrap-icons/font/bootstrap-icons.css';
	import '@fontsource-variable/inter';
	import '@fontsource-variable/montserrat';
	import '$lib/styles/global.css';

	let { children } = $props();

	// `page.url.pathname` includes the base path (e.g. "/feedstock-maintainers/packages" in
	// production), so every comparison here must go through `resolve()` too rather than
	// comparing against bare root-relative strings.
	const isMaintainersActive = $derived(
		page.url.pathname === resolve('/') ||
			page.url.pathname.startsWith(`${resolve('/maintainers')}/`)
	);
	const isPackagesActive = $derived(
		page.url.pathname === resolve('/packages') ||
			page.url.pathname.startsWith(`${resolve('/packages')}/`)
	);

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

<div class="d-flex flex-column min-vh-100">
	<nav class="navbar navbar-expand-lg navbar-light sticky-top bg-white border-bottom px-3 flex-shrink-0">
		<a class="navbar-brand d-flex align-items-center gap-2 mb-0 text-truncate" href={resolve('/')} style="min-width: 0;">
			<img src={anvilLogo} alt="conda-forge" width="auto" height="32" class="me-4" />
			<span class="text-truncate" style="font-weight: bold; font-size: 1rem; font-family: 'Montserrat Variable', Montserrat, sans-serif">
			    Feedstock Maintainers
			</span>
		</a>
		<ul class="navbar-nav flex-row gap-3 ms-4">
			<li class="nav-item">
				<a
					class="nav-link"
					class:fw-semibold={isMaintainersActive}
					class:text-primary={isMaintainersActive}
					href={resolve('/')}
				>
					Maintainers
				</a>
			</li>
			<li class="nav-item">
				<a
					class="nav-link"
					class:fw-semibold={isPackagesActive}
					class:text-primary={isPackagesActive}
					href={resolve('/packages')}
				>
					Packages
				</a>
			</li>
		</ul>
		<a
			class="btn btn-outline-secondary btn-sm ms-auto flex-shrink-0"
			href="https://github.com/travishathaway/feedstock-maintainers"
			target="_blank"
			rel="noreferrer"
		>
			<i class="bi bi-github"></i><span class="d-none d-sm-inline"> View on GitHub</span>
		</a>
		<a
			class="btn btn-outline-secondary btn-sm ms-2 flex-shrink-0"
			href="https://conda-forge.org"
			target="_blank"
			rel="noreferrer"
		>
			<i class="bi bi-box-arrow-up-right"></i><span class="d-none d-sm-inline"> conda-forge.org</span>
		</a>
	</nav>

	<div class="container flex-grow-1">
		<div class="row h-100">
			<main class="col-12 h-100 d-flex flex-column py-3">
				{@render children()}
			</main>
		</div>
	</div>

	<Footer />
</div>
