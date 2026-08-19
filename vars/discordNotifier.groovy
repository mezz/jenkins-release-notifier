private String configuredText(Map configuration, String name) {
    if (!configuration.containsKey(name)) {
        return ''
    }
    def value = configuration[name]
    if (!(value instanceof CharSequence)) {
        throw new IllegalArgumentException("discordNotifier: '${name}' must be a string")
    }
    def normalized = value.toString().trim()
    if (normalized.length() > 2048) {
        throw new IllegalArgumentException("discordNotifier: '${name}' is too long")
    }
    if (normalized =~ /[\u0000-\u001f\u007f]/) {
        throw new IllegalArgumentException("discordNotifier: '${name}' must not contain control characters")
    }
    return normalized
}


private String getGradleProperty(String propertyName) {
    if (!fileExists('gradle.properties')) {
        return ''
    }
    def propertyPrefix = "${propertyName}="
    for (def line in readFile(file: 'gradle.properties').readLines()) {
        def trimmedLine = line.trim()
        if (trimmedLine.startsWith(propertyPrefix)) {
            return trimmedLine.substring(propertyPrefix.length()).trim()
        }
    }
    return ''
}


private String getJsonValue(String jsonText, String propertyName) {
    def key = "\"${propertyName}\""
    def keyIndex = jsonText.indexOf(key)
    if (keyIndex < 0) {
        return ''
    }

    def colonIndex = jsonText.indexOf(':', keyIndex + key.length())
    if (colonIndex < 0) {
        return ''
    }

    def valueStart = colonIndex + 1
    while (valueStart < jsonText.length()) {
        def character = jsonText.substring(valueStart, valueStart + 1)
        if (character != ' ' && character != '\n' && character != '\r' && character != '\t') {
            break
        }
        valueStart++
    }
    if (valueStart >= jsonText.length()) {
        return ''
    }

    if (jsonText.substring(valueStart, valueStart + 1) == '"') {
        valueStart++
        def valueEnd = jsonText.indexOf('"', valueStart)
        return valueEnd < 0 ? '' : jsonText.substring(valueStart, valueEnd)
    }

    def commaIndex = jsonText.indexOf(',', valueStart)
    def braceIndex = jsonText.indexOf('}', valueStart)
    def valueEnd = commaIndex
    if (valueEnd < 0 || (braceIndex >= 0 && braceIndex < valueEnd)) {
        valueEnd = braceIndex
    }
    if (valueEnd < 0) {
        valueEnd = jsonText.length()
    }
    return jsonText.substring(valueStart, valueEnd).trim()
}


private String removeTrailingSlashes(String url) {
    def valueEnd = url.length()
    while (valueEnd > 0 && url.substring(valueEnd - 1, valueEnd) == '/') {
        valueEnd--
    }
    return url.substring(0, valueEnd)
}


private String validatedPublicUrl(Object value, String fieldName) {
    if (!(value instanceof CharSequence)) {
        throw new IllegalArgumentException(
            "discordNotifier: '${fieldName}' must be an HTTP or HTTPS URL"
        )
    }
    def url = value.toString().trim()
    if (url.length() > 2048 || !(url ==~ /^https?:\/\/[^\s]+$/)) {
        throw new IllegalArgumentException(
            "discordNotifier: '${fieldName}' must be an HTTP or HTTPS URL"
        )
    }
    if (url ==~ /^https?:\/\/[^\/]*@.*$/) {
        throw new IllegalArgumentException(
            "discordNotifier: '${fieldName}' must not contain credentials"
        )
    }
    def sensitiveNames = [
        'accesskey', 'accesskeyid', 'accesstoken', 'apikey', 'authorization',
        'credential', 'jwt', 'password', 'secret', 'sig', 'signature', 'token'
    ]
    def parameterParts = url.split(/[?&#]/)
    for (int partIndex = 1; partIndex < parameterParts.size(); partIndex++) {
        def equalsIndex = parameterParts[partIndex].indexOf('=')
        if (equalsIndex < 0) {
            continue
        }
        def rawName = parameterParts[partIndex].substring(0, equalsIndex)
        if (rawName =~ /(?i)%[0-9a-f]{2}/) {
            throw new IllegalArgumentException(
                "discordNotifier: '${fieldName}' must not contain encoded parameter names"
            )
        }
        def normalizedName = rawName.toLowerCase().replaceAll(/[^a-z0-9]/, '')
        if (sensitiveNames.contains(normalizedName)) {
            throw new IllegalArgumentException(
                "discordNotifier: '${fieldName}' must not contain credential parameters"
            )
        }
    }
    return url
}


private String getGithubUrl(Map configuration) {
    def githubUrl = configuredText(configuration, 'githubUrl') ?: getGradleProperty('githubUrl')
    if (!githubUrl) {
        def repository = configuredText(configuration, 'repository')
        if (repository) {
            githubUrl = "https://github.com/${repository}"
        }
    }
    return githubUrl ? removeTrailingSlashes(validatedPublicUrl(githubUrl, 'githubUrl')) : ''
}


private String getCurseHomepageUrl(Map configuration) {
    def value = configuredText(configuration, 'curseHomepageUrl') ?:
        getGradleProperty('curseHomepageUrl')
    return value ? removeTrailingSlashes(validatedPublicUrl(value, 'curseHomepageUrl')) : ''
}


private String getModrinthId(Map configuration) {
    def value = configuredText(configuration, 'modrinthId') ?: getGradleProperty('modrinthId')
    if (value && !(value ==~ /^[A-Za-z0-9_-]+$/)) {
        throw new IllegalArgumentException(
            "discordNotifier: 'modrinthId' contains unsupported characters"
        )
    }
    return value
}


private String getLastPathSegment(String url) {
    def valueEnd = url.length()
    while (valueEnd > 0 && url.substring(valueEnd - 1, valueEnd) == '/') {
        valueEnd--
    }
    def slashIndex = url.lastIndexOf('/', valueEnd - 1)
    return slashIndex < 0 ? url.substring(0, valueEnd) : url.substring(slashIndex + 1, valueEnd)
}


private String getFirstLine(String text) {
    if (!text) {
        return ''
    }
    def valueEnd = text.length()
    def newlineIndex = text.indexOf('\n')
    if (newlineIndex >= 0 && newlineIndex < valueEnd) {
        valueEnd = newlineIndex
    }
    def returnIndex = text.indexOf('\r')
    if (returnIndex >= 0 && returnIndex < valueEnd) {
        valueEnd = returnIndex
    }
    return text.substring(0, valueEnd).trim()
}


private String truncateText(String text, int maxLength) {
    if (!text || text.length() <= maxLength) {
        return text
    }
    if (maxLength <= 1) {
        return text.substring(0, maxLength)
    }
    return "${text.substring(0, maxLength - 1)}…"
}


private String sanitizeDiscordText(String text) {
    return (text ?: '').replace('@', '@\u200B')
}


private String getBuildVersion(Map configuration) {
    if (configuration.containsKey('version')) {
        return configuredText(configuration, 'version')
    }
    def specificationVersion = getGradleProperty('specificationVersion')
    return specificationVersion ? "${specificationVersion}.${env.BUILD_NUMBER}" : ''
}


private List<String> getReleaseLoaders(Map configuration) {
    if (configuration.containsKey('modLoaders')) {
        def value = configuration.modLoaders
        if (!(value instanceof List) || value.any { !(it instanceof CharSequence) }) {
            throw new IllegalArgumentException("discordNotifier: 'modLoaders' must be a list of strings")
        }
        return value.collect { it.toString().trim() }.findAll { it }
    }

    def loaders = []
    if (fileExists('NeoForge/build.gradle') || fileExists('NeoForge/build.gradle.kts')) {
        loaders.add('NeoForge')
    }
    if (fileExists('Fabric/build.gradle') || fileExists('Fabric/build.gradle.kts')) {
        loaders.add('Fabric')
    }
    if (fileExists('Forge/build.gradle') || fileExists('Forge/build.gradle.kts')) {
        loaders.add('Forge')
    }
    if (!loaders && (fileExists('build.gradle') || fileExists('build.gradle.kts'))) {
        loaders.add('Forge')
    }
    return loaders
}


private String validatedLinkUrl(Object value, int index) {
    return validatedPublicUrl(value, "releaseLinks[${index}].url")
}


private List<Map<String, String>> getConfiguredReleaseLinks(Map configuration) {
    def value = configuration.releaseLinks
    if (!(value instanceof List)) {
        throw new IllegalArgumentException("discordNotifier: 'releaseLinks' must be a list")
    }
    def links = []
    value.eachWithIndex { item, index ->
        if (!(item instanceof Map)) {
            throw new IllegalArgumentException(
                "discordNotifier: 'releaseLinks[${index}]' must contain label and url"
            )
        }
        def label = item.label
        if (!(label instanceof CharSequence) || label.toString().trim().isEmpty()) {
            throw new IllegalArgumentException(
                "discordNotifier: 'releaseLinks[${index}].label' must be a non-empty string"
            )
        }
        def unknown = item.keySet().findAll { !(it in ['label', 'url']) }
        if (unknown) {
            throw new IllegalArgumentException(
                "discordNotifier: 'releaseLinks[${index}]' has unknown fields: ${unknown.join(', ')}"
            )
        }
        def normalizedLabel = label.toString().trim()
        if (normalizedLabel.length() > 100 ||
            normalizedLabel =~ /[\u0000-\u001f\u007f]/) {
            throw new IllegalArgumentException(
                "discordNotifier: 'releaseLinks[${index}].label' is invalid"
            )
        }
        def url = validatedLinkUrl(item.url, index)
        links.add([label: normalizedLabel, url: url])
    }
    return links
}


private List<String> getConfiguredReleaseLinkLines(Map configuration) {
    def links = getConfiguredReleaseLinks(configuration).collect { link ->
        "[${sanitizeDiscordText(link.label)}](${link.url})"
    }
    return links ? ["**Downloads:** ${links.join(' | ')}"] : []
}


private List<Map<String, String>> getAutomaticReleaseLinkEntries(
    Map configuration,
    boolean includeFallback
) {
    def resultFiles = [
        [module: 'Forge', file: 'Forge/build/publishMods/publishCurseforge.json'],
        [module: 'Forge', file: 'Forge/build/publishMods/publishModrinth.json'],
        [module: 'Fabric', file: 'Fabric/build/publishMods/publishCurseforge.json'],
        [module: 'Fabric', file: 'Fabric/build/publishMods/publishModrinth.json'],
        [module: 'NeoForge', file: 'NeoForge/build/publishMods/publishCurseforge.json'],
        [module: 'NeoForge', file: 'NeoForge/build/publishMods/publishModrinth.json'],
        [module: 'Forge', file: 'build/publishMods/publishCurseforge.json'],
        [module: 'Forge', file: 'build/publishMods/publishModrinth.json']
    ]

    def loaders = getReleaseLoaders(configuration)
    def curseForgeLinksByModule = [:]
    def modrinthLinksByModule = [:]
    def curseHomepageUrl = getCurseHomepageUrl(configuration)
    def curseProjectSlug = getLastPathSegment(curseHomepageUrl)

    for (def resultFile in resultFiles) {
        if (!loaders.contains(resultFile.module) || !fileExists(resultFile.file)) {
            continue
        }
        def publishResult = readFile(file: resultFile.file)
        def publishType = getJsonValue(publishResult, 'type')
        if (publishType == 'curseforge') {
            def fileId = getJsonValue(publishResult, 'fileId')
            def projectSlug = getJsonValue(publishResult, 'projectSlug') ?: curseProjectSlug
            if (fileId && projectSlug && projectSlug != 'dry-run') {
                curseForgeLinksByModule[resultFile.module] =
                    "https://www.curseforge.com/minecraft/mc-mods/${projectSlug}/files/${fileId}"
            }
        } else if (publishType == 'modrinth') {
            def projectId = getJsonValue(publishResult, 'projectId')
            def versionId = getJsonValue(publishResult, 'id')
            if (projectId && projectId != 'dry-run' && versionId) {
                modrinthLinksByModule[resultFile.module] =
                    "https://modrinth.com/mod/${projectId}/version/${versionId}"
            }
        }
    }

    def entries = []
    for (def loader in loaders) {
        if (curseForgeLinksByModule[loader]) {
            entries.add([
                platform: 'CurseForge',
                loader: loader,
                url: curseForgeLinksByModule[loader]
            ])
        }
    }
    for (def loader in loaders) {
        if (modrinthLinksByModule[loader]) {
            entries.add([
                platform: 'Modrinth',
                loader: loader,
                url: modrinthLinksByModule[loader]
            ])
        }
    }
    if (entries || !includeFallback) {
        return entries
    }

    if (curseHomepageUrl) {
        for (def loader in loaders) {
            entries.add([
                platform: 'CurseForge',
                loader: loader,
                url: "${curseHomepageUrl}/files"
            ])
        }
    }
    def modrinthId = getModrinthId(configuration)
    if (modrinthId) {
        for (def loader in loaders) {
            entries.add([
                platform: 'Modrinth',
                loader: loader,
                url: "https://modrinth.com/mod/${modrinthId}/versions"
            ])
        }
    }
    return entries
}


private List<String> getAutomaticReleaseLinkLines(Map configuration, boolean includeFallback) {
    def entries = getAutomaticReleaseLinkEntries(configuration, includeFallback)
    def result = []
    for (def platform in ['CurseForge', 'Modrinth']) {
        def links = entries.findAll { it.platform == platform }.collect { entry ->
            "[${entry.loader}](${entry.url})"
        }
        if (links) {
            result.add("**${platform}:** ${links.join(' | ')}")
        }
    }
    return result
}


private List<String> getReleaseLinkLines(Map configuration, boolean includeFallback) {
    if (configuration.containsKey('releaseLinks')) {
        return getConfiguredReleaseLinkLines(configuration)
    }
    return getAutomaticReleaseLinkLines(configuration, includeFallback)
}


def getReleaseLinks(Map configuration = [:], boolean includeFallback = true) {
    if (configuration.containsKey('releaseLinks')) {
        return getConfiguredReleaseLinks(configuration)
    }
    return getAutomaticReleaseLinkEntries(configuration, includeFallback).collect { entry ->
        [label: "${entry.platform} (${entry.loader})", url: entry.url]
    }
}


def getGradleReleaseMetadata(Map configuration = [:]) {
    return [
        minecraftVersion: configuredText(configuration, 'minecraftVersion') ?:
            getGradleProperty('minecraftVersion'),
        modLoaders: getReleaseLoaders(configuration),
        projectName: configuredText(configuration, 'projectName') ?: getGradleProperty('modName'),
        releaseLinks: getReleaseLinks(configuration, true),
        version: getBuildVersion(configuration)
    ]
}


private String formatCommitLink(String githubUrl, String commitId, String message) {
    if (!commitId) {
        return ''
    }
    def shortCommit = commitId.length() > 10 ? commitId.substring(0, 10) : commitId
    def subject = sanitizeDiscordText(truncateText(getFirstLine(message), 100))
    return "- [`${shortCommit}`](${githubUrl}/commit/${commitId}) ${subject ?: '(no commit message)'}"
}


private List<String> getCommitLinksFromChangeSets(String githubUrl, int maxCommits) {
    def links = []
    def totalCommits = 0
    for (def changeSet in currentBuild.changeSets) {
        for (def item in changeSet.items) {
            def commitId = item.commitId ?: ''
            if (!commitId) {
                continue
            }
            totalCommits++
            if (links.size() < maxCommits) {
                links.add(formatCommitLink(githubUrl, commitId, item.msg ?: ''))
            }
        }
    }
    if (totalCommits > maxCommits) {
        links.add("- …and ${totalCommits - maxCommits} more")
    }
    return links
}


private List<String> getHeadCommitLink(String githubUrl) {
    def gitLog = sh(script: 'git log -1 --format=%H%x09%s', returnStdout: true).trim()
    if (!gitLog) {
        return []
    }
    def separatorIndex = gitLog.indexOf('\t')
    if (separatorIndex < 0) {
        return [formatCommitLink(githubUrl, gitLog, '')]
    }
    return [formatCommitLink(
        githubUrl,
        gitLog.substring(0, separatorIndex),
        gitLog.substring(separatorIndex + 1)
    )]
}


private List<String> getCommitLinks(Map configuration) {
    def githubUrl = getGithubUrl(configuration)
    if (!githubUrl) {
        return []
    }
    def links = getCommitLinksFromChangeSets(githubUrl, 10)
    return links ?: getHeadCommitLink(githubUrl)
}


private List<String> getCommitLinksSafely(Map configuration) {
    try {
        return getCommitLinks(configuration)
    } catch (Throwable exception) {
        echo("Discord commit collection failed: ${exception.getMessage()}")
        return []
    }
}


private boolean isPublishSkipped(Map configuration) {
    if (!configuration.containsKey('publishSkipped')) {
        return env.SHOULD_PUBLISH == 'false'
    }
    if (!(configuration.publishSkipped instanceof Boolean)) {
        throw new IllegalArgumentException("discordNotifier: 'publishSkipped' must be a boolean")
    }
    return configuration.publishSkipped
}


private void queueDiscordBuild(Map configuration) {
    def allowed = [
        'curseHomepageUrl', 'footer', 'githubUrl', 'minecraftVersion', 'modLoaders',
        'modrinthId', 'projectName', 'publishSkipped', 'releaseLinks', 'repository',
        'result', 'version', 'workerJob'
    ]
    def unknown = configuration.keySet().findAll { !allowed.contains(it) }.sort()
    if (unknown) {
        throw new IllegalArgumentException(
            "discordNotifier: unknown parameters: ${unknown.join(', ')}"
        )
    }
    def buildResult = configuredText(configuration, 'result') ?: currentBuild.currentResult ?: 'SUCCESS'
    if (!(buildResult in ['SUCCESS', 'UNSTABLE', 'FAILURE', 'ABORTED', 'NOT_BUILT'])) {
        throw new IllegalArgumentException('discordNotifier: unsupported build result')
    }
    def branchName = sanitizeDiscordText(env.BRANCH_NAME ?: env.GIT_BRANCH ?: 'unknown')
    def descriptionLines = [
        "**Result:** ${sanitizeDiscordText(buildResult)}",
        "**Branch:** ${branchName}",
        "**Build:** #${env.BUILD_NUMBER}"
    ]

    def buildVersion = getBuildVersion(configuration)
    if (buildVersion) {
        descriptionLines.add("**Version:** ${sanitizeDiscordText(buildVersion)}")
    }
    def commitLinks = getCommitLinksSafely(configuration)
    if (commitLinks) {
        descriptionLines.add("**Commits:**\n${commitLinks.join('\n')}")
    }

    if (isPublishSkipped(configuration)) {
        descriptionLines.add('**Publish:** skipped (no code changes)')
    } else {
        for (def releaseLinkLine in getReleaseLinkLines(configuration, buildResult == 'SUCCESS')) {
            if (releaseLinkLine) {
                descriptionLines.add(releaseLinkLine)
            }
        }
    }

    def projectName = configuredText(configuration, 'projectName') ?: getGradleProperty('modName')
    def footer = configuredText(configuration, 'footer') ?: (projectName ? "${projectName} Jenkins" : 'Jenkins')
    def description = descriptionLines.join('\n')
    def normalizedFooter = sanitizeDiscordText(footer)
    def title = "${sanitizeDiscordText(env.JOB_NAME ?: 'Jenkins')} #${env.BUILD_NUMBER}"
    if (title.length() > 256) {
        throw new IllegalArgumentException('discordNotifier: generated title is too long')
    }
    if (description.length() > 4096) {
        throw new IllegalArgumentException('discordNotifier: generated description is too long')
    }
    if (normalizedFooter.length() > 2048 ||
        title.length() + description.length() + normalizedFooter.length() > 6000) {
        throw new IllegalArgumentException('discordNotifier: generated embed is too long')
    }
    if (!(env.BUILD_URL ==~ /^https?:\/\/[^\s]+$/)) {
        throw new IllegalArgumentException('discordNotifier: BUILD_URL is not an HTTP or HTTPS URL')
    }

    def workerJob = configuration.containsKey('workerJob') ?
        configuredText(configuration, 'workerJob') : 'release-notifier-worker'
    if (!workerJob) {
        throw new IllegalArgumentException(
            "discordNotifier: 'workerJob' must be a non-empty string"
        )
    }
    build(
        job: workerJob,
        parameters: [
            string(name: 'DISCORD_TITLE', value: title),
            text(name: 'DISCORD_DESCRIPTION', value: description),
            string(name: 'DISCORD_FOOTER', value: normalizedFooter),
            string(name: 'DISCORD_LINK', value: env.BUILD_URL),
            string(name: 'DISCORD_RESULT', value: buildResult)
        ],
        wait: false,
        quietPeriod: 0
    )
    echo("Scheduled Discord notification for ${title}")
}


def call(Map configuration = [:]) {
    queueDiscordBuild(configuration)
}
