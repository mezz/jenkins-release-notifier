# Troubleshooting

The worker retries unfinished GitHub and Discord notifications every 15
minutes. To retry sooner, run `release-notifier-worker` with its default empty
parameters.

The latest worker console shows queued releases, Discord messages, completed
deliveries, and the last error. For GitHub, check the API response, token
permissions, and commit range. For Discord, check that
`discord-webhook-url` still contains a working webhook URL.

If a project published successfully but did not submit its notification, run
the project's notification step again with the same version, commits, and
links. Submitting the same request again does not create duplicate comments.

Discord retries are at-least-once. Discord does not provide an idempotency key
for webhook messages, so an ambiguous response can produce a duplicate. The
notifier leaves an unconfirmed message in the queue.

Keep at least ten worker builds. The worker stores its queue in Jenkins build
descriptions, so it does not depend on a particular workspace or agent. Do not
edit descriptions beginning with `jenkins-release-notifier-state:`.
