# PropioQA Apple-Style Restyle — Design Note (approved 2026-07-19)

User decisions: follow-system appearance with light-first polish; reskin + small structural tweaks; zero functional/keyboard changes.

1. **Tokens**: light = apple.com grays (#f5f5f7 bg, white cards, #1d1d1f text, #86868b secondary, #0071e3 accent, hairlines rgba(0,0,0,.08)); dark = iOS true grays (#161617/#1d1d1f/#2c2c2e layers, #0a84ff accent). Default follows `prefers-color-scheme`; topbar button cycles auto→light→dark.
2. **Topbar**: 52px frosted glass (backdrop-filter blur+saturate) + hairline; tabs become an iOS segmented control.
3. **Type**: -apple-system/SF stack, negative tracking on headings; mono only for task ids/sha/timer; section labels 11px gray uppercase (non-mono).
4. **Components**: primary buttons = blue capsules (980px radius); cards 16px radius + soft shadow (light) / hairline (dark); chips = tinted capsules; rating/severity = iOS segmented; review list = macOS sidebar selection (accent fill row) + disagreement dots (red ≥3 / orange >0); three-way disagree = soft red tint; heatmap cells = `color-mix(var(--accent) α%)` sequential single-hue with luminance-aware text (validated: CVD pass, monotonic lightness, %-labels as low-contrast relief).
5. **Verification**: Chrome live pass, 3 tabs × light+dark screenshots; node --check; pytest untouched; commit+push.
