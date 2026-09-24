# Changelog

## 0.3.0 - 2026-09-24

- Replaces legacy Discord embeds with Components V2 containers, status icons,
  and link buttons for Jenkins builds and published downloads.
- Preserves queued version 1 Discord notifications while new jobs submit the
  structured version 2 notification format.

## 0.2.2 - 2026-09-21

- Uses each channel's saved checkpoint as the release base so successful
  Jenkins builds that did not publish cannot cause later notifications to skip
  or reject a release range.

## 0.2.1 - 2026-09-10

- Skips deleted GitHub targets and targets locked against new comments so one
  permanent delivery failure cannot block later releases in the same channel.

## 0.2.0 - 2026-08-19

- Adds queued Discord build notifications with commit and release links.
- Retries Discord messages every 15 minutes until delivery is confirmed.

## 0.1.1 - 2026-08-19

- Uses the `github-release-comment-token` Jenkins credential.

## 0.1.0 - 2026-08-18

- Initial release.
- Supports configurable GitHub comment messages per project.
