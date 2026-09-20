<script lang="ts">
	import { Bar } from 'svelte-chartjs';
	import {
		Chart as ChartJS,
		CategoryScale,
		LinearScale,
		BarElement,
		Tooltip,
		Legend
	} from 'chart.js';

	ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend);

	interface Props {
		labels: string[];
		values: number[];
	}

	let { labels, values }: Props = $props();

	// conda-forge teal ($primary in bootstrap-theme.scss) -- used as a static fallback during SSR,
	// where `document` doesn't exist yet; overwritten on the client with whatever --bs-primary
	// actually resolves to (see HistoryChart.svelte for the same pattern).
	const FALLBACK_PRIMARY_HEX = '#00695c';

	function readPrimaryColor(): string {
		if (typeof window === 'undefined') return FALLBACK_PRIMARY_HEX;
		const value = getComputedStyle(document.documentElement).getPropertyValue('--bs-primary').trim();
		return value || FALLBACK_PRIMARY_HEX;
	}

	const primaryColor = readPrimaryColor();

	const data = $derived({
		labels,
		datasets: [
			{
				label: 'Downloads',
				data: values,
				backgroundColor: primaryColor,
				borderRadius: 3
			}
		]
	});

	const options = {
		responsive: true,
		maintainAspectRatio: false,
		plugins: {
			legend: { display: false }
		},
		scales: {
			y: { beginAtZero: true }
		}
	};
</script>

<div class="downloads-chart">
	<Bar {data} {options} />
</div>

<style>
	.downloads-chart {
		position: relative;
		width: 100%;
		height: 240px;
	}
</style>
