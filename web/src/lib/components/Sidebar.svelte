<script lang="ts">
	import { onMount } from 'svelte';
	import type TomSelect from 'tom-select';
	import { usernames, selectedUsername, resetViewRequested } from '$lib/stores/graph';

	let mobileSelectEl: HTMLSelectElement;
	let desktopSelectEl: HTMLSelectElement;
	const instances: TomSelect[] = [];

	function resetView() {
		selectedUsername.set(null);
		resetViewRequested.update((n) => n + 1);
	}

	onMount(() => {
		const unsubUsernames = usernames.subscribe(async (names) => {
			if (names.length === 0 || instances.length > 0) return;

			const [{ default: TomSelectCtor }] = await Promise.all([
				import('tom-select'),
				import('tom-select/dist/css/tom-select.bootstrap5.min.css')
			]);

			const options = names.map((login) => ({ value: login, text: login }));
			for (const el of [mobileSelectEl, desktopSelectEl]) {
				instances.push(
					new TomSelectCtor(el, {
						options,
						valueField: 'value',
						labelField: 'text',
						searchField: ['text'],
						maxItems: 1,
						placeholder: 'Search maintainers…',
						create: false,
						plugins: ['clear_button'],
						onChange: (value: string) => selectedUsername.set(value || null)
					})
				);
			}
		});

		const unsubSelected = selectedUsername.subscribe((value) => {
			for (const ts of instances) {
				if (ts.getValue() !== (value ?? '')) ts.setValue(value ?? '', true);
			}
		});

		return () => {
			unsubUsernames();
			unsubSelected();
			for (const ts of instances) ts.destroy();
		};
	});
</script>

{#snippet filterFields(select: (el: HTMLSelectElement) => void, idPrefix: string)}
	<div class="mb-3">
		<label for="{idPrefix}-username-filter" class="form-label small fw-semibold">Username</label>
		<select id="{idPrefix}-username-filter" use:select></select>
	</div>
	<!-- Next filter goes here as another `<div class="mb-3">…</div>` block -->
	<button type="button" class="btn btn-outline-secondary btn-sm w-100" onclick={resetView}>
		<i class="bi bi-arrow-counterclockwise"></i> Reset view
	</button>
{/snippet}

<!-- Mobile trigger -->
<div class="col-12 d-md-none px-3 pt-3">
	<button
		type="button"
		class="btn btn-outline-secondary w-100"
		data-bs-toggle="offcanvas"
		data-bs-target="#filtersSidebar"
		aria-controls="filtersSidebar"
	>
		<i class="bi bi-list"></i> Filters
	</button>
</div>

<!-- Offcanvas (mobile) -->
<div class="offcanvas offcanvas-start" tabindex="-1" id="filtersSidebar" aria-labelledby="filtersSidebarLabel">
	<div class="offcanvas-header">
		<h5 class="offcanvas-title d-flex align-items-center gap-2" id="filtersSidebarLabel">
			<i class="bi bi-funnel"></i> Filters
		</h5>
		<button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>
	</div>
	<div class="offcanvas-body">
		{@render filterFields((el) => (mobileSelectEl = el), 'mobile')}
	</div>
</div>

<!-- Desktop sidebar -->
<div class="col-md-3 d-none d-md-block border-end h-100 overflow-auto py-3">
	<h6 class="text-uppercase text-secondary fw-semibold small mb-3 d-flex align-items-center gap-2">
		<i class="bi bi-funnel"></i> Filters
	</h6>
	{@render filterFields((el) => (desktopSelectEl = el), 'desktop')}
</div>
