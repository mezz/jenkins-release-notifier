private String requiredText(Map configuration, String name) {
    def value = configuration[name]
    if (!(value instanceof CharSequence) || value.toString().trim().isEmpty()) {
        error("releaseNotifier: '${name}' is required and must be a non-empty string")
    }
    def normalized = value.toString().trim()
    if (normalized.length() > 256) {
        error("releaseNotifier: '${name}' must be at most 256 characters")
    }
    if (normalized =~ /[\u0000-\u001f\u007f]/) {
        error("releaseNotifier: '${name}' must not contain control characters")
    }
    return normalized
}


private List<String> optionalTextList(Map configuration, String name) {
    def value = configuration[name]
    if (value == null) {
        return []
    }
    if (!(value instanceof List)) {
        error("releaseNotifier: '${name}' must be a list of strings")
    }
    def result = []
    value.eachWithIndex { item, index ->
        if (!(item instanceof CharSequence) || item.toString().trim().isEmpty()) {
            error("releaseNotifier: '${name}[${index}]' must be a non-empty string")
        }
        def normalized = item.toString().trim()
        if (normalized.length() > 100) {
            error("releaseNotifier: '${name}[${index}]' must be at most 100 characters")
        }
        if (normalized =~ /[\u0000-\u001f\u007f]/) {
            error("releaseNotifier: '${name}[${index}]' must not contain control characters")
        }
        result << normalized
    }
    return result
}


private String optionalMessage(Map configuration) {
    if (!configuration.containsKey('message')) {
        return ''
    }
    def value = configuration.message
    if (!(value instanceof CharSequence) || value.toString().trim().isEmpty()) {
        error("releaseNotifier: 'message' must be a non-empty string")
    }
    def normalized = value.toString().replace('\r\n', '\n').replace('\r', '\n').trim()
    if (normalized.length() > 10000) {
        error("releaseNotifier: 'message' must be at most 10000 characters")
    }
    if (normalized =~ /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/) {
        error("releaseNotifier: 'message' must not contain control characters")
    }
    return normalized
}


private String validatedUrl(Object value, int index) {
    if (!(value instanceof CharSequence)) {
        error("releaseNotifier: 'releaseLinks[${index}].url' must be an absolute HTTP or HTTPS URL")
    }
    def text = value.toString().trim()
    if (text.length() > 2048 || !(text ==~ /^https?:\/\/[^\s]+$/)) {
        error("releaseNotifier: 'releaseLinks[${index}].url' must be an absolute HTTP or HTTPS URL")
    }
    if (text ==~ /^https?:\/\/[^\/]*@.*$/) {
        error("releaseNotifier: 'releaseLinks[${index}].url' must not contain credentials")
    }
    if (text =~ /(?i)^https?:\/\/[^\/]*%[0-9a-f]{2}/) {
        error("releaseNotifier: 'releaseLinks[${index}].url' must not contain encoded authority characters")
    }
    def sensitiveNames = [
        'accesskey', 'accesskeyid', 'accesstoken', 'apikey', 'authorization',
        'credential', 'jwt', 'password', 'secret', 'sig', 'signature', 'token'
    ]
    def parameterParts = text.split(/[?&#]/)
    for (int partIndex = 1; partIndex < parameterParts.size(); partIndex++) {
        def equalsIndex = parameterParts[partIndex].indexOf('=')
        if (equalsIndex < 0) {
            continue
        }
        def rawName = parameterParts[partIndex].substring(0, equalsIndex)
        if (rawName =~ /(?i)%[0-9a-f]{2}/) {
            error("releaseNotifier: 'releaseLinks[${index}].url' must not contain encoded parameter names")
        }
        def normalizedName = rawName.toLowerCase().replaceAll(/[^a-z0-9]/, '')
        if (sensitiveNames.contains(normalizedName)) {
            error("releaseNotifier: 'releaseLinks[${index}].url' must not contain credential parameters")
        }
    }
    return text
}


private List<Map<String, String>> releaseLinks(Map configuration) {
    def value = configuration.releaseLinks
    if (!(value instanceof List)) {
        error("releaseNotifier: 'releaseLinks' is required and must be a list")
    }
    def result = []
    def seen = [] as Set
    value.eachWithIndex { item, index ->
        if (!(item instanceof Map)) {
            error("releaseNotifier: 'releaseLinks[${index}]' must contain label and url")
        }
        def unknown = item.keySet().findAll { !(it in ['label', 'url']) }
        if (unknown) {
            error("releaseNotifier: 'releaseLinks[${index}]' has unknown fields: ${unknown.join(', ')}")
        }
        def label = item.label
        if (!(label instanceof CharSequence) || label.toString().trim().isEmpty()) {
            error("releaseNotifier: 'releaseLinks[${index}].label' must be a non-empty string")
        }
        def normalizedLabel = label.toString().trim()
        if (normalizedLabel.length() > 100) {
            error("releaseNotifier: 'releaseLinks[${index}].label' must be at most 100 characters")
        }
        if (normalizedLabel =~ /[\u0000-\u001f\u007f]/) {
            error("releaseNotifier: 'releaseLinks[${index}].label' must not contain control characters")
        }
        def url = validatedUrl(item.url, index)
        def key = "${normalizedLabel.toLowerCase()}\u0000${url}"
        if (!seen.add(key)) {
            error("releaseNotifier: 'releaseLinks' must not contain duplicates")
        }
        result << [label: normalizedLabel, url: url]
    }
    return result
}


def call(Map configuration = [:]) {
    def allowed = [
        'workerJob',
        'repository',
        'channel',
        'projectName',
        'version',
        'baseCommit',
        'headCommit',
        'message',
        'releaseLinks',
        'minecraftVersions',
        'modLoaders',
        'enhancementLabels'
    ]
    def unknown = configuration.keySet().findAll { !allowed.contains(it) }.sort()
    if (unknown) {
        error("releaseNotifier: unknown parameters: ${unknown.join(', ')}")
    }

    def repository = requiredText(configuration, 'repository')
    if (!(repository ==~ /^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?\/[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$/)) {
        error("releaseNotifier: 'repository' must have the form owner/name")
    }
    def baseCommit = requiredText(configuration, 'baseCommit').toLowerCase()
    def headCommit = requiredText(configuration, 'headCommit').toLowerCase()
    if (!(baseCommit ==~ /^[0-9a-f]{40}$/)) {
        error("releaseNotifier: 'baseCommit' must be a full 40-character commit ID")
    }
    if (!(headCommit ==~ /^[0-9a-f]{40}$/)) {
        error("releaseNotifier: 'headCommit' must be a full 40-character commit ID")
    }

    def request = [
        schemaVersion: 1,
        repository: repository,
        channel: requiredText(configuration, 'channel'),
        projectName: requiredText(configuration, 'projectName'),
        version: requiredText(configuration, 'version'),
        baseCommit: baseCommit,
        headCommit: headCommit,
        releaseLinks: releaseLinks(configuration)
    ]
    def message = optionalMessage(configuration)
    if (message) {
        request.message = message
    }
    ['minecraftVersions', 'modLoaders', 'enhancementLabels'].each { name ->
        if (configuration.containsKey(name)) {
            request[name] = optionalTextList(configuration, name)
        }
    }

    def workerJob = configuration.containsKey('workerJob') ?
        requiredText(configuration, 'workerJob') : 'release-notifier-worker'

    def links = request.releaseLinks
    def enhancementLabelsPresent = request.containsKey('enhancementLabels')
    // Jenkins stores these parameters on the worker build.
    // The worker validates and queues them before making any GitHub calls.
    build(
        job: workerJob,
        parameters: [
            string(name: 'RELEASE_REPOSITORY', value: request.repository),
            string(name: 'RELEASE_CHANNEL', value: request.channel),
            string(name: 'RELEASE_PROJECT_NAME', value: request.projectName),
            string(name: 'RELEASE_VERSION', value: request.version),
            string(name: 'RELEASE_BASE_COMMIT', value: request.baseCommit),
            string(name: 'RELEASE_HEAD_COMMIT', value: request.headCommit),
            text(name: 'RELEASE_MESSAGE', value: request.message ?: ''),
            text(name: 'RELEASE_LINK_LABELS', value: links.collect { it.label }.join('\n')),
            text(name: 'RELEASE_LINK_URLS', value: links.collect { it.url }.join('\n')),
            text(name: 'RELEASE_MINECRAFT_VERSIONS', value: (request.minecraftVersions ?: []).join('\n')),
            text(name: 'RELEASE_MOD_LOADERS', value: (request.modLoaders ?: []).join('\n')),
            string(name: 'RELEASE_ENHANCEMENT_LABELS_PRESENT', value: enhancementLabelsPresent.toString()),
            text(name: 'RELEASE_ENHANCEMENT_LABELS', value: (request.enhancementLabels ?: []).join('\n'))
        ],
        wait: false,
        quietPeriod: 0
    )
    echo("Scheduled release notification for ${repository} ${request.version}")
}
