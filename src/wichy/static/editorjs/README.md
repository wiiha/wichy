# Vendored Editor.js assets

Editor.js and its block tools, vendored as UMD bundles. No build step and no
network access at runtime: the server serves these files directly.

## Files and their globals

| File | Global | Package | Version |
| ---- | ---- | ---- | ---- |
| `editorjs.umd.js` | `EditorJS` | `@editorjs/editorjs` | 2.31.7 |
| `header.umd.js` | `Header` | `@editorjs/header` | 2.8.9 |
| `list.umd.js` | `List` | `@editorjs/list` | 1.10.0 |
| `code.umd.js` | `CodeTool` | `@editorjs/code` | 2.9.4 |
| `quote.umd.js` | `Quote` | `@editorjs/quote` | 2.7.6 |
| `checklist.umd.js` | `Checklist` | `@editorjs/checklist` | 1.6.0 |
| `delimiter.umd.js` | `Delimiter` | `@editorjs/delimiter` | 1.4.2 |
| `custom-blocks.js` | -- | authored here | -- |
| `editorjs-custom-blocks.css` | -- | authored here | -- |

Every global above was confirmed against its own bundle by reading the UMD
assignment (`..., J.EditorJS = ne()`) rather than trusting the package's
documentation. The template still guards each global individually: a wrong or
missing one then fails visibly at page load instead of silently at editor init.

`paragraph` is not vendored -- it is the only block tool built into the core
bundle. `delimiter` is vendored because it is not core: it was removed from the
core repository in 2.31.0.

## Provenance

- Plugin bundles fetched from `https://cdn.jsdelivr.net/npm/<package>@<version>/dist/<file>.umd.js`.
- The core bundle is `ref_code/editorjs_2.31.7.umd.min.js`, verified to be the
  genuine 2.31.7 `editorjs.umd.js` (its body hash matches npm once jsDelivr's
  banner is stripped).
- Core bundle sha256:
  `0d5bc39172212ef26aa0b5d637c603387be4ee0301113e977f090aebfbc29a01`
  (245,139 bytes, 57 lines).

All bundles are minified production builds, with mangled identifiers and no
readable function names. They are vendored as served: do not re-minify them,
and do not expect to read them. Note that each bundle injects its own styles
into `document.head` at load time -- no CSS, font or icon asset ships
separately, which is why `editorjs-custom-blocks.css` is the only stylesheet
here.

## Load order

Core first, then plugins, then the custom block classes, because
`custom-blocks.js` extends the classes the plugins define. See
`src/wichy/templates/notes.html`.
