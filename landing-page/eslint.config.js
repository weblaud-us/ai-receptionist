import js from '@eslint/js';
import astro from 'eslint-plugin-astro';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import { defineConfig, globalIgnores } from 'eslint/config';

export default defineConfig([
  globalIgnores(['dist', '.astro', 'node_modules']),
  js.configs.recommended,
  tseslint.configs.recommended,
  astro.configs.recommended,
  // Accessibility rules on Astro templates (astro/jsx-a11y/*, backed by eslint-plugin-jsx-a11y-x).
  astro.configs['jsx-a11y-recommended'],
  {
    files: ['**/*.{js,mjs,ts}'],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
  {
    files: ['public/**/*.js'],
    languageOptions: { sourceType: 'script', globals: globals.browser },
    rules: { 'no-var': 'off' },
  },
]);
