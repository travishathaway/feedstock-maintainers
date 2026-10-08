<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import MermaidDiagram from '$lib/components/MermaidDiagram.svelte';
	import { HEALTH_TIER_BADGE_CLASSES, HEALTH_TIER_LABELS } from '$lib/health';
	import { loadPackageList, type HealthConfig } from '$lib/site-data';

	let config = $state<HealthConfig | undefined>(undefined);

	onMount(async () => {
		try {
			config = (await loadPackageList()).config ?? undefined;
		} catch {
			// The explanation reads fine without exact numbers; they just show as "…".
		}
	});

	const pct = (key: string) => (config ? `${Math.round((config.weights[key] ?? 0) * 100)}%` : '…');
	const n = (value: number | undefined) => (value === undefined ? '…' : String(value));

	const composition = $derived(`flowchart TD
    R["Recent upkeep activity (${pct('recency')})"] --> SUM
    M["Maintainer coverage (${pct('maintainers')})"] --> SUM
    O["Open pull requests (${pct('open_prs')})"] --> SUM
    I["Open issues (${pct('issues')})"] --> SUM
    SUM["Weighted sum: score 0 to 100"] --> T{"Tier"}
    E["Dependency exposure"] -. "moves the cutoff, not the score" .-> T
    T --> A["Active"]
    T --> Q["Quiet"]
    T --> N["Could use attention"]`);

	const tierFlow = $derived(`flowchart TD
    S(["Feedstock with collected signals"]) --> X{"On the exemption list?"}
    X -- yes --> EX["Not scored"]
    X -- no --> D{"No commits, comments or merged PRs for ${n(config?.dormant_days)}+ days?"}
    D -- no --> Q1{"Score below ${n(config?.quiet_below)}?"}
    Q1 -- yes --> QU["Quiet"]
    Q1 -- no --> AC["Active"]
    D -- yes --> C{"Score below the exposure-adjusted cutoff?"}
    C -- yes --> NA["Could use attention"]
    C -- no --> Q1`);

	const prFlow = `flowchart TD
    PR["Open pull request"] --> DR{"Draft?"}
    DR -- yes --> I1["Ignored"]
    DR -- no --> BOT{"Opened by a bot?"}
    BOT -- no --> HU["Human PR: counts against"]
    BOT -- yes --> MIG{"Migration label or title?"}
    MIG -- yes --> MG["Migration: counts only beyond a small allowance"]
    MIG -- no --> VER{"Looks like a version bump?"}
    VER -- yes --> VU["Version update: neutral"]
    VER -- no --> OT["Other bot PR: neutral"]`;

	const tiers = ['active', 'quiet', 'needs_attention', 'exempt'] as const;
</script>

<div class="offset-1 offset-md-2 col-md-8 col-9" style="max-width: 80ch">
	<h2 class="mt-5">How the health score works</h2>
	<p class="text-body-secondary">
	    The primary goal of the health score is directing the attention of volunteers to feedstocks
	    that need help or maintenance. It can additionally be used by package consumers  to have
	    greater transparency into conda-forge is run and all the work that goes into providing
	    these packages.
	</p>

	<div class="alert alert-warning small" role="note">
		<strong>Remember, it's an indication, not a verdict.</strong> Plenty of healthy feedstocks are quiet
		because the software is finished, and the score can be wrong. It is deliberately worded
		neutrally, only ever flags feedstocks with no commits, comments or merged PRs for a long time, and is never
		meant as a stick to beat active maintainers with.
	</div>

	<hr />

	<h3 class="h4 mt-5">What goes into the score</h3>
	<p>
		Four components, each turned into a 0–100% value (higher is healthier) and combined with the
		weights below. The package page shows each one so you can see <em>why</em> a score came out
		the way it did.
	</p>
	<MermaidDiagram chart={composition} title="Components, weights and tiers" />

	<div class="table-responsive">
		<table class="table align-middle">
			<thead>
				<tr><th>Component</th><th class="text-end">Weight</th><th>How it is measured</th></tr>
			</thead>
			<tbody>
				<tr>
					<td class="fw-semibold">Recent upkeep activity</td>
					<td class="text-end">{pct('recency')}</td>
					<td>
						Time since the most recent sign of upkeep: a human commit or comment, or any merged
						PR. A rebuild or version bump that a bot opened and that was merged (for example an
						autotick-bot migration that passed CI and was auto-merged) counts, because it shows the
						feedstock is still being kept current. Bare bot commits and comments (admin
						housekeeping, re-renders) do not. Full marks within {n(config?.recency_full_days)} days,
						falling linearly to zero at {n(config?.recency_zero_days)} days. The latest
						<em>human</em> activity is shown separately on each package page.
					</td>
				</tr>
				<tr>
					<td class="fw-semibold">Maintainer coverage</td>
					<td class="text-end">{pct('maintainers')}</td>
					<td>
						Half from listed maintainers (more is better, saturating at 3), half from maintainers
						actually active in the last 12 months (saturating at 2). A team handle such as
						<code>conda-forge/r</code> counts its current members as people (only the number is
						used; members are never listed). The team itself is not counted as an extra person,
						and the package page still shows that the team manages the recipe.
					</td>
				</tr>
				<tr>
					<td class="fw-semibold">Open pull requests</td>
					<td class="text-end">{pct('open_prs')}</td>
					<td>
						Each open human-authored PR costs {n(config ? config.human_pr_penalty * 100 : undefined)}
						points of this component. Bot migration PRs only count once more than {n(
							config?.migration_pr_free
						)} pile up (a pile of stuck migrations usually means a dormant feedstock). Drafts
						and bot version updates are neutral.
					</td>
				</tr>
				<tr>
					<td class="fw-semibold">Open issues</td>
					<td class="text-end">{pct('issues')}</td>
					<td>
						Deliberately tiny. Open issues are often questions kept open for visibility or
						long-term to-do lists, and say little about health. Saturates at {n(
							config?.issues_saturation
						)}.
					</td>
				</tr>
			</tbody>
		</table>
	</div>

	<h4 class="h5 mt-4">How open pull requests are counted</h4>
	<MermaidDiagram chart={prFlow} title="Which open PRs count against a feedstock" />
	<p class="small text-secondary">
		The bot version-update vs. migration split is a heuristic based on labels and titles. When it
		cannot tell, it errs on the side of not counting the PR.
	</p>

	<h3 class="h4 mt-5">Dependency exposure</h3>
	<p>
		How many other feedstocks depend on a package, directly or through other packages, does
		<strong>not</strong> change its score. It only changes how early a clearly dormant feedstock is
		surfaced: the same low score is flagged sooner for something thousands of packages rely on
		(a transitive dependency nobody installs directly but everything needs) than for something
		nobody depends on. Exposure is log-scaled and reaches its maximum at
		{n(config?.exposure_full_dependents)} dependents.
	</p>

	<h3 class="h4 mt-5">Tiers</h3>
	<MermaidDiagram chart={tierFlow} title="How a score becomes a label" />
	<ul class="list-unstyled">
		{#each tiers as tier (tier)}
			<li class="mb-2">
				<span class="badge {HEALTH_TIER_BADGE_CLASSES[tier]}">{HEALTH_TIER_LABELS[tier]}</span>
				{#if tier === 'active'}
					Looks actively looked after (score at or above {n(config?.quiet_below)}).
				{:else if tier === 'quiet'}
					Lower score, but there has been activity within the last {n(
						config?.dormant_days
					)} days or the score is above the attention cutoff. Nothing to worry about on its own.
				{:else if tier === 'needs_attention'}
					No commits, comments or merged PRs for {n(config?.dormant_days)}+ days <em>and</em> a low score. The cutoff
					is {n(config?.needs_attention_base)}, rising by up to {n(
						config?.needs_attention_exposure_bonus
					)} for heavily depended-on packages.
				{:else}
					Never labelled: feedstocks that never go stale by design (like the pinning and
					repodata-patches feedstocks) or are under constant attention but very hard to build
					{#if config}
						({config.exempt_feedstocks.join(', ')}).
					{/if}
				{/if}
			</li>
		{/each}
	</ul>

	<h3 class="h4 mt-5">Known limitations</h3>
	<ul>
		<li>Only the most-downloaded and most-depended-on feedstocks are scored.</li>
		<li>Activity outside the feedstock repository (such as upstream work) is invisible.</li>
		<li>
			Finished software is quiet by nature. A "Quiet" label is not a problem; "Could use
			attention" only appears alongside a long absence of activity.
		</li>
		<li>
			When one package is built by several feedstocks, the best-scoring one is shown, to give the
			benefit of the doubt.
		</li>
	</ul>

	<p class="mt-4">
		<a href={resolve('/packages')}>&larr; Back to packages</a>
	</p>
</div>
