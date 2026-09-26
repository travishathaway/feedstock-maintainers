import { Gateway, Version, simpleSolve, type SolvedPackage } from '@conda-org/rattler';

export const DEFAULT_CHANNELS = ['https://prefix.dev/conda-forge'];

// Candidate platforms to query when discovering what a package actually offers -- 'noarch' is
// listed first since it's also the preferred default platform (see PackageAbout/DependencyTree
// selection logic), keeping declared order, <select> display order, and preference order all
// consistent. Deliberately excludes rarer conda-forge subdirs (e.g. linux-s390x, linux-riscv64)
// to keep query cost bounded.
export const CANDIDATE_PLATFORMS = [
	'noarch',
	'linux-64',
	'linux-aarch64',
	'linux-ppc64le',
	'osx-64',
	'osx-arm64',
	'win-64',
	'win-arm64'
] as const;
export type PlatformOption = (typeof CANDIDATE_PLATFORMS)[number];

export interface DependencyTreeNode {
	name: string;
	version: string;
	build: string;
	isVirtual: boolean;
	alreadyShown: boolean;
	children: DependencyTreeNode[];
}

export interface DependencyTreeResult {
	root: DependencyTreeNode;
	directDependencyCount: number;
}

/**
 * Extracts the bare package name from a conda matchspec string
 * (e.g. "numpy >=1.20,<2" -> "numpy"). The name is always the first
 * whitespace-separated token in conda's matchspec grammar.
 */
function matchSpecName(dependsEntry: string): string {
	return dependsEntry.trim().split(/\s+/, 1)[0];
}

/**
 * A real conda client (conda/mamba) detects the host system and injects
 * "virtual package" records (__glibc, __unix, __osx, ...) into the solve so
 * that recipes depending on them (e.g. `__glibc >=2.17`) are satisfiable.
 * There is no host system here, so we inject generously permissive stand-ins
 * -- the same technique as conda's CONDA_OVERRIDE_GLIBC/CONDA_OVERRIDE_OSX
 * env vars, used to get reproducible solves without host detection.
 */
function virtualPackagesFor(platform: PlatformOption) {
	const versionsByName: Record<string, string> = platform.startsWith('osx')
		? { __unix: '0', __osx: '14.0' }
		: platform.startsWith('win')
			? { __win: '0' }
			: { __unix: '0', __linux: '5.15.0', __glibc: '2.35' };

	return Object.entries(versionsByName).map(([name, version]) => ({
		build: '0',
		buildNumber: 0n,
		depends: [],
		extraDepends: {},
		filename: `${name}-${version}-0.tar.bz2`,
		packageName: name,
		repoName: 'virtual/',
		subdir: platform,
		url: `https://virtual.invalid/${platform}/${name}-${version}-0.tar.bz2`,
		version
	}));
}

const NON_NOARCH_PLATFORMS = CANDIDATE_PLATFORMS.filter(
	(platform): platform is Exclude<PlatformOption, 'noarch'> => platform !== 'noarch'
);

/**
 * A "noarch" build has no platform of its own, but its dependencies can still
 * be platform-specific (e.g. a noarch Python package depending on a compiled,
 * per-platform library). Solving with only the "noarch" subdir starves the
 * solver of every one of those records and fails. So when the selected
 * platform *is* noarch, query every real architecture's repodata alongside
 * noarch, and offer virtual packages for all of them, so whichever
 * architecture a given dependency actually ships for is resolvable.
 */
function virtualPackagesForSelection(platform: PlatformOption) {
	if (platform !== 'noarch') {
		return virtualPackagesFor(platform);
	}

	const byNameAndVersion = new Map<string, ReturnType<typeof virtualPackagesFor>[number]>();
	for (const arch of NON_NOARCH_PLATFORMS) {
		for (const pkg of virtualPackagesFor(arch)) {
			byNameAndVersion.set(`${pkg.packageName}-${pkg.version}`, pkg);
		}
	}
	return [...byNameAndVersion.values()];
}

// A Gateway caches every repodata record it has fetched, so reusing a single instance across
// calls (e.g. listing versions, then platforms, for the same package) avoids re-downloading
// repodata shards that were already fetched for an earlier query.
let sharedGateway: Gateway | undefined;
function getGateway(): Gateway {
	if (!sharedGateway) {
		sharedGateway = new Gateway();
	}
	return sharedGateway;
}

export async function listAvailableVersions(packageName: string): Promise<string[]> {
	const gateway = getGateway();
	const records = await gateway.query(DEFAULT_CHANNELS, [...CANDIDATE_PLATFORMS], [packageName]);

	const versions = new Map<string, Version>();
	for (const record of records) {
		if (!versions.has(record.version)) {
			versions.set(record.version, new Version(record.version));
		}
	}

	return [...versions.entries()]
		.sort(([, a], [, b]) => b.compare(a))
		.map(([version]) => version);
}

/**
 * Which of CANDIDATE_PLATFORMS actually have a build of `packageName` at `version`, e.g. so a
 * noarch-only package only ever offers "noarch" regardless of which version is selected.
 */
export async function listAvailablePlatforms(
	packageName: string,
	version: string
): Promise<PlatformOption[]> {
	const gateway = getGateway();
	const records = await gateway.query(
		DEFAULT_CHANNELS,
		[...CANDIDATE_PLATFORMS],
		[`${packageName}=${version}`]
	);

	const found = new Set(
		records.filter((record) => record.version === version).map((record) => record.subdir)
	);

	return CANDIDATE_PLATFORMS.filter((platform) => found.has(platform));
}

export async function solveDependencyTree(
	packageName: string,
	version: string,
	platform: PlatformOption
): Promise<DependencyTreeResult> {
	const subdirs: PlatformOption[] =
		platform === 'noarch' ? [...CANDIDATE_PLATFORMS] : [platform, 'noarch'];

	const solved = await simpleSolve(
		[`${packageName}=${version}`],
		DEFAULT_CHANNELS,
		subdirs,
		virtualPackagesForSelection(platform)
	);

	const byName = new Map<string, SolvedPackage>();
	for (const pkg of solved) {
		byName.set(pkg.packageName, pkg);
	}

	const root = byName.get(packageName);
	if (!root) {
		throw new Error(`Solve did not return a resolved record for "${packageName}".`);
	}

	const seen = new Set<string>([packageName]);

	function buildNode(pkg: SolvedPackage): DependencyTreeNode {
		// A single package's `depends` can list the same name more than once
		// (e.g. separate constraints merged from different build variants) --
		// collapse those to one child so the tree doesn't show a dependency
		// twice under the same parent.
		const uniqueNames = [...new Set((pkg.depends ?? []).map(matchSpecName))];

		const children = uniqueNames.map((name): DependencyTreeNode => {
			if (name.startsWith('__')) {
				return {
					name,
					version: '',
					build: '',
					isVirtual: true,
					alreadyShown: false,
					children: []
				};
			}

			const child = byName.get(name);
			if (!child) {
				// Referenced in a matchspec but not part of the resolved set
				// (e.g. an optional/extra dependency not pulled into this solve).
				return {
					name,
					version: '',
					build: '',
					isVirtual: false,
					alreadyShown: false,
					children: []
				};
			}

			if (seen.has(name)) {
				return {
					name: child.packageName,
					version: child.version,
					build: child.build,
					isVirtual: false,
					alreadyShown: true,
					children: []
				};
			}

			seen.add(name);
			return buildNode(child);
		});

		return {
			name: pkg.packageName,
			version: pkg.version,
			build: pkg.build,
			isVirtual: false,
			alreadyShown: false,
			children
		};
	}

	const rootNode = buildNode(root);
	const directDependencyCount = rootNode.children.filter((child) => !child.isVirtual).length;

	return { root: rootNode, directDependencyCount };
}
