# Troubleshooting

The worker retries unfinished notifications every 15 minutes. To retry sooner,
run `release-notifier-worker` with its default empty release parameters.

The latest worker console shows queued releases, posted comments, and the last
error. Check the GitHub API response, token permissions, and commit range shown
in that output.

If a project published successfully but did not submit its notification, run
the project's notification step again with the same version, commits, and
links. Submitting the same request again does not create duplicate comments.

Keep at least ten worker builds. The worker stores its queue in Jenkins build
descriptions, so it does not depend on a particular workspace or agent. Do not
edit descriptions beginning with `jenkins-release-notifier-state:`.
