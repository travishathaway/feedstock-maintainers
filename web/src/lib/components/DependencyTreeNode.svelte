<script lang="ts">
	import type { DependencyTreeNode } from '$lib/dependency-tree';
	import { resolve } from '$app/paths';
	import Self from './DependencyTreeNode.svelte';

	interface Props {
		node: DependencyTreeNode;
		depth?: number;
	}

	let { node, depth = 0 }: Props = $props();
</script>

{#if node.children.length === 0}
	<div class="tree-leaf">
		{#if node.isVirtual}
			<span class="text-body-secondary">{node.name}</span>
			<span class="badge text-bg-light text-body-secondary border ms-1">virtual package</span>
		{:else if node.alreadyShown}
			<a
				href={resolve('/packages/[name]', { name: encodeURIComponent(node.name) })}
				class="text-decoration-none">{node.name}{node.version ? `==${node.version}` : ''}</a
			>
			<span class="text-body-secondary small ms-1">(see above)</span>
		{:else}
			<a
				href={resolve('/packages/[name]', { name: encodeURIComponent(node.name) })}
				class="text-decoration-none">{node.name}{node.version ? `==${node.version}` : ''}</a
			>
		{/if}
	</div>
{:else}
	<details open={depth < 1}>
		<summary>
			<a
				href={resolve('/packages/[name]', { name: encodeURIComponent(node.name) })}
				class="text-decoration-none">{node.name}=={node.version}</a
			>
		</summary>
		<div class="tree-children">
			{#each node.children as child (child.name)}
				<Self node={child} depth={depth + 1} />
			{/each}
		</div>
	</details>
{/if}

<style>
	details {
		margin: 0.15rem 0;
	}

	summary {
		cursor: pointer;
	}

	summary::marker {
		color: var(--bs-secondary-color, #6c757d);
	}

	.tree-children {
		margin-left: 1.25rem;
		border-left: 1px solid var(--bs-border-color, #dee2e6);
		padding-left: 0.75rem;
	}

	.tree-leaf {
		margin: 0.15rem 0 0.15rem 1.1rem;
	}
</style>
