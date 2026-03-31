# Governance

This document describes the governance model for the pywrkr project.

## Overview

pywrkr uses a **BDFL (Benevolent Dictator for Life)** governance model, which is common for small, single-maintainer open source projects. As the project grows, this model may evolve.

## Roles

### Maintainer

The project maintainer ([@kurok](https://github.com/kurok)) has final decision-making authority on:

- Feature direction and roadmap
- Pull request acceptance and merging
- Release scheduling and versioning
- Community moderation

### Contributors

Anyone who contributes to the project through code, documentation, bug reports, or community support. Contributors are recognized in pull request history and release notes.

### How to become a maintainer

There is currently one maintainer. As the project grows, additional maintainers may be invited based on:

- Sustained, high-quality contributions over time
- Demonstrated understanding of the project's goals and architecture
- Active participation in issue triage and code review
- Alignment with the project's values and code of conduct

If you are interested in taking on more responsibility, start by contributing regularly and helping with issue triage and PR reviews.

## Decision Making

- **Day-to-day decisions** (bug fixes, small features, dependency updates) are made by the maintainer directly.
- **Significant changes** (new major features, breaking API changes, architectural shifts) are discussed in GitHub Issues or Discussions before implementation.
- **Community input** is always welcome. Open an issue or start a discussion to propose ideas.

## Releases

Releases follow [Semantic Versioning](https://semver.org/). The maintainer decides when to cut releases based on the changes accumulated on `main`. See the [release process](CONTRIBUTING.md#release-process) for details.

## Code of Conduct

All participants are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). The maintainer is responsible for enforcement.

## Changes to Governance

This governance model may be updated as the project evolves. Changes will be proposed via pull request and discussed openly before adoption.
