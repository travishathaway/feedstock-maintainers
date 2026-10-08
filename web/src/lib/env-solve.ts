import { simpleSolve } from '@conda-org/rattler';
import {
	DEFAULT_CHANNELS,
	virtualPackagesForSelection
} from '$lib/dependency-tree';
import { specName, type SolvePlatform } from '$lib/env-spec';

export interface SolvedEnvironmentPackage {
	name: string;
	version: string;
	build: string;
	requested: boolean;
}

/** Maps a channel shortname ("conda-forge") to a prefix.dev URL; full URLs pass through. */
function channelUrl(channel: string): string {
	return /^https?:\/\//.test(channel) ? channel : `https://prefix.dev/${channel}`;
}

export async function solveEnvironment(
	specs: string[],
	channels: string[],
	platform: SolvePlatform
): Promise<SolvedEnvironmentPackage[]> {
	const solved = await simpleSolve(
		specs,
		channels.length ? channels.map(channelUrl) : DEFAULT_CHANNELS,
		[platform, 'noarch'],
		virtualPackagesForSelection(platform)
	);
	const requested = new Set(specs.map((spec) => specName(spec).toLowerCase()));
	return solved
		.filter((pkg) => !pkg.packageName.startsWith('__'))
		.map((pkg) => ({
			name: pkg.packageName,
			version: pkg.version,
			build: pkg.build,
			requested: requested.has(pkg.packageName.toLowerCase())
		}));
}
