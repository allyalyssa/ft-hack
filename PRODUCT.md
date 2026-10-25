# Collex

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
Collections staff reviewing overdue invoices, account context, drafts, purchase-order cases, disputes, and write-offs in a daily workspace. Scope confirmed by the user.

## Product Purpose
Find small overdue balances that have not been followed up, prepare collection actions, and let a person review decisions.

## Operating Context
Python standard-library servers serve a local HTML/JavaScript workspace on port 8000 and an approval dashboard on port 8080. The application must work offline without external fonts, scripts, or assets.

## Capabilities and Constraints
Preserve the invoice data, Collex name, approval controls, offline operation, account grouping, dispute evidence, withdrawal reasons, and source-backed assistant. Financial actions remain owned by the existing approval backend. The exported dataset is synthetic; do not imply real customers or measured recovery.

## Brand Commitments
The user requests a complete workspace redesign inspired by Apple's website: clean, chic, restrained, and free of generic AI design patterns. They subsequently renamed it Collex and requested a stock-market-board-like dashboard showing all invoices. Dense financial data takes precedence over summary cards. Plain language and minimal decorative chrome.

## Evidence on Hand
The user requested a very cute Fuggler-inspired chat character. A generated, transparent plush companion lives in app/assets/collex-companion.png; it appears within the assistant panel, separate from the invoice board.

README.md documents workflows. app/index.html implements the collector workspace. data/exports contains the synthetic invoice world. channel/dashboard.py implements approval actions.
