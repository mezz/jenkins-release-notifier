def notifierStatePrefix() {
    return 'jenkins-release-notifier-state:v1:'
}


def findPreviousNotifierState() {
    def previousBuild = currentBuild.previousBuild
    while (previousBuild != null) {
        def description = previousBuild.description ?: ''
        if (description.startsWith(notifierStatePrefix())) {
            return description
        }
        previousBuild = previousBuild.previousBuild
    }
    return ''
}


def persistNotifierState(String stateFile) {
    def description = sh(
        script: "PYTHONPATH=src python3 -m release_notifier describe-state " +
            "--state-file '${stateFile}'",
        returnStdout: true
    ).trim()
    currentBuild.description = description
}


pipeline {
    agent any

    triggers {
        cron('H/15 * * * *')
    }

    options {
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        timeout(time: 30, unit: 'MINUTES')
    }

    parameters {
        string(name: 'RELEASE_REPOSITORY', defaultValue: '', description: 'GitHub owner/name; empty for a retry-only build')
        string(name: 'RELEASE_CHANNEL', defaultValue: '', description: 'Independent ordered release line')
        string(name: 'RELEASE_PROJECT_NAME', defaultValue: '', description: 'Project name used in comments')
        string(name: 'RELEASE_VERSION', defaultValue: '', description: 'Published version')
        string(name: 'RELEASE_BASE_COMMIT', defaultValue: '', description: 'Previous published 40-character commit ID')
        string(name: 'RELEASE_HEAD_COMMIT', defaultValue: '', description: 'Published 40-character commit ID')
        text(name: 'RELEASE_MESSAGE', defaultValue: '', description: 'Optional custom GitHub comment; release links are appended')
        text(name: 'RELEASE_LINK_LABELS', defaultValue: '', description: 'One public link label per line')
        text(name: 'RELEASE_LINK_URLS', defaultValue: '', description: 'One public URL per line')
        text(name: 'RELEASE_MINECRAFT_VERSIONS', defaultValue: '', description: 'Optional; one Minecraft version per line')
        text(name: 'RELEASE_MOD_LOADERS', defaultValue: '', description: 'Optional; one mod loader per line')
        string(name: 'RELEASE_ENHANCEMENT_LABELS_PRESENT', defaultValue: 'false', description: 'Whether enhancement labels were explicitly supplied')
        text(name: 'RELEASE_ENHANCEMENT_LABELS', defaultValue: '', description: 'Optional; one feature-request label per line')
        string(name: 'DISCORD_TITLE', defaultValue: '', description: 'Discord embed title; empty for no new Discord notification')
        text(name: 'DISCORD_DESCRIPTION', defaultValue: '', description: 'Discord embed description')
        string(name: 'DISCORD_FOOTER', defaultValue: '', description: 'Discord embed footer')
        string(name: 'DISCORD_LINK', defaultValue: '', description: 'Jenkins build URL')
        string(name: 'DISCORD_RESULT', defaultValue: '', description: 'Jenkins build result')
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        stage('Restore') {
            steps {
                script {
                    env.NOTIFIER_WORK_DIRECTORY = "build/jenkins/${env.BUILD_NUMBER}"
                    env.NOTIFIER_STATE_FILE = "${env.NOTIFIER_WORK_DIRECTORY}/state.json"
                    env.NOTIFIER_DESCRIPTION_FILE = "${env.NOTIFIER_WORK_DIRECTORY}/description.txt"
                    sh 'mkdir -p "$NOTIFIER_WORK_DIRECTORY"'
                    def previousState = findPreviousNotifierState()
                    if (previousState) {
                        writeFile file: env.NOTIFIER_DESCRIPTION_FILE, text: previousState
                        sh '''
                            PYTHONPATH=src python3 -m release_notifier restore-state \
                              --state-file "$NOTIFIER_STATE_FILE" \
                              --description-file "$NOTIFIER_DESCRIPTION_FILE"
                        '''
                    } else {
                        sh '''
                            PYTHONPATH=src python3 -m release_notifier init \
                              --state-file "$NOTIFIER_STATE_FILE"
                        '''
                    }
                    persistNotifierState(env.NOTIFIER_STATE_FILE)
                }
            }
        }
        stage('Queue Release') {
            when {
                expression { params.RELEASE_REPOSITORY?.trim() }
            }
            steps {
                script {
                    sh '''
                        PYTHONPATH=src python3 -m release_notifier submit-parameters \
                          --state-file "$NOTIFIER_STATE_FILE"
                    '''
                    persistNotifierState(env.NOTIFIER_STATE_FILE)
                }
            }
        }
        stage('Queue Discord') {
            when {
                expression { params.DISCORD_TITLE?.trim() }
            }
            steps {
                script {
                    sh '''
                        PYTHONPATH=src python3 -m release_notifier submit-discord-parameters \
                          --state-file "$NOTIFIER_STATE_FILE"
                    '''
                    persistNotifierState(env.NOTIFIER_STATE_FILE)
                }
            }
        }
        stage('Post Notifications') {
            steps {
                script {
                    def deliveryFailed = false
                    try {
                        def githubPending = sh(
                            script: '''
                                PYTHONPATH=src python3 -m release_notifier has-pending \
                                  --state-file "$NOTIFIER_STATE_FILE" \
                                  --delivery github
                            ''',
                            returnStatus: true
                        ) == 0
                        if (githubPending) {
                            withCredentials([string(
                                credentialsId: 'github-release-comment-token',
                                variable: 'GITHUB_RELEASE_NOTIFIER_TOKEN'
                            )]) {
                                deliveryFailed |= sh(
                                    script: '''
                                        PYTHONPATH=src python3 -m release_notifier process \
                                          --state-file "$NOTIFIER_STATE_FILE" \
                                          --delivery github \
                                          --token-env GITHUB_RELEASE_NOTIFIER_TOKEN
                                    ''',
                                    returnStatus: true
                                ) != 0
                            }
                            persistNotifierState(env.NOTIFIER_STATE_FILE)
                        }

                        def discordPending = sh(
                            script: '''
                                PYTHONPATH=src python3 -m release_notifier has-pending \
                                  --state-file "$NOTIFIER_STATE_FILE" \
                                  --delivery discord
                            ''',
                            returnStatus: true
                        ) == 0
                        if (discordPending) {
                            withCredentials([string(
                                credentialsId: 'discord-webhook-url',
                                variable: 'DISCORD_WEBHOOK_URL'
                            )]) {
                                deliveryFailed |= sh(
                                    script: '''
                                        PYTHONPATH=src python3 -m release_notifier process \
                                          --state-file "$NOTIFIER_STATE_FILE" \
                                          --delivery discord \
                                          --discord-webhook-env DISCORD_WEBHOOK_URL
                                    ''',
                                    returnStatus: true
                                ) != 0
                            }
                            persistNotifierState(env.NOTIFIER_STATE_FILE)
                        }
                    } finally {
                        persistNotifierState(env.NOTIFIER_STATE_FILE)
                        sh '''
                            PYTHONPATH=src python3 -m release_notifier inspect \
                              --state-file "$NOTIFIER_STATE_FILE"
                        '''
                    }
                    if (deliveryFailed) {
                        error('One or more notifications remain queued for retry')
                    }
                }
            }
        }
    }
}
