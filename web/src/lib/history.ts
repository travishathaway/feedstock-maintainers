// Set at build time in CI (see .github/workflows/pages.yml). Empty in local
// dev, where the dev server already serves web/static/* from the site root.
const SITE_BASE_URL = import.meta.env.VITE_SITE_BASE_URL ?? '';

export interface MaintainerHistoryPoint {
	date: string;
	unique_maintainer_count: number;
}

export interface FeedstockCountHistoryPoint {
	date: string;
	feedstock_count: number;
}

function resolveDataUrl(filename: string): string {
	if (!SITE_BASE_URL) {
		return `/data/${filename}`;
	}
	return `${SITE_BASE_URL.replace(/\/+$/, '')}/data/${filename}`;
}

async function loadJson<T>(filename: string): Promise<T> {
	const url = resolveDataUrl(filename);
	const res = await fetch(url);
	if (!res.ok) {
		throw new Error(`Failed to load ${filename} (${res.status})`);
	}
	return res.json();
}

export function loadMaintainerHistory(): Promise<MaintainerHistoryPoint[]> {
	return loadJson('maintainer-history.json');
}

export function loadFeedstockCountHistory(): Promise<FeedstockCountHistoryPoint[]> {
	return loadJson('feedstock-count-history.json');
}
