def scheduledBuilds = []
def messages = []
def pipelineBinding = new Binding()
pipelineBinding.setVariable('error', { String message -> throw new IllegalArgumentException(message) })
pipelineBinding.setVariable('string', { Map value -> value })
pipelineBinding.setVariable('text', { Map value -> value })
pipelineBinding.setVariable('build', { Map value -> scheduledBuilds << value })
pipelineBinding.setVariable('echo', { String message -> messages << message })

def shell = new GroovyShell(pipelineBinding)
def step = shell.parse(new File('vars/releaseNotifier.groovy'))
step.run()

step.call([
    repository: 'mezz/MezzConfig',
    channel: 'main',
    projectName: 'MezzConfig',
    version: '1.2.3',
    baseCommit: 'a' * 40,
    headCommit: 'b' * 40,
    message: 'Released in 1.2.3.\n\nThanks!',
    releaseLinks: [[label: 'Maven', url: 'https://example.invalid/1.2.3']],
    minecraftVersions: [],
    modLoaders: []
])

assert scheduledBuilds.size() == 1
assert scheduledBuilds[0].job == 'release-notifier-worker'
assert scheduledBuilds[0].wait == false
assert scheduledBuilds[0].quietPeriod == 0
def parameters = scheduledBuilds[0].parameters.collectEntries { [(it.name): it.value] }
assert parameters.RELEASE_REPOSITORY == 'mezz/MezzConfig'
assert parameters.RELEASE_CHANNEL == 'main'
assert parameters.RELEASE_PROJECT_NAME == 'MezzConfig'
assert parameters.RELEASE_VERSION == '1.2.3'
assert parameters.RELEASE_BASE_COMMIT == 'a' * 40
assert parameters.RELEASE_HEAD_COMMIT == 'b' * 40
assert parameters.RELEASE_MESSAGE == 'Released in 1.2.3.\n\nThanks!'
assert parameters.RELEASE_LINK_LABELS == 'Maven'
assert parameters.RELEASE_LINK_URLS == 'https://example.invalid/1.2.3'
assert parameters.RELEASE_ENHANCEMENT_LABELS_PRESENT == 'false'
assert messages.size() == 1

def rejected = false
try {
    step.call([
        repository: 'mezz/MezzConfig',
        channel: 'main',
        projectName: 'MezzConfig',
        version: '1.2.3',
        baseCommit: 'short',
        headCommit: 'b' * 40,
        releaseLinks: []
    ])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('baseCommit')
}
assert rejected
assert scheduledBuilds.size() == 1

rejected = false
try {
    step.call([
        repository: 'mezz/MezzConfig',
        channel: 'main',
        projectName: 'MezzConfig',
        version: '1.2.3',
        baseCommit: 'a' * 40,
        headCommit: 'b' * 40,
        releaseLinks: [[label: 'private', url: 'https://example.invalid/file?access_token=secret']]
    ])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('credential parameters')
}
assert rejected
assert scheduledBuilds.size() == 1

rejected = false
try {
    step.call([
        repository: 'mezz/MezzConfig',
        channel: 'main',
        projectName: 'MezzConfig',
        version: '1.2.3',
        baseCommit: 'a' * 40,
        headCommit: 'b' * 40,
        releaseLinks: [[label: 'private', url: 'https://example.invalid/file?%74oken=secret']]
    ])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('encoded parameter names')
}
assert rejected
assert scheduledBuilds.size() == 1

rejected = false
try {
    step.call([
        repository: 'mezz/MezzConfig',
        channel: 'main',
        projectName: 'MezzConfig',
        version: '1.2.3',
        baseCommit: 'a' * 40,
        headCommit: 'b' * 40,
        releaseLinks: [[
            label: 'Download',
            url: 'https://example.invalid/file',
            token: 'not-allowed'
        ]]
    ])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('unknown fields')
}
assert rejected
assert scheduledBuilds.size() == 1

rejected = false
try {
    step.call([
        repository: 'mezz/MezzConfig',
        channel: 'main',
        projectName: 'MezzConfig',
        version: '1.2.3',
        baseCommit: 'a' * 40,
        headCommit: 'b' * 40,
        releaseLinks: [
            [label: 'Download', url: 'https://example.invalid/file'],
            [label: 'download', url: 'https://example.invalid/file']
        ]
    ])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('duplicates')
}
assert rejected
assert scheduledBuilds.size() == 1

println 'ReleaseNotifierStepTest passed'
