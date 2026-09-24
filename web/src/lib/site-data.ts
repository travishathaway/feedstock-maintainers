// Set at build time in CI (see .github/workflows/pages.yml). Empty in local
// dev, where the dev server already serves web/static/* from the site root.
const SITE_BASE_URL = import.meta.env.VITE_SITE_BASE_URL ?? '';

export type PackageStatus = 'healthy' | 'watch' | 'at_risk';

export interface TopMaintainer {
	login: string;
	name: string | null;
	avatar_url: string | null;
	feedstock_count: number;
}

export interface PopularPackage {
	name: string;
	downloads_last_month: number;
	maintainer_count: number;
	top_maintainer_login: string | null;
}

export interface MaintainerOverview {
	generated_at: string;
	stats: {
		maintainer_count: number;
		feedstock_count: number;
		avg_maintainers_per_feedstock: number;
		median_maintainers_per_feedstock: number;
		single_maintainer_feedstock_count: number;
		single_maintainer_feedstock_pct: number;
	};
	top_maintainers: TopMaintainer[];
	popular_packages: PopularPackage[];
}

export interface RiskPackage {
	name: string;
	downloads_last_month: number;
	maintainer_count: number;
	status: PackageStatus;
}

export interface TransitiveDependency {
	name: string;
	dependent_feedstocks: number;
	maintainer_count: number;
}

export interface PackageOverview {
	generated_at: string;
	stats: {
		package_count: number;
		packages_le2_maintainers_count: number;
		packages_le2_maintainers_pct: number;
		most_depended_on: { name: string; transitive_dependents: number; maintainer_count: number } | null;
	};
	risk_packages: RiskPackage[];
	transitive_dependencies: TransitiveDependency[];
}

export interface CoMaintainer {
	login: string;
	name: string | null;
	shared_feedstocks: number;
}

export interface EgoNetworkNode {
	key: string;
	label: string;
	distance: number;
}

export interface EgoNetworkEdge {
	source: string;
	target: string;
	weight: number;
}

export interface EgoNetwork {
	nodes: EgoNetworkNode[];
	edges: EgoNetworkEdge[];
}

export interface MaintainerProfile {
	login: string;
	name: string;
	avatar_url: string | null;
	html_url: string | null;
	company: string | null;
	location: string | null;
	feedstock_count: number;
	packages: string[];
	co_maintainer_count: number;
	co_maintainers: CoMaintainer[];
	most_shared_with: { login: string; shared_feedstocks: number } | null;
	ego_network: EgoNetwork;
}

export interface PackageMaintainer {
	login: string;
	name: string | null;
}

export interface FeedstockLink {
	name: string;
	url: string;
}

export interface DownloadsMonthlyPoint {
	month: string;
	downloads: number;
}

export interface PackageProfile {
	name: string;
	maintainers: PackageMaintainer[];
	maintainer_count: number;
	// null means no GitHub activity data has been collected for this package's feedstock(s) yet
	// (only a popularity-thresholded tier is covered) -- distinct from 0, which means activity
	// data exists and says nobody has authored/merged/reviewed a PR in the last 12 months.
	active_maintainer_count: number | null;
	dependent_feedstock_count: number | null;
	direct_dependencies: string[];
	notable_dependents: string[];
	downloads_monthly: DownloadsMonthlyPoint[];
	downloads_last_month: number;
	status: PackageStatus;
	feedstocks: FeedstockLink[];
	license: string | null;
}

function resolveDataUrl(path: string): string {
	if (!SITE_BASE_URL) {
		return `/data/${path}`;
	}
	return `${SITE_BASE_URL.replace(/\/+$/, '')}/data/${path}`;
}

async function loadJson<T>(path: string): Promise<T> {
	const url = resolveDataUrl(path);
	const res = await fetch(url);
	if (!res.ok) {
		throw new Error(`Failed to load ${path} (${res.status})`);
	}
	return res.json();
}

export function loadMaintainerOverview(): Promise<MaintainerOverview> {
	return loadJson('maintainer-overview.json');
}

export function loadPackageOverview(): Promise<PackageOverview> {
	return loadJson('package-overview.json');
}

export function loadMaintainerProfile(login: string): Promise<MaintainerProfile> {
	return loadJson(`maintainers/${encodeURIComponent(login)}.json`);
}

export function loadPackageProfile(name: string): Promise<PackageProfile> {
	return loadJson(`packages/${encodeURIComponent(name)}.json`);
}
