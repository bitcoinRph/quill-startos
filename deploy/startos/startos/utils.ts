export const uiPort = 8080
export const uiHostId = 'ui'

export const dataDir = '/data'
export const modelsDir = '/models'

export const apiKeyPrefix = 'qk_'
export const secretCharset = 'a-z,A-Z,0-9'

export const defaultAsrModel = 'parakeet-tdt-0.6b-v3-int8'
export const englishAsrModel = 'parakeet-tdt-0.6b-v2-int8'
export const defaultRemoteAsrModel = 'whisper-1'

export const defaultSpeakerName = 'Me'
export const defaultBotName = 'Quill Notetaker'
export const defaultBotMaxMinutes = 240
export const defaultBotAloneMinutes = 3

export const openwhisprPackageId = 'openwhispr'
export const openwhisprHostId = 'ui-multi'
export const openwhisprPort = 8081
export const openwhisprHealthCheck = 'whisper'
export const openwhisprVersionRange = '>=0.1.9:0'

export const ollamaPackageId = 'ollama'
export const ollamaHostId = 'api-multi'
export const ollamaPort = 11434
export const ollamaHealthCheck = 'primary'
export const ollamaVersionRange = '>=0.34.0:0'

export const modelIdPattern = '^[A-Za-z0-9][A-Za-z0-9._:/-]*$'
export const httpUrlPattern = '^https?://[^\\s]+$'
export const hostPattern =
  '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'

export const normalizeHosts = (hosts: string[]): string[] => [
  ...new Set(hosts.map((host) => host.trim().toLowerCase()).filter(Boolean)),
]

export const stripTrailingSlash = (url: string): string =>
  url.trim().replace(/\/+$/, '')

/** https first (the browser recorder needs a secure context), public before LAN. */
export const pickUrl = (preferred: string[], all: string[]): string | null =>
  preferred.find((url) => url.startsWith('https://')) ??
  all.find((url) => url.startsWith('https://')) ??
  preferred[0] ??
  all[0] ??
  null
