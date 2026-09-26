export function formatNumber(value: number): string {
	return value.toLocaleString();
}

export function formatPercent(value: number): string {
	return `${value.toLocaleString()}%`;
}

// A license value can be a single SPDX identifier (e.g. "MIT", "Apache-2.0"), but recipes
// sometimes declare a compound SPDX expression (e.g. "MIT OR Apache-2.0") or a "LicenseRef-*"
// custom reference -- neither of those has its own page on spdx.org, so only single identifiers
// are linked.
export function spdxLicenseUrl(license: string): string | null {
	if (/\s/.test(license) || license.startsWith('LicenseRef-')) {
		return null;
	}
	return `https://spdx.org/licenses/${encodeURIComponent(license)}.html`;
}
