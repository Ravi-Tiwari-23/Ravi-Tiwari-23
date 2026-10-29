# Profile statistics methodology

The profile uses one reporting timezone (`Asia/Kolkata`) and a rolling 365-day period ending on the current local date.

## Data sources and counting rules

- **Public repository activity:** GitHub's REST API is paginated until every owner repository is retrieved. The displayed repository count is GitHub's public repository count. Stars and forks exclude the profile repository, archived repositories, forks, and any repository excluded in `config/profile.yaml`.
- **Contribution calendar:** an authenticated GitHub GraphQL `contributionsCollection(from:, to:)` query supplies daily calendar values. The total shown on the streak card is the sum of those daily GitHub-visible values for the selected period.
- **Eligible commit contributions:** `totalCommitContributions` from that same GraphQL collection. This follows GitHub's contribution eligibility rules; it is not a raw commit count, a lifetime count, or a claim about every repository. Results are limited to activity visible to the workflow token.
- **Pull-request contributions:** `totalPullRequestContributions` from the same collection and date range.
- **Current and longest streaks:** consecutive days with at least one GitHub-visible calendar contribution, computed only inside the selected period. If the current day has no contribution yet, the current streak may end on the previous day.
- **Language distribution:** GitHub Linguist byte totals from the REST `/languages` endpoint for every eligible public repository, aggregated after repository pagination. Percentages describe checked-in code composition, not proficiency or commit volume.

GitHub may expose a restricted/private contribution count without repository details. The generator never publishes private repository names or other private activity details. If the workflow token cannot retrieve a supported value, the profile does not invent or infer it.

## Refresh and failure behavior

`.github/workflows/update-profile.yml` runs every six hours and supports manual dispatch. It fetches data, generates the light and dark SVG cards, validates the README and every SVG, and commits only when tracked output changed. A fetch or generation error stops before publication, so previously valid committed cards remain available. The contribution snake has its own existing daily workflow and output branch.
