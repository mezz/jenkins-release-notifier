# Set up Jenkins Release Notifier 0.1.0

No global Jenkins library or administrator configuration is required. Create
one worker job, then configure each project from its Jenkinsfile.

## Create the worker job

Create a GitHub token with these repository permissions:

- Contents: read
- Issues: read and write
- Pull requests: read and write

Save it in Jenkins as a **Secret text** credential with the ID:

```text
github-release-notifier-token
```

Create a **Pipeline** job named `release-notifier-worker`. Select
**Pipeline script from SCM** and enter:

| Jenkins setting | Value |
| --- | --- |
| SCM | Git |
| Repository URL | `https://github.com/mezz/jenkins-release-notifier.git` |
| Branch Specifier | `refs/tags/v0.1.0` |
| Script Path | `Jenkinsfile` |

Run the job once to initialize it. The selected agent must have Python 3.11 or
newer. Configure Jenkins to retain at least ten builds for this job.

## Configure a project Jenkinsfile

Add this stage after the project publishes its release:

```groovy
stage('Notify Released Issues') {
    steps {
        script {
            library(
                identifier: 'jenkins-release-notifier@v0.1.0',
                retriever: modernSCM([
                    $class: 'GitSCMSource',
                    remote: 'https://github.com/mezz/jenkins-release-notifier.git'
                ])
            )

            releaseNotifier(
                workerJob: '/release-notifier-worker',
                repository: 'your-name/your-project',
                channel: 'main',
                projectName: 'Your Project',
                version: releaseVersion,
                baseCommit: previousReleaseCommit,
                headCommit: env.GIT_COMMIT,
                message: "Released in ${releaseVersion}. Thanks for contributing!",
                releaseLinks: [
                    [label: 'Download', url: releaseUrl]
                ]
            )
        }
    }
}
```

Change the project name, repository, version, commits, and download links. If
the worker is in a Jenkins folder, use its full path, for example
`/team/release-notifier-worker`.

`baseCommit` is the full commit ID of the previous published release.
`headCommit` is the full commit ID of the release just published. Use a
different `channel` for each branch or release line that advances separately.

`message` is optional Markdown used for every matching pull request and issue.
Release links are appended automatically. Omit it to use the notifier's
default pull-request and issue messages.

The worker retries unfinished notifications every 15 minutes. See
[Troubleshooting](operations.md) if a notification does not complete.
