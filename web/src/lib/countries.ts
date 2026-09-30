import { loadJson } from './history';

export interface MaintainerCountryCount {
	country: string;
	iso_numeric: string | null;
	count: number;
}

export function loadMaintainerCountries(): Promise<MaintainerCountryCount[]> {
	return loadJson('maintainer-countries.json');
}
