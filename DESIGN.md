---
name: Collex
description: A restrained, dense workspace for invoice collections and human review.
colors:
  paper: "#fafafa"
  surface: "#fff"
  line: "#e2e2e5"
  line-strong: "#d2d2d7"
  ink: "#1d1d1f"
  muted: "#6e6e73"
  action: "#0071e3"
  action-hover: "#0066cc"
  action-soft: "#f0f4f9"
  positive: "#38684f"
  negative: "#9b3d3f"
  dispute: "#9b3d3f"
  dispute-soft: "#fbf1f0"
  warning: "#775b27"
  warning-soft: "#f8f4ec"
typography:
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "28px"
    fontWeight: 650
    letterSpacing: "-.025em"
  board-title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "24px"
    fontWeight: 650
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    lineHeight: 1.5
  ledger:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
  numeric:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "11px"
    fontWeight: 500
rounded:
  stamp: "4px"
  compact: "6px"
  control: "8px"
  panel: "14px"
spacing:
  small: "8px"
  medium: "12px"
  control: "16px"
  section: "24px"
  gutter: "28px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  board-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.compact}"
    padding: "8px 10px"
  detail-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    padding: "28px"
---

# Design System: Collex

## Overview

**Creative North Star: "Restrained invoice board"**

Collex combines the user's Apple-inspired restraint with the density and scanability of a stock-market board. Cool surfaces, graphite text and compact financial rows keep the invoice ledger central. This is an operational workspace with plain language and minimal decorative chrome.

**Key Characteristics:**

- Dense, aligned financial data.
- Quiet horizontal navigation and flat surfaces.
- Blue actions with semantic red, green and amber status.
- Evidence and human review available from invoice details.

## Colors

### Primary

Action blue identifies primary controls, focus and hovered invoice links; its pale tint supports selected review rows. Workspace tokens use `--action`; the separate approval dashboard uses `--ledger`.

### Neutral

Cool paper frames white working surfaces. Graphite carries primary text, muted gray carries supporting context, and two divider strengths separate rows, fields and table boundaries. Table headers use a subtle gray tone; rows stay white and gain a neutral gray hover.

### Semantic

Green marks paid or approved states; red marks overdue balances or dispute warnings; amber marks review and write-off context. Status text accompanies color. The ledger's negative red and the dispute red are distinct incumbent values.

**The Meaning Rule.** Use red and green for actual collection states, never invented market movement.

## Typography

System sans keeps the offline interface native and restrained. System monospace identifies invoice IDs. Dates, balances and totals use tabular system-sans numerals for comparison. No external fonts are loaded.

The compact board title sits below the larger headings used elsewhere. Ledger body text is denser than general body copy; labels and numeric metadata are smaller. Detail headings use a medium step (22px), while portfolio totals use tabular system sans (22px). On phones the board title reduces to 22px.

## Layout

The workspace uses a broad centered container (maximum 1600px) with 28px desktop gutters and a compact horizontal navigation bar. Portfolio totals form a narrow divided strip above the filter controls and ledger. Desktop ledger rows are 43px high, with sticky column headers and right-aligned numbers. The ledger scrolls within its own bounded region and paginates in groups of 50.

At 1050px the navigation wraps into a horizontally scrollable row and filters take a full line. At 760px the ledger keeps its horizontal scroll, pins the invoice column, places account context beneath its invoice ID, and keeps the adjacent balance visible in the initial viewport. Phone rows grow to 52px; list/detail layouts become one column. At 400px gutters narrow to 18px.

The separate approval dashboard uses a maximum 1480px container, a fact/detail grid that collapses at 1100px, and 16px gutters at 720px.

**The Ledger Rule.** Preserve invoice identity, account context and readable balances when adapting the board to phones.

## Elevation & Depth

Working surfaces are flat, separated by borders, alternate row tones and white against cool paper. Shadows identify overlays: the workspace Ask panel uses a broad left shadow; the approval dashboard also uses shadows for its rejection popover and temporary notification. Ordinary detail panels have no shadow.

## Shapes

Compact controls use modest corners; regular buttons and fields use the control radius. Detail panels use the larger panel radius. The ledger itself is rectangular, with thin horizontal rules. Account initials use small neutral circles.

## Components

- **Buttons:** Blue filled primary actions; graphite invoice links; white bordered secondary actions; red text and a pale red border for danger actions. Hover darkens primary blue or changes the neutral surface. Disabled controls reduce opacity. Visible keyboard focus uses a blue outline.
- **Fields:** White, thin gray borders and compact padding. Board fields use the compact radius; other fields use the control radius. Inputs retain visible labels or accessible names.
- **Navigation:** A graphite masthead holds light gray labels. Hover and active labels become white; the active workspace destination has a white underline. Approval navigation uses a lighter graphite selected surface. Narrow layouts retain horizontal access.
- **Statuses:** Ledger states use quiet text labels, with color reinforcing the written status. Detail lists use lightweight text pills; approval states use compact tinted stamps. Color reinforces the written state.
- **Detail panels:** White bordered containers hold account facts, evidence, drafts and review actions. Supporting lists use tinted selection and neutral hover states.
- **Invoice ledger:** Sortable sticky headers, white rows, neutral gray hover, monospace IDs and tabular system-sans amounts, and explicit empty, loading and error states. Phone invoice cells include secondary account text.
- **Ask panel:** At 1200px and above, chat opens by default as a 360px sidebar beside the working area, with a close control, scrollable conversation and bottom composer. The ledger reflows beside it rather than sitting underneath. At narrower sizes chat is a collapsible panel at most 420px wide. Escape closes it. The plush companion identifies the navigation toggle, chat header, welcome state and agent replies. Reduced-motion preferences disable panel entry animation.

## Do's and Don'ts

### Do:

- **Do** prioritize dense, aligned invoice rows over large summary cards.
- **Do** preserve the offline system-font stack and tabular financial figures.
- **Do** retain readable status words, visible keyboard focus and explicit review controls.
- **Do** keep invoice identity, account context and balance available on phones.

### Don't:

- **Don't** add invented price movement, pulsing decoration or chart filler to the invoice board.
- **Chat companion:** The user-requested Fuggler-inspired plush belongs in the Ask panel only. Use the transparent local `app/assets/collex-companion.png` at 56px in its header (48px on small phones), 144px in its welcome state (128px on small phones), and 24px beside agent reply labels. Keep the financial workspace unchanged. The generation prompt is stored beside the image.
- **Don't** replace restrained chrome with large icon tiles or decorative gradients.
- **Don't** use color alone to convey collection state.

Palette refinement: graphite navigation (#1d1d1f) with light labels, porcelain-white canvas (#fafafa), white ledger surfaces, soft silver separators (#e2e2e5), quiet gray hover (#f7f7f9), and blue (#0071e3) reserved for primary actions and focus. Portfolio totals remain graphite; only written financial status uses muted red, green or amber.
