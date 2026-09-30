// Small, dependency-free color helpers shared by chart components. Kept in plain hex/HSL math
// (no color-science library) to match the rest of the codebase's chart color code (see
// HistoryChart.svelte's hexToRgba).

interface Hsl {
	h: number;
	s: number;
	l: number;
}

/** Read a CSS custom property off <html>, e.g. '--bs-primary'. SSR-safe: returns `fallbackHex`
 * when `document` doesn't exist yet (see HistoryChart.svelte's readPrimaryColor, which this
 * generalizes so other components -- e.g. the country map -- can share it). */
export function readCssColor(varName: string, fallbackHex: string): string {
	if (typeof window === 'undefined') return fallbackHex;
	const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
	return value || fallbackHex;
}

function hexToHsl(hex: string): Hsl {
	const match = /^#?([0-9a-f]{6})$/i.exec(hex);
	if (!match) return { h: 0, s: 0, l: 0 };
	const value = parseInt(match[1], 16);
	const r = ((value >> 16) & 255) / 255;
	const g = ((value >> 8) & 255) / 255;
	const b = (value & 255) / 255;
	const max = Math.max(r, g, b);
	const min = Math.min(r, g, b);
	const l = (max + min) / 2;
	if (max === min) return { h: 0, s: 0, l };
	const d = max - min;
	const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
	let h: number;
	if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
	else if (max === g) h = (b - r) / d + 2;
	else h = (r - g) / d + 4;
	return { h: h * 60, s, l };
}

function hslToHex(h: number, s: number, l: number): string {
	const hue = ((h % 360) + 360) % 360;
	const c = (1 - Math.abs(2 * l - 1)) * s;
	const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
	const m = l - c / 2;
	let r = 0;
	let g = 0;
	let b = 0;
	if (hue < 60) [r, g, b] = [c, x, 0];
	else if (hue < 120) [r, g, b] = [x, c, 0];
	else if (hue < 180) [r, g, b] = [0, c, x];
	else if (hue < 240) [r, g, b] = [0, x, c];
	else if (hue < 300) [r, g, b] = [x, 0, c];
	else [r, g, b] = [c, 0, x];
	const toHex = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, '0');
	return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/** A sequential (one-hue, light -> dark) interpolator for magnitude data, per the dataviz
 * skill's color formula: the light end recedes toward the surface ("near zero"), the dark end is
 * the hue itself. `t` is a normalized value in [0, 1].
 *
 * Note on the light end: for a green-dominant hue like this site's teal, HSL lightness is a poor
 * proxy for perceptual luminance (the G channel dominates the WCAG relative-luminance formula),
 * so even a fairly low L still reads as "light" against a white page -- true near-white contrast
 * isn't achievable here without also desaturating away the hue entirely, which would defeat the
 * ramp's purpose. Callers needing map/shape legibility independent of fill lightness (e.g. a
 * choropleth) should pair this with a visibly-contrasting border color, not rely on the fill
 * alone -- see MaintainerCountryMap.svelte's `borderColor`. */
export function sequentialInterpolator(hex: string): (t: number) => string {
	const { h, s } = hexToHsl(hex);
	const lightL = 0.82;
	const darkL = 0.24;
	// Clamp saturation into a muted band regardless of the source hue's own saturation. A
	// green-dominant hue (this site's teal) at full saturation reads as neon/fluorescent well
	// before it reads as "vivid brand color" -- capping it, not just flooring it, is what keeps
	// the ramp muted; a hue that's already muted still gets floored up to something visible.
	const saturation = Math.min(Math.max(s, 0.35), 0.5);
	return (t: number) => {
		const clamped = Math.max(0, Math.min(1, t));
		const l = lightL + (darkL - lightL) * clamped;
		return hslToHex(h, saturation, l);
	};
}
