import type { HealthTier, PackageHealth } from './site-data';

// Deliberately neutral wording: this score is a prompt to look, not a verdict against the people
// maintaining a feedstock. See /health for the full rationale.
export const HEALTH_TIER_LABELS: Record<HealthTier, string> = {
	active: 'Active',
	quiet: 'Quiet',
	needs_attention: 'Could use attention',
	exempt: 'Not scored'
};

export const HEALTH_TIER_BADGE_CLASSES: Record<HealthTier, string> = {
	active: 'text-bg-success',
	quiet: 'text-bg-secondary',
	needs_attention: 'text-bg-warning',
	exempt: 'text-bg-light border'
};

export const HEALTH_COMPONENT_LABELS: Record<keyof PackageHealth['components'], string> = {
	recency: 'Recent upkeep activity',
	maintainers: 'Maintainer coverage',
	open_prs: 'Open pull requests',
	issues: 'Open issues'
};

const DAY_MS = 86_400_000;

/** "3 days ago" / "5 months ago" / "2 years ago", or "unknown" for null. */
export function formatRelativeTime(iso: string | null, now: Date = new Date()): string {
	if (!iso) return 'unknown';
	const days = Math.floor((now.getTime() - new Date(iso).getTime()) / DAY_MS);
	if (days < 1) return 'today';
	if (days < 2) return 'yesterday';
	if (days < 30) return `${days} days ago`;
	if (days < 365) {
		const months = Math.floor(days / 30);
		return `${months} month${months === 1 ? '' : 's'} ago`;
	}
	const years = Math.floor(days / 365);
	return `${years} year${years === 1 ? '' : 's'} ago`;
}

/** One plain-language sentence per component, for the "why this score" breakdown. */
export function describeComponent(
	key: keyof PackageHealth['components'],
	health: PackageHealth
): string {
	switch (key) {
		case 'recency': {
			const days = health.components.recency.value;
			if (days === null) return 'No commits, comments or merged PRs found in the data we collect.';
			const human = health.last_human_activity_at;
			const humanNote =
				human === health.last_activity_at
					? ''
					: human
						? ` Last human activity ${formatRelativeTime(human)}.`
						: ' No human activity found.';
			return `Last activity ${formatRelativeTime(health.last_activity_at)} (commit, comment or merged PR, including automated rebuilds).${humanNote}`;
		}
		case 'maintainers': {
			const { listed, active } = health.components.maintainers.value;
			return active === null
				? `${listed} listed maintainer${listed === 1 ? '' : 's'} (recent activity not collected).`
				: `${listed} listed, ${active} active in the last 12 months.`;
		}
		case 'open_prs': {
			const prs = health.components.open_prs.value;
			return (
				`${prs.human ?? 0} open human PR${prs.human === 1 ? '' : 's'}, ` +
				`${prs.migration ?? 0} bot migration${prs.migration === 1 ? '' : 's'}; ` +
				'drafts and version updates are not counted against it.'
			);
		}
		case 'issues':
			return `${health.components.issues.value} open issue${health.components.issues.value === 1 ? '' : 's'} (very low weight).`;
	}
}
