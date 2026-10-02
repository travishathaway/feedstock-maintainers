<script lang="ts">
	import { onMount } from 'svelte';
	import MaintainerCountryMap from './MaintainerCountryMap.svelte';
	import MaintainerCountryTable from './MaintainerCountryTable.svelte';
	import { loadMaintainerCountries, summarizeMaintainerCountries } from '$lib/countries';
	import { formatNumber, formatPercent } from '$lib/format';
	import type { MaintainerCountryCount } from '$lib/countries';

	let counts = $state<MaintainerCountryCount[]>([]);
	let error = $state<string | undefined>(undefined);
	let loading = $state(true);

	onMount(async () => {
		try {
			counts = await loadMaintainerCountries();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	const summary = $derived(summarizeMaintainerCountries(counts));
</script>

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="text-body-secondary">Loading maintainer countries…</p>
{:else}
	<div class="text-center mb-4 d-none d-md-block">
        <div class="row">
            <div class="col-md-4">
                <div class="card text-bg-light">
                    <div class="card-body">
                        <div class="row">
                            <div class="col-2 col-md-12">
                                <span class="h4">{formatNumber(summary.countriesRepresented)}</span>
                            </div>
                            <div class="col-10 col-md-12">
                                <p class="mb-0">Countries represented</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card text-bg-light">
                    <div class="card-body">
                        <div class="row">
                            <div class="col-3 col-md-12">
                                <span class="h4">{formatPercent(summary.pctOutsideUs)}</span>
                            </div>
                            <div class="col-9 col-md-12">
                                <p class="mb-0">Outside U.S.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card text-bg-light">
                    <div class="card-body">
                        <span class="h4">{formatPercent(summary.pctOutsideUsEurope)}</span>
                        <p class="mb-0">Outside U.S./Europe</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

	<div class="row">
		<div class="col-lg-9 col-12 mb-3">
			<MaintainerCountryMap {counts} />
		</div>
		<div class="col-lg-3 col-12 mb-3">
			<MaintainerCountryTable {counts} />
		</div>
	</div>

	<p class="small text-body-secondary mt-2 mb-0 text-center">
		*Based on self-reported location on GitHub profiles and may be inaccurate.
	</p>
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}
</style>
