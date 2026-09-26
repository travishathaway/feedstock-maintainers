<script lang="ts">
	type FooterLink = {
		label: string;
		href: string;
		external?: boolean;
	};

	type FooterColumn = {
		title: string;
		links: FooterLink[];
	};

	// Mirrors the four footer columns on https://conda-forge.org (Docs / Community /
	// Stay up-to-date / Resources), linking out to the real conda-forge.org pages since
	// this site doesn't host that content itself.
	const columns: FooterColumn[] = [
		{
			title: 'Docs',
			links: [
				{ label: 'Getting started', href: 'https://conda-forge.org/docs/' },
				{ label: 'User docs', href: 'https://conda-forge.org/docs/user/' },
				{ label: 'Maintainer docs', href: 'https://conda-forge.org/docs/maintainer/' },
				{ label: 'CFEPs', href: 'https://conda-forge.org/community/cfep/' },
				{ label: 'Reorganized docs', href: 'https://conda-forge.org/docs/diataxis/' }
			]
		},
		{
			title: 'Community',
			links: [
				{ label: 'About conda-forge', href: 'https://conda-forge.org/community/' },
				{ label: 'Governance', href: 'https://conda-forge.org/community/governance/' },
				{ label: 'Meeting minutes', href: 'https://conda-forge.org/community/minutes/' },
				{ label: 'Get in touch', href: 'https://conda-forge.org/community/getting-in-touch/' }
			]
		},
		{
			title: 'Stay up-to-date',
			links: [
				{ label: 'News', href: 'https://conda-forge.org/news/archive/' },
				{ label: 'Blog', href: 'https://conda-forge.org/blog/archive/' },
				{ label: 'Status', href: 'https://conda-forge.org/status/' },
				{ label: 'Bluesky', href: 'https://bsky.app/profile/conda-forge.org', external: true }
			]
		},
		{
			title: 'Resources',
			links: [
				{ label: 'Status', href: 'https://conda-forge.org/status/' },
				{ label: 'Style guide', href: 'https://conda-forge.org/style-guide/' },
				{ label: 'GitHub', href: 'https://github.com/conda-forge', external: true },
				{ label: 'Zulip', href: 'https://conda-forge.zulipchat.com', external: true },
				{
					label: 'Stack Overflow',
					href: 'https://stackoverflow.com/questions/tagged/conda-forge',
					external: true
				}
			]
		}
	];
</script>

<footer class="site-footer flex-shrink-0">
	<div class="container">
		<div class="row">
			{#each columns as column (column.title)}
				<div class="col-6 col-md-3 mb-4 mb-md-0">
					<div class="site-footer-title">{column.title}</div>
					<ul class="list-unstyled mb-0">
						{#each column.links as link (link.label)}
							<li>
								<a
									class="site-footer-link"
									href={link.href}
									target={link.external ? '_blank' : undefined}
									rel={link.external ? 'noreferrer' : undefined}
								>
									{link.label}
									{#if link.external}
										<svg
											class="site-footer-external-icon"
											width="13.5"
											height="13.5"
											aria-label="(opens in new tab)"
											viewBox="0 0 24 24"
										>
											<path
												fill="currentColor"
												d="M21 13v10h-21v-19h12v2h-10v15h17v-8h2zm3-12h-10.988l4.035 4-6.977 7.07 2.828 2.828 6.977-7.07 4.125 4.172v-11z"
											/>
										</svg>
									{/if}
								</a>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</div>
	</div>
</footer>

<style>
	.site-footer {
		background-color: #303846;
		padding: 2.5rem 0;
	}

	.site-footer-title {
		color: #fff;
		font-family: var(--bs-headings-font-family), var(--bs-body-font-family);
		font-weight: 700;
		margin-bottom: 0.75rem;
	}

	.site-footer-link {
		display: block;
		color: #ebedf0;
		line-height: 2;
		text-decoration: none;
	}

	.site-footer-link:hover,
	.site-footer-link:focus-visible {
		color: #fff;
		text-decoration: underline;
	}

	.site-footer-external-icon {
		margin-left: 0.3rem;
		vertical-align: middle;
	}
</style>
