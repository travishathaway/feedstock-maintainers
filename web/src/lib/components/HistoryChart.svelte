<script lang="ts">
	import { Line } from 'svelte-chartjs';
	import {
		Chart as ChartJS,
		CategoryScale,
		LinearScale,
		PointElement,
		LineElement,
		Tooltip,
		Legend,
		Filler
	} from 'chart.js';

	ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, Filler);

	interface Props {
		labels: string[];
		values: number[];
		label: string;
	}

	let { labels, values, label }: Props = $props();

	// conda-forge teal ($primary in bootstrap-theme.scss) -- used as a static fallback during SSR,
	// where `document` doesn't exist yet; overwritten on the client with whatever --bs-primary
	// actually resolves to, so a theme change to bootstrap-theme.scss doesn't also require
	// updating this component.
	const FALLBACK_PRIMARY_HEX = '#00695c';

	function hexToRgba(hex: string, alpha: number): string {
		const match = /^#?([0-9a-f]{6})$/i.exec(hex);
		if (!match) {
			return `rgba(0, 105, 92, ${alpha})`; // FALLBACK_PRIMARY_HEX as rgba
		}
		const value = parseInt(match[1], 16);
		const r = (value >> 16) & 255;
		const g = (value >> 8) & 255;
		const b = value & 255;
		return `rgba(${r}, ${g}, ${b}, ${alpha})`;
	}

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
				label,
				data: values,
				borderColor: primaryColor,
				backgroundColor: hexToRgba(primaryColor, 0.15),
				pointBackgroundColor: primaryColor,
				pointRadius: 2,
				pointHoverRadius: 4,
				borderWidth: 2,
				fill: true,
				tension: 0.25
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
			y: { beginAtZero: true },
			x: {
			    ticks: {
                    callback: function(val, index) {
                        let date = new Date(Date.parse(this.getLabelForValue(val)));

                        if ( date.getMonth() === 0 ) {
                           return date.getFullYear();
                        } else {
                            return null;
                        }
                    }
                }
			}
		}
	};
</script>

<div class="history-chart">
	<Line {data} {options} />
</div>

<style>
	.history-chart {
		position: relative;
		width: 100%;
		height: 100%;
		min-height: 240px;
	}
</style>
