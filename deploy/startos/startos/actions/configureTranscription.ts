import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'
import {
  defaultOllamaAsrModel,
  defaultRemoteAsrModel,
  httpUrlPattern,
  modelIdPattern,
} from '../utils'

const { InputSpec, Value, Variants } = sdk

const remoteModel = Value.text({
  name: i18n('Model name'),
  description: i18n(
    'The model field sent with each request. Most servers ignore it.',
  ),
  required: true,
  default: defaultRemoteAsrModel,
  maxLength: 100,
})

const engineVariants = Variants.of({
  'parakeet-v3': {
    name: i18n('Built-in Parakeet v3 (25 European languages)'),
    spec: InputSpec.of({}),
  },
  'parakeet-v2': {
    name: i18n('Built-in Parakeet v2 (English only)'),
    spec: InputSpec.of({}),
  },
  openwhispr: {
    name: i18n('OpenWhispr service on this server (99 languages)'),
    spec: InputSpec.of({
      token: Value.text({
        name: i18n('OpenWhispr API token'),
        description: i18n(
          'Run Get API Credentials in the OpenWhispr service and paste its API token here. Quill cannot read it from another service.',
        ),
        required: true,
        default: null,
        masked: true,
      }),
      model: remoteModel,
    }),
  },
  ollama: {
    name: i18n('Ollama service on this server (experimental)'),
    spec: InputSpec.of({
      model: Value.text({
        name: i18n('Model ID'),
        description: i18n(
          'An audio-capable model pulled in the Ollama service, for example gemma4:e2b or gemma4:e4b. Ollama returns text without word timings, and a language model can reword or invent speech: check important transcripts.',
        ),
        required: true,
        default: defaultOllamaAsrModel,
        maxLength: 200,
        patterns: [
          {
            regex: modelIdPattern,
            description: i18n(
              'Must start with a letter or number and contain only letters, numbers, dots, underscores, colons, slashes and hyphens.',
            ),
          },
        ],
      }),
    }),
  },
  custom: {
    name: i18n('Custom OpenAI-compatible server'),
    spec: InputSpec.of({
      url: Value.text({
        name: i18n('Server URL'),
        description: i18n(
          'Base URL of a server that implements /v1/audio/transcriptions, for example http://192.168.1.20:8000',
        ),
        required: true,
        default: null,
        inputmode: 'url',
        patterns: [
          {
            regex: httpUrlPattern,
            description: i18n('Must start with http:// or https://'),
          },
        ],
      }),
      key: Value.text({
        name: i18n('API key'),
        description: i18n('Sent as a Bearer token. Leave empty if none.'),
        required: false,
        default: null,
        masked: true,
      }),
      model: remoteModel,
    }),
  },
})

export const inputSpec = InputSpec.of({
  engine: Value.union({
    name: i18n('Speech engine'),
    description: i18n(
      'The built-in engines run on this server and download their model on first start (about 490 MB). Changing the engine restarts Quill.',
    ),
    default: 'parakeet-v3',
    variants: engineVariants,
  }),
  threads: Value.number({
    name: i18n('CPU threads'),
    description: i18n(
      'Threads for the built-in engines. 0 chooses automatically: the smaller of 4 and the number of CPUs.',
    ),
    required: true,
    default: 0,
    min: 0,
    max: 64,
    integer: true,
  }),
})

export const configureTranscription = sdk.Action.withInput(
  'configure-transcription',

  async () => ({
    name: i18n('Configure Transcription'),
    description: i18n(
      'Choose the speech engine: built-in Parakeet, the OpenWhispr service, the Ollama service (experimental), or another OpenAI-compatible server.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: i18n('Configuration'),
    visibility: 'enabled',
  }),

  inputSpec,

  async () => {
    const t = await storeJson.read((s) => s.transcription).once()
    if (!t) return null
    const engine =
      t.engine === 'openwhispr'
        ? { selection: t.engine, value: t.openwhispr }
        : t.engine === 'ollama'
          ? { selection: t.engine, value: { model: t.ollama.model } }
          : t.engine === 'custom'
            ? {
                selection: t.engine,
                value: {
                  url: t.custom.url,
                  key: t.custom.key || null,
                  model: t.custom.model,
                },
              }
            : { selection: t.engine, value: {} }
    return { engine, threads: t.threads }
  },

  async ({ effects, input }) => {
    const { engine, threads } = input
    const base = { threads }

    if (engine.selection === 'openwhispr') {
      await storeJson.merge(effects, {
        transcription: {
          ...base,
          engine: 'openwhispr',
          openwhispr: {
            token: engine.value.token.trim(),
            model: engine.value.model.trim(),
          },
        },
      })
    } else if (engine.selection === 'ollama') {
      await storeJson.merge(effects, {
        transcription: {
          ...base,
          engine: 'ollama',
          ollama: { model: engine.value.model.trim() },
        },
      })
    } else if (engine.selection === 'custom') {
      await storeJson.merge(effects, {
        transcription: {
          ...base,
          engine: 'custom',
          custom: {
            url: engine.value.url.trim(),
            key: engine.value.key?.trim() ?? '',
            model: engine.value.model.trim(),
          },
        },
      })
    } else {
      await storeJson.merge(effects, {
        transcription: { ...base, engine: engine.selection },
      })
    }
  },
)
