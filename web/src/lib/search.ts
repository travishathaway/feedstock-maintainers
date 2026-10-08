import type { SearchIndex } from './site-data';

export interface MaintainerResult {
	kind: 'maintainer';
	login: string;
	name: string;
	avatarUrl: string | null;
}

export interface PackageResult {
	kind: 'package';
	name: string;
}

export type SearchResult = MaintainerResult | PackageResult;

const SEPARATORS = /[\s\-_.]+/;

/**
 * Lower is better; `null` means no match. Exact > prefix > word-prefix > substring. Ties are
 * resolved by the caller via index order (the index is pre-sorted by popularity).
 */
export function scoreText(text: string, query: string): number | null {
	const t = text.toLowerCase();
	if (t === query) return 0;
	if (t.startsWith(query)) return 1;
	if (t.split(SEPARATORS).some((word) => word.startsWith(query))) return 2;
	if (t.includes(query)) return 3;
	return null;
}

function best(a: number | null, b: number | null): number | null {
	if (a === null) return b;
	if (b === null) return a;
	return Math.min(a, b);
}

export function avatarUrlFromId(id: number | null): string | null {
	return id === null ? null : `https://avatars.githubusercontent.com/u/${id}?v=4`;
}

function topMatches<T>(
	items: readonly T[],
	score: (item: T) => number | null,
	limit: number
): T[] {
	const scored: { item: T; score: number; pos: number }[] = [];
	for (let pos = 0; pos < items.length; pos++) {
		const s = score(items[pos]);
		if (s !== null) scored.push({ item: items[pos], score: s, pos });
	}
	scored.sort((a, b) => a.score - b.score || a.pos - b.pos);
	return scored.slice(0, limit).map((s) => s.item);
}

/** Up to `limit` results per kind: maintainers (by login or display name) then packages. */
export function search(index: SearchIndex, rawQuery: string, limit = 5): SearchResult[] {
	const query = rawQuery.trim().toLowerCase();
	if (!query) return [];

	const maintainers = topMatches(
		index.maintainers,
		([login, name]) => best(scoreText(login, query), name ? scoreText(name, query) : null),
		limit
	).map(
		([login, name, avatarId]): MaintainerResult => ({
			kind: 'maintainer',
			login,
			name,
			avatarUrl: avatarUrlFromId(avatarId)
		})
	);
	const packages = topMatches(index.packages, (name) => scoreText(name, query), limit).map(
		(name): PackageResult => ({ kind: 'package', name })
	);
	return [...maintainers, ...packages];
}
