import { storeJson } from './fileModels/store.json'
import { i18n } from './i18n'
import { sdk } from './sdk'
import {
  dataDir,
  defaultAsrModel,
  englishAsrModel,
  modelsDir,
  ollamaHostId,
  ollamaPackageId,
  ollamaPort,
  openwhisprHostId,
  openwhisprPackageId,
  openwhisprPort,
  pickUrl,
  stripTrailingSlash,
  uiHostId,
  uiPort,
} from './utils'

const origin = (url: string) => new URL(url).origin

type HealthBody = {
  model_ready?: boolean
  model?: string
  model_download?: { bytes?: number; total?: number } | null
}

const downloadPercent = (body: HealthBody): number | null => {
  const { bytes, total } = body.model_download ?? {}
  if (typeof bytes === 'number' && typeof total === 'number' && total > 0)
    return Math.min(100, Math.floor((bytes / total) * 100))
  return null
}

export const main = sdk.setupMain(async ({ effects }) => {
  const store = await storeJson.read().const(effects)
  if (!store) throw new Error('store.json has not been seeded')

  const urls = (await sdk.host
    .getOwn(effects, uiHostId, (host) => {
      const address = host?.bindings[uiPort]?.interfaces['ui']?.addressInfo
      if (!address) return { preferred: [], all: [] }
      return {
        preferred: address.public.format('urlstring'),
        all: address.nonLocal.format('urlstring'),
      }
    })
    .const()) ?? { preferred: [], all: [] }

  const picked = pickUrl(urls.preferred, urls.all)
  const publicUrl = picked ? origin(picked) : ''

  const { transcription, notes } = store

  const ollamaBridge = () =>
    sdk.host
      .getBridgeAddress(effects, {
        packageId: ollamaPackageId,
        hostId: ollamaHostId,
        internalPort: ollamaPort,
      })
      .const()

  const asr: Record<string, string> = {}
  if (transcription.engine === 'parakeet-v3') {
    asr.QUILL_ASR_ENGINE = 'parakeet'
    asr.QUILL_ASR_MODEL = defaultAsrModel
  } else if (transcription.engine === 'parakeet-v2') {
    asr.QUILL_ASR_ENGINE = 'parakeet'
    asr.QUILL_ASR_MODEL = englishAsrModel
  } else if (transcription.engine === 'openwhispr') {
    const bridge = await sdk.host
      .getBridgeAddress(effects, {
        packageId: openwhisprPackageId,
        hostId: openwhisprHostId,
        internalPort: openwhisprPort,
      })
      .const()
    if (!bridge)
      throw new Error(
        'Transcription is set to OpenWhispr but the OpenWhispr service is not reachable. Install and start OpenWhispr, or choose another engine in Configure Transcription.',
      )
    if (!transcription.openwhispr.token)
      throw new Error(
        'Transcription is set to OpenWhispr but no API token is stored. Run Configure Transcription and paste the token from OpenWhispr, Get API Credentials.',
      )
    asr.QUILL_ASR_ENGINE = 'openai'
    asr.QUILL_REMOTE_ASR_URL = `http://${bridge}`
    asr.QUILL_REMOTE_ASR_KEY = transcription.openwhispr.token
    asr.QUILL_REMOTE_ASR_MODEL = transcription.openwhispr.model
  } else if (transcription.engine === 'ollama') {
    const bridge = await ollamaBridge()
    if (!bridge)
      throw new Error(
        'Transcription is set to Ollama but the Ollama service is not reachable. Install and start Ollama, or choose another engine in Configure Transcription.',
      )
    asr.QUILL_ASR_ENGINE = 'openai'
    asr.QUILL_REMOTE_ASR_URL = `http://${bridge}`
    asr.QUILL_REMOTE_ASR_KEY = ''
    asr.QUILL_REMOTE_ASR_MODEL = transcription.ollama.model
  } else {
    if (!transcription.custom.url)
      throw new Error(
        'Transcription is set to a custom server but no URL is stored. Run Configure Transcription.',
      )
    asr.QUILL_ASR_ENGINE = 'openai'
    asr.QUILL_REMOTE_ASR_URL = stripTrailingSlash(transcription.custom.url)
    asr.QUILL_REMOTE_ASR_KEY = transcription.custom.key
    asr.QUILL_REMOTE_ASR_MODEL = transcription.custom.model
  }

  const llm: Record<string, string> = {}
  if (notes.source === 'ollama') {
    const bridge = await ollamaBridge()
    if (!bridge)
      throw new Error(
        'Notes are set to Ollama but the Ollama service is not reachable. Install and start Ollama, or choose another source in Configure Notes LLM.',
      )
    llm.QUILL_LLM_BASE_URL = `http://${bridge}/v1`
    llm.QUILL_LLM_API_KEY = 'ollama'
    llm.QUILL_LLM_MODEL = notes.ollamaModel
  } else if (notes.source === 'custom') {
    llm.QUILL_LLM_BASE_URL = stripTrailingSlash(notes.custom.baseUrl)
    llm.QUILL_LLM_API_KEY = notes.custom.key
    llm.QUILL_LLM_MODEL = notes.custom.model
  }

  const env: Record<string, string> = {
    QUILL_DATA_DIR: dataDir,
    QUILL_MODELS_DIR: modelsDir,
    QUILL_PORT: String(uiPort),
    QUILL_ADMIN_PASSWORD: store.adminPassword,
    QUILL_API_KEY: store.apiKey,
    QUILL_SESSION_SECRET: store.sessionSecret,
    QUILL_INTERNAL_TOKEN: store.internalToken,
    QUILL_PUBLIC_URL: publicUrl,
    QUILL_ASR_THREADS: String(transcription.threads),
    ...asr,
    ...llm,
    QUILL_AUTO_NOTES: String(notes.autoNotes && notes.source !== 'none'),
    QUILL_JITSI_DOMAINS: store.jitsi.domains.join(','),
    QUILL_BOT_NAME: store.jitsi.botName,
    QUILL_BOT_ANNOUNCE: String(store.jitsi.announce),
    QUILL_BOT_MAX_MINUTES: String(store.jitsi.maxMinutes),
    QUILL_BOT_ALONE_MINUTES: String(store.jitsi.aloneMinutes),
    QUILL_AUDIO_RETENTION_DAYS: String(store.retention.audioDays),
    QUILL_COMPRESS_AUDIO: String(store.retention.compressAudio),
    QUILL_SPEAKER_NAME: store.profile.name,
    QUILL_OWNER_EMAIL: store.profile.email,
  }

  const quillSub = sdk.SubContainer.of(
    effects,
    { imageId: 'quill' },
    sdk.Mounts.of()
      .mountVolume({
        volumeId: 'main',
        subpath: null,
        mountpoint: dataDir,
        readonly: false,
      })
      .mountVolume({
        volumeId: 'models',
        subpath: null,
        mountpoint: modelsDir,
        readonly: false,
      }),
    'quill-sub',
  )

  return sdk.Daemons.of(effects)
    .addDaemon('ui', {
      subcontainer: quillSub,
      exec: {
        command: sdk.useEntrypoint(),
        env,
      },
      ready: {
        display: i18n('Web Interface'),
        gracePeriod: 60_000,
        fn: () =>
          sdk.healthCheck.checkPortListening(effects, uiPort, {
            successMessage: i18n('The web interface is ready'),
            errorMessage: i18n('The web interface is not ready'),
          }),
      },
      requires: [],
    })
    .addHealthCheck('model', {
      ready: {
        display: i18n('Speech Model'),
        gracePeriod: 30_000,
        trigger: sdk.trigger.statusTrigger(30_000, {
          loading: 5_000,
          starting: 5_000,
          failure: 5_000,
        }),
        fn: async () => {
          try {
            const res = await fetch(`http://127.0.0.1:${uiPort}/health`, {
              signal: AbortSignal.timeout(3_000),
            })
            if (!res.ok)
              return {
                result: 'failure',
                message: i18n('The health endpoint answered with an error'),
              }
            const body = (await res.json()) as HealthBody
            if (body.model_ready)
              return {
                result: 'success',
                message: i18n('The speech model ${model} is ready', {
                  model: body.model ?? '',
                }),
              }
            const percent = downloadPercent(body)
            return {
              result: 'loading',
              message:
                percent === null
                  ? i18n(
                      'Downloading the speech model on first start (about 490 MB)',
                    )
                  : i18n('Downloading the speech model: ${percent}%', {
                      percent,
                    }),
            }
          } catch {
            return {
              result: 'failure',
              message: i18n('The health endpoint is not reachable'),
            }
          }
        },
      },
      requires: ['ui'],
    })
})
