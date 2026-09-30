<script lang="ts">
	import { onMount } from 'svelte';
	import { Chart } from 'svelte-chartjs';
	import { Chart as ChartJS, Tooltip, Legend } from 'chart.js';
	import {
		ChoroplethController,
		GeoFeature,
		ColorLogarithmicScale,
		ProjectionScale
	} from 'chartjs-chart-geo';
	import { feature } from 'topojson-client';
	import type { Topology, GeometryCollection } from 'topojson-specification';
	import type { Feature, FeatureCollection, Geometry } from 'geojson';
	import { loadMaintainerCountries } from '$lib/countries';
	import { readCssColor, sequentialInterpolator } from '$lib/color';

	ChartJS.register(
		ChoroplethController,
		GeoFeature,
		ColorLogarithmicScale,
		ProjectionScale,
		Tooltip,
		Legend
	);

	// conda-forge teal ($primary in bootstrap-theme.scss) -- SSR fallback, mirrors HistoryChart.
	const FALLBACK_PRIMARY_HEX = '#00695c';

	interface CountryProperties {
		name: string;
	}

	type CountriesTopology = Topology<{
		countries: GeometryCollection<CountryProperties>;
		land: GeometryCollection;
	}>;

	interface ChoroplethDatum {
		feature: Feature<Geometry, CountryProperties>;
		// NaN (not 0) for a country with no maintainer-countries.json entry -- keeps "no data"
		// out of the logarithmic color scale's domain entirely (log(0) is undefined) and lets it
		// render with the scale's distinct `missing` color instead of the lightest ramp step, so
		// "zero" and "a few" don't look identical.
		value: number;
	}

	let loading = $state(true);
	let error = $state<string | undefined>(undefined);
	let chartData = $state<{ datasets: { data: ChoroplethDatum[]; outline: Feature[] }[] } | undefined>(
		undefined
	);
	let nameByIso = new Map<number, string>();

	onMount(async () => {
		try {
			const [counts, worldModule] = await Promise.all([
				loadMaintainerCountries(),
				import('world-atlas/countries-110m.json')
			]);
			const world = worldModule.default as unknown as CountriesTopology;
			const collection = feature(world, world.objects.countries) as FeatureCollection<
				Geometry,
				CountryProperties
			>;

			const countByIso = new Map<number, number>();
			for (const row of counts) {
				if (row.iso_numeric === null) continue;
				countByIso.set(Number(row.iso_numeric), row.count);
				nameByIso.set(Number(row.iso_numeric), row.country);
			}

			const data: ChoroplethDatum[] = collection.features.map((f) => ({
				feature: f,
				value: countByIso.get(Number(f.id)) ?? Number.NaN
			}));

			chartData = {
				datasets: [{ data, outline: collection.features }]
			};
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});

	const accentColor = readCssColor('--bs-primary', FALLBACK_PRIMARY_HEX);
	// The theme's --bs-border-color (#dee2e6) is too pale to read as a country outline on this
	// map -- shapes need to stay distinguishable even where the fill itself is nearly white (a
	// country with very few maintainers), which fill contrast alone can't guarantee. A medium
	// gray (Bootstrap's own $gray-400) gives real outline contrast without competing with the
	// color ramp.
	const borderColor = '#adb5bd';
	const missingColor = readCssColor('--bs-secondary-bg', '#e9ecef');

	const options = {
		responsive: true,
		maintainAspectRatio: false,
		showOutline: true,
		plugins: {
			legend: { display: false },
			tooltip: {
				callbacks: {
					label: (context: { raw: unknown }) => {
						const point = context.raw as ChoroplethDatum;
						const iso = Number(point.feature.id);
						const name = nameByIso.get(iso) ?? point.feature.properties?.name ?? 'Unknown';
						const count = point.value;
						if (Number.isNaN(count)) return `${name}: no known maintainers`;
						return `${name}: ${count} maintainer${count === 1 ? '' : 's'}`;
					}
				}
			}
		},
		scales: {
			// `axis` is required here (even though a geo chart has no real Cartesian x/y axes):
			// Chart.js's core config merge (determineAxis in chart.js's config.js) looks it up on
			// each scale's own options before falling back to registry defaults, and crashes if
			// none of those fallbacks resolve for a non-x/y-named scale id like "projection"/"color".
			projection: {
				axis: 'x' as const,
				projection: 'naturalEarth1' as const
			},
			color: {
				axis: 'x' as const,
				// Logarithmic, not linear: maintainer counts per country are extremely
				// long-tailed (the US is ~3x the #2 country and ~20x the median), so a linear
				// scale crushes all but a handful of countries into the same near-invisible
				// light step. Log spreads mid-sized countries across the visible range instead.
				type: 'colorLogarithmic' as const,
				interpolate: sequentialInterpolator(accentColor),
				missing: missingColor,
				legend: {
					position: 'bottom-right' as const
				},
				// Works around a real chartjs-chart-geo 4.3.6 / chart.js 4.5.1 incompatibility:
				// ColorLogarithmicScale declares a static `descriptors._scriptable` that's meant
				// to exempt `interpolate` from Chart.js's generic "function-valued option ->
				// call it with a scriptable context" resolution, but chart.js's resolver only
				// ever reads `_scriptable` off the *merged options object* (see
				// `_descriptors()`/`_resolveWithContext()` in chart.js's helpers.dataset chunk),
				// never off a scale class's separate static `.descriptors` -- so the exemption
				// never actually takes effect, and `interpolate` gets invoked with a Chart.js
				// context object instead of the normalized number it expects (crashing inside
				// sequentialInterpolator's Math.min/max). Re-declaring the same exemption here,
				// directly on our own scale options, makes it part of that merged object so
				// chart.js's resolver actually sees it. Verified against the installed package
				// versions directly (not just by reading source): without this, `interpolate`
				// receives a circular context object; with it, real normalized numbers.
				_scriptable: (name: string) => name !== 'interpolate',
				_indexable: false
			}
		},
		elements: {
			geoFeature: {
				borderColor,
				borderWidth: 0.5
			}
		}
	};
</script>

{#if error}
	<p class="error">{error}</p>
{:else if loading}
	<p class="text-body-secondary">Loading maintainer countries…</p>
{:else if chartData}
	<div class="country-map">
		<Chart type="choropleth" data={chartData} {options} />
	</div>
	<p class="small text-body-secondary mt-2 mb-0 text-center">
		*Based on self-reported location on GitHub profiles and may be inaccurate.
	</p>
{/if}

<style>
	.error {
		color: var(--bs-danger);
	}

	.country-map {
		position: relative;
		width: 100%;
		height: 100%;
		min-height: 320px;
	}
</style>
