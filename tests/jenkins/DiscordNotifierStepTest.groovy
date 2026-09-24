def scheduledBuilds = []
def messages = []
def shellCalls = []
def projectCommit = '0123456789abcdef0123456789abcdef01234567'
def libraryCommit = 'fedcba9876543210fedcba9876543210fedcba98'
def headCommit = '1234567890abcdef1234567890abcdef12345678'
def files = [
    'gradle.properties': '''
githubUrl=https://github.com/example/ExampleMod
curseHomepageUrl=https://www.curseforge.com/minecraft/mc-mods/example-mod
modrinthId=example123
specificationVersion=30.24.0
minecraftVersion=26.2
modName=Example Mod
''',
    'NeoForge/build.gradle': '',
    'Fabric/build.gradle': '',
    'NeoForge/build/publishMods/publishCurseforge.json':
        '{"type":"curseforge","fileId":"1234","projectSlug":"example-mod"}',
    'Fabric/build/publishMods/publishModrinth.json':
        '{"type":"modrinth","projectId":"example123","id":"abcd"}'
]
def pipelineBinding = new Binding()
pipelineBinding.setVariable('env', [
    BRANCH_NAME: '26.2',
    BUILD_NUMBER: '42',
    BUILD_URL: 'https://ci.example.invalid/job/42/',
    JOB_NAME: 'team/ExampleMod/main',
    SHOULD_PUBLISH: 'true'
])
pipelineBinding.setVariable('currentBuild', [
    currentResult: 'SUCCESS',
    changeSets: [
        [items: [[
            commitId: libraryCommit,
            msg: 'Update the notifier shared library'
        ]]],
        [items: [[
            commitId: projectCommit,
            msg: 'Fix @everyone notification formatting'
        ]]]
    ]
])
pipelineBinding.setVariable('fileExists', { String fileName -> files.containsKey(fileName) })
pipelineBinding.setVariable('readFile', { Map arguments -> files[arguments.file] })
pipelineBinding.setVariable('sh', { Map arguments ->
    shellCalls << arguments
    if (arguments.returnStatus) {
        return arguments.script.contains(projectCommit) ? 0 : 128
    }
    return arguments.returnStdout ? "${headCommit}\tFallback head commit\n" : ''
})
pipelineBinding.setVariable('string', { Map value -> value })
pipelineBinding.setVariable('text', { Map value -> value })
pipelineBinding.setVariable('build', { Map value -> scheduledBuilds << value })
pipelineBinding.setVariable('echo', { String message -> messages << message })

def shell = new GroovyShell(pipelineBinding)
def step = shell.parse(new File('vars/discordNotifier.groovy'))
step.run()

def releaseMetadata = step.getGradleReleaseMetadata()
assert releaseMetadata.minecraftVersion == '26.2'
assert releaseMetadata.modLoaders == ['NeoForge', 'Fabric']
assert releaseMetadata.projectName == 'Example Mod'
assert releaseMetadata.version == '30.24.0.42'
assert releaseMetadata.releaseLinks == [
    [label: 'CurseForge (NeoForge)', url: 'https://www.curseforge.com/minecraft/mc-mods/example-mod/files/1234'],
    [label: 'Modrinth (Fabric)', url: 'https://modrinth.com/mod/example123/version/abcd']
]

step.call([projectName: 'Example Mod', workerJob: '/team/release-notifier-worker'])

assert scheduledBuilds.size() == 1
def scheduled = scheduledBuilds[0]
assert scheduled.job == '/team/release-notifier-worker'
assert scheduled.wait == false
assert scheduled.quietPeriod == 0
def parameters = scheduled.parameters.collectEntries { [(it.name): it.value] }
assert parameters.DISCORD_FOOTER == 'Example Mod Jenkins'
assert parameters.DISCORD_LINK == 'https://ci.example.invalid/job/42/'
assert parameters.DISCORD_RESULT == 'SUCCESS'
assert parameters.DISCORD_TITLE == 'team/ExampleMod/main #42'
assert parameters.DISCORD_DESCRIPTION.contains('**Result:** SUCCESS')
assert parameters.DISCORD_DESCRIPTION.contains('**Version:** 30.24.0.42')
assert parameters.DISCORD_DESCRIPTION.contains('Fix @\u200Beveryone notification formatting')
assert parameters.DISCORD_DESCRIPTION.contains(projectCommit)
assert !parameters.DISCORD_DESCRIPTION.contains(libraryCommit)
assert !parameters.DISCORD_DESCRIPTION.contains('Update the notifier shared library')
assert parameters.DISCORD_LINK_LABELS == 'CurseForge (NeoForge)\nModrinth (Fabric)'
assert parameters.DISCORD_LINK_URLS ==
    'https://www.curseforge.com/minecraft/mc-mods/example-mod/files/1234\n' +
    'https://modrinth.com/mod/example123/version/abcd'

pipelineBinding.getVariable('env').SHOULD_PUBLISH = 'false'
step.call([
    projectName: 'Example Mod',
    releaseLinks: [[label: 'Download', url: 'https://example.invalid/download']]
])
assert scheduledBuilds.size() == 2
parameters = scheduledBuilds[1].parameters.collectEntries { [(it.name): it.value] }
assert parameters.DISCORD_DESCRIPTION.contains('**Publish:** skipped (no code changes)')
assert parameters.DISCORD_LINK_LABELS == ''
assert parameters.DISCORD_LINK_URLS == ''

pipelineBinding.getVariable('env').SHOULD_PUBLISH = 'true'
pipelineBinding.getVariable('currentBuild').changeSets = [[items: [[
    commitId: libraryCommit,
    msg: 'Update the notifier shared library'
]]]]
step.call([
    projectName: 'Example',
    repository: 'example/project',
    version: '1.2.3',
    releaseLinks: [[label: 'Download', url: 'https://example.invalid/download']]
])
assert scheduledBuilds.size() == 3
parameters = scheduledBuilds[2].parameters.collectEntries { [(it.name): it.value] }
assert parameters.DISCORD_DESCRIPTION.contains('**Version:** 1.2.3')
assert !parameters.DISCORD_DESCRIPTION.contains('**Commits:**')
assert parameters.DISCORD_LINK_LABELS == 'Download'
assert parameters.DISCORD_LINK_URLS == 'https://example.invalid/download'
assert shellCalls.count { it.returnStdout } == 0

pipelineBinding.getVariable('currentBuild').changeSets = []
step.call([
    projectName: 'Example',
    repository: 'example/project',
    version: '1.2.4',
    releaseLinks: []
])
assert scheduledBuilds.size() == 4
parameters = scheduledBuilds[3].parameters.collectEntries { [(it.name): it.value] }
assert parameters.DISCORD_DESCRIPTION.contains(headCommit)
assert parameters.DISCORD_DESCRIPTION.contains('Fallback head commit')
assert shellCalls.count { it.returnStdout } == 1

def rejected = false
try {
    step.call([projectName: 'Example Mod', result: 'UNKNOWN'])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('unsupported build result')
}
assert rejected
assert scheduledBuilds.size() == 4

rejected = false
try {
    step.call([
        releaseLinks: [[
            label: 'Private',
            url: 'https://example.invalid/download?token=secret'
        ]]
    ])
} catch (IllegalArgumentException error) {
    rejected = error.message.contains('credential parameters')
}
assert rejected
assert scheduledBuilds.size() == 4
assert messages.last().startsWith('Scheduled Discord notification')

println 'DiscordNotifierStepTest passed'
