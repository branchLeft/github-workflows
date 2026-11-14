# image-scan

Scans a container image with Grype over a Syft SBOM, and blocks on any critical or high
finding that no valid exemption covers. It is the image half of `SEC-4` and `SEC-7` and the
SBOM half of `CON-11`. It scans, and does nothing else: it signs nothing, attests nothing and
applies nothing.

## The pieces

| File | Role |
| --- | --- |
| `.github/workflows/image-scan.yml` | The gate. A reusable workflow: checks the image is pinned by digest, scans it, uploads the SBOMs and findings. |
| `.github/actions/image-scan/action.yml` | The mechanism: install Syft and Grype, catalogue, scan, judge. The gate and the self-test both run it. |
| `image-scan.py` | Installs the pinned tools, judges the Grype report, reads the exemptions, writes findings, counts and the verdict. |
| `tool-pins.json` | The Syft and Grype releases and the SHA-256 of each archive. Bumped by a reviewed change. |
| `.github/workflows/image-scan-report.yml` | Manual, report-only counts for the images in `report-images.txt`. Not callable from another repository. |

## The rule

- The image is `name@sha256:<digest>`. A tag is refused, because a tag can move between the scan and the deploy.
- A finding at `critical` or `high` blocks. The threshold is a constant in `image-scan.py`. The reusable workflow has no input for it, for report-only or for the tool versions, so a caller cannot weaken it.
- `medium`, `low`, `negligible` and `unknown` are counted, never blocking.
- A scan that cannot finish fails: a missing or unreadable SBOM or report, a tool that does not install, a download whose SHA-256 differs from its pin. There is no pass on error.
- Grype and Syft run with the config files beside the action, so a `.grype.yaml` or `.syft.yaml` in the scanned repository is never read. Grype reads `.grype.yaml` from the working directory by default, and a package ignore rule there silenced every finding in a local test.
- A severity Grype does not emit (`Critical`, `High`, `Medium`, `Low`, `Negligible`, `Unknown`, spelled so) fails the scan. It is never counted as `unknown`.
- An SBOM with no packages fails (`CON-11`). See "Accepted behaviour".
- The verdict does not depend on the order of the report. If Grype reports one finding twice, once with a fix and once without, it is fixable and no exemption covers it.

## Exemptions (`SEC-5`)

`SEC-5` defines no file format, only its content: the finding, why it cannot be reached, a
30-day expiry, and the platform owner's approval for a critical finding. This is the minimal
consumable format. The default path is `.image-scan-exemptions.json` in the calling repository,
and `CODEOWNERS` on that file is the control that makes the approval real.

```json
{
  "exemptions": [
    {
      "vulnerability": "CVE-2026-12345",
      "package": "libexample",
      "image": "ghcr.io/branchleft/example",
      "reason": "Why the vulnerable code cannot be reached in this image.",
      "granted": "2026-11-09",
      "expires": "2026-12-09",
      "approved-by": "NAME-OR-ROLE, with where the approval is recorded"
    }
  ]
}
```

| Key | Needed | Rule |
| --- | --- | --- |
| `vulnerability` | yes | A CVE or advisory id. It matches the finding's id or any alias Grype lists, ignoring case. |
| `package` | yes | The package name. |
| `image` | yes | The image name without a tag or digest, as the caller passes it to the workflow. `SEC-5` says an exemption names the finding, and a finding is in an image: an exemption without one is refused rather than read as covering every image. |
| `reason` | yes | Not empty, at most 500 characters. |
| `granted`, `expires` | yes | `YYYY-MM-DD`. `granted` is not in the future, and `expires` is at most 30 days after it and not before today. |
| `approved-by` | for a critical finding | Free text, not empty or blank. The script cannot tell who approved or whether it is true: the control is `CODEOWNERS` on this file, so the platform owner reviews every change to it. |

An exemption covers a finding only when the finding has no fix. If a fix exists the exemption is
ignored with a warning and the finding blocks: update instead. Any entry that is malformed,
expired or missing a reason fails the job, even when no finding matches it. An entry that matches
no finding is reported as `info` and does not fail, because one file can serve several images.
A missing file means no exemptions.

## Findings and counts

Each finding is one JSON line `{clause, file, line, level, message}`, the format the standards
tools use. `SEC-4` is a vulnerability (`error`, or `exempt` when covered), `SEC-5` an exemption
fault, `CON-11` an empty SBOM. `file` is the package path inside the image, and `line` is 1.
The job summary has the count per severity, and the step prints a `counts` line and a notice
annotation with the same numbers.

Text from the Grype report is cleaned of quotes, backslashes, percent signs and line breaks
before it is printed, so a package name cannot start a workflow command.

## Outputs

`verdict` (`PASS` or `BLOCK`, unset means block), `blocking`, `exempted`, `critical`, `high`,
`medium`, `low`, `negligible`, `unknown`, `packages`. Counts before exemptions are subtracted,
except `blocking`. The reusable workflow forwards `verdict`, `blocking`, `critical`, `high` and
`sbom-artifact`.

## Accepted behaviour

- **An image with no packages fails by default.** An empty catalogue means nothing was judged, and a catalogue that failed to see anything looks the same as a bare `scratch` image. A fail-closed default costs a bare image one explicit line: the reusable workflow's `allow-empty-sbom: true` makes it a warning. It does not change anything else.
- **One platform is scanned per call.** For a multi-platform image the digest names the index, and Syft picks one image from it: the runner's own platform (`linux/amd64`) unless the optional `platform` input is given (Syft's `--platform`, such as `linux/arm64`; checked against Syft v1.56.0). A caller that publishes several platforms calls the workflow once per platform. The uploaded artifact is named `image-scan-<first 12 hex of the digest>-<platform with / as ->` (`linux-amd64` when no platform is given), so two calls in one run do not collide, and each call's `sbom-artifact` output names its own. The other platforms are not scanned by a call that names none of them.
- **Findings are a day's view.** The Grype database is fetched at scan time, so the same digest can pass today and block tomorrow. That is why `SEC-6` re-scans live images; this gate is the build-time half.

## Limits

- Only `linux-amd64` tools are pinned, so the gate runs on a GitHub-hosted `ubuntu-latest` runner.
- `GITHUB_TOKEN` with `packages: read` reads a ghcr.io package linked to the calling repository, or a public one. A private package of another repository is not readable with it.
- The registry token goes to `ghcr.io` only. Any other registry is pulled anonymously.
- The Grype database is downloaded at scan time and Grype refuses a database older than its own age limit. The result depends on the day, which is the point of `SEC-6`, not a flaw.
- A finding is only as good as Syft's catalogue of the image: a package Syft cannot see is not scanned.
- Lockfile scanning (`SEC-7`), licence checks, signing and attestation are other changes.
- The nightly re-scan (`SEC-6`) is `.github/workflows/nightly-image-rescan.yml`: it runs this action over the digest a tag points at, on the caller's schedule. It files no issue.
- Syft reads the SBOM from the registry by digest, so the image must already be pushed: scan after the push and before the attest.

## Commands (image-scan.py)

`check-input` refuses an image reference that is not pinned by digest, `install`
fetches a pinned, checksum-verified Syft or Grype, `evaluate` turns the Grype JSON into
findings, severity counts and a verdict, and `list-images` reads the manual report's list.
