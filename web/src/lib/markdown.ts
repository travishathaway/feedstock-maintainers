import { marked } from 'marked';
import sanitizeHtml from 'sanitize-html';

// breaks: false (the default) so a single hard-wrapped newline in recipe text reflows into the
// paragraph like normal markdown, instead of forcing a line break at every wrap point.
marked.use({ gfm: true, breaks: false });

const allowedTags = [
	'p',
	'br',
	'hr',
	'strong',
	'em',
	'b',
	'i',
	'u',
	's',
	'del',
	'a',
	'ul',
	'ol',
	'li',
	'blockquote',
	'code',
	'pre',
	'h5',
	'table',
	'thead',
	'tbody',
	'tr',
	'th',
	'td',
	'img'
];

const sanitizeOptions: sanitizeHtml.IOptions = {
	allowedTags,
	allowedAttributes: {
		a: ['href', 'title', 'target', 'rel'],
		img: ['src', 'alt', 'title']
	},
	allowedSchemes: ['http', 'https', 'mailto'],
	transformTags: {
		a: sanitizeHtml.simpleTransform('a', {
			target: '_blank',
			rel: 'noreferrer'
		}),
		// Recipe descriptions can bring their own document structure (h1, h2, ...); flattening
		// everything to h5 keeps headings from overpowering the surrounding page layout.
		h1: 'h5',
		h2: 'h5',
		h3: 'h5',
		h4: 'h5',
		h6: 'h5'
	}
};

/** Renders a block of markdown (recipe `about.description`) to sanitized HTML. */
export function renderMarkdown(text: string): string {
	const html = marked.parse(text, { async: false }) as string;
	return sanitizeHtml(html, sanitizeOptions);
}

/** Renders a single line of markdown (recipe `about.summary`) without a wrapping <p>. */
export function renderInlineMarkdown(text: string): string {
	const html = marked.parseInline(text, { async: false }) as string;
	return sanitizeHtml(html, sanitizeOptions);
}
