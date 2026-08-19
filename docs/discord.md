# Discord build notifications

Save the Discord webhook URL on the worker job as a Jenkins **Secret text**
credential with the ID `discord-webhook-url`. Projects do not need access
to that credential or the Jenkins Discord plugin.

Load the library once before the `pipeline` block:

```groovy
library(
    identifier: 'jenkins-release-notifier@v0.2.0',
    retriever: modernSCM([
        $class: 'GitSCMSource',
        remote: 'https://github.com/mezz/jenkins-release-notifier.git'
    ])
)
```

Call `discordNotifier` from the Pipeline's `post` block:

```groovy
post {
    always {
        script {
            discordNotifier(
                workerJob: '/release-notifier-worker',
                projectName: 'Your Project',
                repository: 'your-name/your-project'
            )
        }
    }
}
```

The message includes the result, branch, build number, version, commits, and
release links that are available. Gradle mod projects automatically use values
from `gradle.properties` and output from the Mod Publish Plugin.

Gradle mod projects with `modName` and `githubUrl` in `gradle.properties` can
omit the project metadata. Pass `workerJob` if the worker is in a Jenkins
folder. The same step provides
`getGradleReleaseMetadata()` when `releaseNotifier` needs the project name,
version, Minecraft version, mod loaders, and published links.

The project schedules the message without waiting. The worker saves it in its
queue and retries every 15 minutes until Discord confirms delivery. Discord
webhooks have no idempotency key, so an ambiguous response after Discord
accepts a message can result in a duplicate on retry. An unconfirmed response
remains queued.
