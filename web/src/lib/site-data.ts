// Set at build time in CI (see .github/workflows/pages.yml). Empty in local
// dev, where the dev server already serves web/static/* from the site root.
const SITE_BASE_URL = import.meta.env.VITE_SITE_BASE_URL ?? '';

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

export interface PackageAbout {
	description: string | null;
	summary: string | null;
	home: string | null;
	dev_url: string | null;
	doc_url: string | null;
	recipe_maintainers: string[];
	version: string;
}

export type HealthTier = 'active' | 'quiet' | 'needs_attention' | 'exempt';

/** One component of the health score: its raw `value` (shape varies by component), its 0..1
 *  `score` (1 = healthy) and its `weight` in the composite. */
export interface HealthComponent {
	value: unknown;
	score: number;
	weight: number;
}

export interface OpenPrCounts {
	human?: number;
	draft?: number;
	version_update?: number;
	migration?: number;
	bot_other?: number;
	total_open?: number;
}

export interface PackageHealth {
	/** The feedstock the score came from (a package can be built by several). */
	feedstock: string;
	score: number;
	tier: HealthTier;
	exempt: boolean;
	/** Latest commit, comment or merged PR, including PRs a bot opened and merged. */
	last_activity_at: string | null;
	/** Latest activity with a human involved (commit, comment, or a PR a person authored, merged or approved). */
	last_human_activity_at: string | null;
	components: {
		recency: HealthComponent & { value: number | null };
		maintainers: HealthComponent & { value: { listed: number; active: number | null } };
		open_prs: HealthComponent & { value: OpenPrCounts };
		issues: HealthComponent & { value: number };
	};
	exposure: {
		transitive_dependents: number | null;
		transitive_only_ratio: number | null;
		value: number;
	};
}

export interface PackageProfile {
	name: string;
	// Individually listed maintainers only; team members are never named, only counted.
	maintainers: PackageMaintainer[];
	// Team handles (e.g. "conda-forge/r") that manage the recipe.
	teams: string[];
	// Head count, including members of the teams above.
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
	feedstocks: FeedstockLink[];
	license: string | null;
	about: PackageAbout | null;
	// null means health signals haven't been collected for this package's feedstock(s) (only a
	// popularity-thresholded tier is covered) -- not that the package is unhealthy.
	health: PackageHealth | null;
}

export interface PackageListRow {
	name: string;
	downloads_last_month: number;
	maintainer_count: number;
	active_maintainer_count: number | null;
	last_activity_at: string | null;
	health_score: number;
	health_tier: HealthTier;
	dependent_feedstock_count: number | null;
	transitive_only_ratio: number | null;
}

/** The scoring constants (weights, thresholds, exempt list) emitted by `generate feedstock-health`
 *  so the explainer page never drifts from the code. */
export interface HealthConfig {
	weights: Record<string, number>;
	recency_full_days: number;
	recency_zero_days: number;
	dormant_days: number;
	quiet_below: number;
	needs_attention_base: number;
	needs_attention_exposure_bonus: number;
	exposure_full_dependents: number;
	human_pr_penalty: number;
	migration_pr_penalty: number;
	migration_pr_free: number;
	issues_saturation: number;
	exempt_feedstocks: string[];
}

export interface PackageList {
	generated_at: string;
	config: HealthConfig | null;
	packages: PackageListRow[];
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

export function loadPackageList(): Promise<PackageList> {
	return loadJson('package-list.json');
}

export function loadMaintainerProfile(login: string): Promise<MaintainerProfile> {
	return loadJson(`maintainers/${encodeURIComponent(login)}.json`);
}

export function loadPackageProfile(name: string): Promise<PackageProfile> {
	return loadJson(`packages/${encodeURIComponent(name)}.json`);
}

/** `[login, name ("" if unset), GitHub avatar user id | null]`, most feedstocks first. */
export type SearchMaintainerRow = [string, string, number | null];

export interface SearchIndex {
	maintainers: SearchMaintainerRow[];
	/** Package names, most downloaded last month first. */
	packages: string[];
}

let searchIndexPromise: Promise<SearchIndex> | null = null;

/** Loads `search-index.json` once; a failed load is not cached so the next focus can retry. */
export function loadSearchIndex(): Promise<SearchIndex> {
	if (!searchIndexPromise) {
		searchIndexPromise = loadJson<SearchIndex>('search-index.json').catch((err) => {
			searchIndexPromise = null;
			throw err;
		});
	}
	return searchIndexPromise;
}
