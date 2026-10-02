import { loadJson } from './history';

export interface MaintainerCountryCount {
	country: string;
	iso_numeric: string | null;
	iso_alpha2: string | null;
	count: number;
}

export function loadMaintainerCountries(): Promise<MaintainerCountryCount[]> {
	return loadJson('maintainer-countries.json');
}

/** A Unicode flag emoji built from an ISO 3166-1 alpha-2 code (regional indicator symbols), or
 * '' for a missing/invalid code. Rendering depends on the viewer's platform having a color-emoji
 * font -- on a handful of Linux setups without one, this shows as the bare two-letter code
 * instead of a flag. */
export function countryFlagEmoji(alpha2: string | null): string {
	if (!alpha2 || alpha2.length !== 2) return '';
	const codePoints = [...alpha2.toUpperCase()].map(
		(c) => 0x1f1e6 + (c.charCodeAt(0) - 'A'.charCodeAt(0))
	);
	if (codePoints.some((cp) => cp < 0x1f1e6 || cp > 0x1f1ff)) return '';
	return String.fromCodePoint(...codePoints);
}

// ISO 3166-1 numeric codes for the UN M49 "Europe" region (Northern, Southern, Western, and
// Eastern Europe) -- the whole continent, not just the EU. Includes non-EU countries such as the
// UK, Switzerland, Norway, Russia, Ukraine, and Serbia, and the Nordic/Baltic/micro-states;
// excludes Turkey/Cyprus/the Caucasus states, which UN M49 classifies under Western Asia.
// Verified entry-by-entry against pycountry when this list was written; update deliberately if
// country membership or ISO codes ever change.
export const EUROPE_ISO_NUMERIC: ReadonlySet<string> = new Set([
	// Northern Europe
	'248', '208', '233', '234', '246', '831', '352', '372', '833', '832', '428', '440', '578',
	'744', '752', '826',
	// Southern Europe
	'008', '020', '070', '191', '292', '300', '336', '380', '470', '499', '807', '620', '674',
	'688', '705', '724',
	// Western Europe
	'040', '056', '250', '276', '438', '442', '492', '528', '756',
	// Eastern Europe
	'112', '100', '203', '348', '616', '498', '642', '643', '703', '804',
]);

export interface MaintainerCountrySummary {
	countriesRepresented: number;
	pctOutsideUs: number;
	pctOutsideUsEurope: number;
}

export function summarizeMaintainerCountries(
	rows: MaintainerCountryCount[]
): MaintainerCountrySummary {
	const total = rows.reduce((sum, r) => sum + r.count, 0);
	const us = rows.find((r) => r.country === 'United States')?.count ?? 0;
	const europe = rows
		.filter((r) => r.iso_numeric !== null && EUROPE_ISO_NUMERIC.has(r.iso_numeric))
		.reduce((sum, r) => sum + r.count, 0);
	const round1 = (n: number) => Math.round(n * 10) / 10;
	return {
		countriesRepresented: rows.length,
		pctOutsideUs: total ? round1(((total - us) / total) * 100) : 0,
		pctOutsideUsEurope: total ? round1(((total - us - europe) / total) * 100) : 0
	};
}
