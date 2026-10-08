export interface ParsedEnvironment {
	specs: string[];
	channels: string[];
	platforms: string[];
	warnings: string[];
}

type Format = 'yaml' | 'toml' | 'specs';

/** Rewrites a GitHub "blob" page URL to its raw-content equivalent; other URLs pass through. */
export function toRawUrl(input: string): string {
	const url = new URL(input.trim());
	if (url.hostname === 'github.com') {
		const match = url.pathname.match(/^\/([^/]+)\/([^/]+)\/blob\/(.+)$/);
		if (match) {
			return `https://raw.githubusercontent.com/${match[1]}/${match[2]}/${match[3]}`;
		}
	}
	if (url.hostname === 'gist.github.com') {
		const parts = url.pathname.split('/').filter(Boolean);
		if (parts.length === 2) {
			return `https://gist.githubusercontent.com/${parts[0]}/${parts[1]}/raw`;
		}
	}
	return url.toString();
}

/** Guesses the format from a filename when there is one, otherwise from the content. */
export function detectFormat(text: string, filename?: string): Format {
	const lower = filename?.toLowerCase() ?? '';
	if (lower.endsWith('.toml')) return 'toml';
	if (lower.endsWith('.yml') || lower.endsWith('.yaml')) return 'yaml';
	if (/^\s*\[[^\]]+\]\s*$/m.test(text)) return 'toml';
	if (/^\s*(dependencies|channels|name)\s*:/m.test(text)) return 'yaml';
	return 'specs';
}

/** conda-style pins ("numpy=1.26", "numpy=1.26=build") are valid match specs already. */
function normalizeSpec(spec: string): string {
	return spec.trim();
}

function parseSpecLines(text: string): string[] {
	return text
		.split(/\r?\n/)
		.map((line) => line.replace(/\s+#.*$/, '').replace(/^\s*#.*$/, '').replace(/^\s*-\s+/, ''))
		.map((line) => line.trim())
		.filter(Boolean)
		.map(normalizeSpec);
}

function tomlSpec(name: string, value: unknown): string | undefined {
	if (typeof value === 'string') {
		return !value || value === '*' ? name : `${name} ${value}`;
	}
	if (value && typeof value === 'object') {
		const version = (value as Record<string, unknown>).version;
		if (typeof version === 'string' && version !== '*') return `${name} ${version}`;
		return name;
	}
	return undefined;
}

function collectToml(table: unknown, specs: string[]): void {
	if (!table || typeof table !== 'object') return;
	for (const [name, value] of Object.entries(table as Record<string, unknown>)) {
		const spec = tomlSpec(name, value);
		if (spec) specs.push(spec);
	}
}

async function parseToml(text: string): Promise<ParsedEnvironment> {
	const { parse } = await import('smol-toml');
	const doc = parse(text) as Record<string, any>;
	// pyproject.toml nests everything under [tool.pixi]; pixi.toml is top-level.
	const root = doc.tool?.pixi ?? doc;
	const warnings: string[] = [];
	const specs: string[] = [];

	collectToml(root.dependencies, specs);
	if (root.feature && typeof root.feature === 'object') {
		const features = Object.keys(root.feature).filter((key) => root.feature[key]?.dependencies);
		if (features.length) {
			warnings.push(
				`Dependencies from feature tables (${features.join(', ')}) are included as one combined environment.`
			);
			for (const feature of features) collectToml(root.feature[feature].dependencies, specs);
		}
	}
	if (root['pypi-dependencies']) {
		warnings.push('PyPI dependencies are ignored; only conda packages are solved.');
	}

	const meta = root.workspace ?? root.project ?? {};
	return {
		specs,
		channels: (meta.channels ?? []).map((c: unknown) =>
			typeof c === 'string' ? c : String((c as Record<string, unknown>).channel)
		),
		platforms: meta.platforms ?? [],
		warnings
	};
}

async function parseYaml(text: string): Promise<ParsedEnvironment> {
	const { parse } = await import('yaml');
	const doc = (parse(text) ?? {}) as Record<string, any>;
	const warnings: string[] = [];
	const specs: string[] = [];
	for (const entry of doc.dependencies ?? []) {
		if (typeof entry === 'string') specs.push(normalizeSpec(entry));
		else if (entry && typeof entry === 'object' && 'pip' in entry) {
			warnings.push('PyPI (pip) dependencies are ignored; only conda packages are solved.');
		}
	}
	return {
		specs,
		channels: (doc.channels ?? []).filter((c: unknown) => typeof c === 'string'),
		platforms: [],
		warnings
	};
}

export async function parseEnvironment(
	text: string,
	filename?: string
): Promise<ParsedEnvironment> {
	const format = detectFormat(text, filename);
	if (format === 'toml') return parseToml(text);
	if (format === 'yaml') return parseYaml(text);
	return { specs: parseSpecLines(text), channels: [], platforms: [], warnings: [] };
}

/** The bare package name of a match spec ("numpy >=1.2,<2" or "numpy>=1.2" -> "numpy"). */
export function specName(spec: string): string {
	// A "channel::name" prefix may precede the name.
	const withoutChannel = spec.includes('::') ? spec.split('::').pop()! : spec;
	return withoutChannel.trim().split(/[\s=<>!~[]/, 1)[0];
}

export const SELECTABLE_PLATFORMS = [
	'linux-64',
	'linux-aarch64',
	'linux-ppc64le',
	'osx-64',
	'osx-arm64',
	'win-64',
	'win-arm64'
] as const;
export type SolvePlatform = (typeof SELECTABLE_PLATFORMS)[number];

/** Best-effort default from the browser; falls back to linux-64. */
export function detectPlatform(): SolvePlatform {
	if (typeof navigator === 'undefined') return 'linux-64';
	const hint = `${navigator.platform} ${navigator.userAgent}`.toLowerCase();
	const arm = /arm|aarch64/.test(hint);
	if (/win/.test(hint)) return arm ? 'win-arm64' : 'win-64';
	if (/mac|darwin/.test(hint)) return 'osx-arm64';
	return arm ? 'linux-aarch64' : 'linux-64';
}

/** Per-row extras when the package table shows a solved environment. */
export interface InspectInfo {
	version: string;
	requested: boolean;
	scored: boolean;
}
