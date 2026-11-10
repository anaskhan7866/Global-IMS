/**
 * Tailwind build configuration.
 *
 * The site's CSS is pre-built ONCE into static/css/app.css (small, cached, instant) instead of
 * being generated in every visitor's browser by the Tailwind CDN script.
 *
 * After changing classes in any template, rebuild with:   npm run build:css
 * (or keep it rebuilding automatically while you work:   npm run watch:css)
 */
module.exports = {
  content: [
    './templates/**/*.html',
    './dashboard/templates/**/*.html',
    './inventory/**/*.py',
    './transactions/**/*.py',
    './dashboard/**/*.py',
  ],
  theme: {
    extend: {
      fontFamily: {
        // Built-in system fonts: nothing to download, text renders immediately.
        sans: ['system-ui', '-apple-system', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', '"Cascadia Mono"', '"SF Mono"', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
};
