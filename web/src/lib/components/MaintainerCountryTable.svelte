<script lang="ts">
	import type { MaintainerCountryCount } from '$lib/countries';
	import { countryFlagEmoji } from '$lib/countries';
	import { formatNumber } from '$lib/format';

	interface Props {
		counts: MaintainerCountryCount[];
	}

	let { counts }: Props = $props();

	const sorted = $derived([...counts].sort((a, b) => b.count - a.count));
	const total = $derived(counts.reduce((sum, row) => sum + row.count, 0));
</script>

<div class="card text-bg-light h-100">
	<div class="card-body">
		<h3 class="h5 mb-3">Maintainers by country</h3>
		<div class="country-table-scroll">
			<table class="table align-middle mb-0">
				<tbody>
					{#each sorted as row (row.country)}
						<tr>
							<td>{countryFlagEmoji(row.iso_alpha2)} {row.country}</td>
							<td class="text-end">{formatNumber(row.count)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<div class="d-flex justify-content-between fw-semibold border-top pt-2 mt-2">
			<span>Total</span>
			<span>{formatNumber(total)}</span>
		</div>
	</div>
</div>

<style>
	.country-table-scroll {
		max-height: 320px;
		overflow-y: auto;
	}
</style>
