# README research and editorial decisions

Reviewed October 25, 2026. These are presentation references, not validation of Collex's commercial claims. No project prose or code was copied.

| Project | Winner evidence | Presentation pattern used |
|---|---|---|
| [BlackBox](https://github.com/alejandro-publius/blackbox-datahub) | [Devpost grand-prize entry](https://devpost.com/software/project-blackbox) | Short judge path, inspectable evidence, separation of real execution and synthetic inputs. |
| [OpenCodeReview](https://github.com/victortong-git/open-code-review/blob/main/README.md) | [NVIDIA winner list](https://developer.nvidia.com/agentiq-hackathon) | Explain architectural layers, technology choices, and model configuration. |
| [cuOptIQ](https://github.com/SaiTarunChandrupatla/cuOptIQ) | [NVIDIA winner list](https://developer.nvidia.com/agentiq-hackathon) | Put prerequisites, setup, and concrete interactions within reach. |
| [VendorGuard](https://github.com/experienceswithanishh/vendorguard-copilot-studio) | [Microsoft winner list](https://microsoft.github.io/agent-academy/events/hackathon/) | Start with a business trigger and workflow; tie components to user tasks. |

Awards do not prove README formatting caused a win. Current READMEs may include edits after judging.

## Collex-specific decisions

- Lead with the neglected small-invoice workflow and the team's build-versus-buy hypothesis.
- Link demo, setup, architecture, and evidence rather than burying them in role handoffs.
- Preserve the previous README in IMPLEMENTATION.md. Its role-oriented notes are historical; the new root README describes the integrated three-service setup.
- Explain both model paths: direct drafting and OpenClaw sandbox chat. Do not invent the deployed DeepSeek tag; it is absent from the inspected committed configuration.
- Document current uploads, the Role B adapter, three-invoice fixture, and replacement versus merge semantics.
- Label synthetic eligibility counts separately from model throughput, recovery, ROI, and traction.
- Use the [actual event's criteria](https://luma.com/builde-ixyz): technical execution, business value, local-first design, demo quality. The host list is not a verified judge roster.
- Do not invent team identities, award badges, certification, video URLs, or screenshots. The team still needs to attach its final recording and a real product image.
