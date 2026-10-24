import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'
import { httpUrlPattern, modelIdPattern } from '../utils'

const { InputSpec, Value, Variants } = sdk

const modelPatterns = [
  {
    regex: modelIdPattern,
    description: i18n(
      'Must start with a letter or number and contain only letters, numbers, dots, underscores, colons, slashes and hyphens.',
    ),
  },
]

const sourceVariants = Variants.of({
  none: {
    name: i18n('None: agents write the notes'),
    spec: InputSpec.of({}),
  },
  ollama: {
    name: i18n('Ollama service on this server'),
    spec: InputSpec.of({
      model: Value.text({
        name: i18n('Model ID'),
        description: i18n(
          'The model tag pulled in the Ollama service, for example qwen3.5:4b. Exact match.',
        ),
        required: true,
        default: null,
        maxLength: 200,
        patterns: modelPatterns,
      }),
    }),
  },
  custom: {
    name: i18n('Custom OpenAI-compatible server'),
    spec: InputSpec.of({
      baseUrl: Value.text({
        name: i18n('Base URL'),
        description: i18n(
          'The /v1 base URL of an OpenAI-compatible chat completions server, for example https://api.openai.com/v1',
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
      model: Value.text({
        name: i18n('Model ID'),
        description: i18n('The model name the server expects.'),
        required: true,
        default: null,
        maxLength: 200,
      }),
    }),
  },
})

export const inputSpec = InputSpec.of({
  source: Value.union({
    name: i18n('Notes LLM'),
    description: i18n(
      'The model Quill calls to write notes on the server. With None, an MCP agent such as Claude Code writes notes and saves them back. A custom server outside this machine receives the transcript.',
    ),
    default: 'none',
    variants: sourceVariants,
  }),
  autoNotes: Value.toggle({
    name: i18n('Write notes automatically'),
    description: i18n(
      'Generate notes as soon as a transcript is ready. Needs an LLM above.',
    ),
    default: false,
  }),
})

export const configureNotesLlm = sdk.Action.withInput(
  'configure-notes-llm',

  async () => ({
    name: i18n('Configure Notes LLM'),
    description: i18n(
      'Choose the model that writes meeting notes on the server: the Ollama service, another OpenAI-compatible server, or none.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: i18n('Configuration'),
    visibility: 'enabled',
  }),

  inputSpec,

  async () => {
    const n = await storeJson.read((s) => s.notes).once()
    if (!n) return null
    const source =
      n.source === 'ollama'
        ? { selection: n.source, value: { model: n.ollamaModel } }
        : n.source === 'custom'
          ? {
              selection: n.source,
              value: {
                baseUrl: n.custom.baseUrl,
                key: n.custom.key || null,
                model: n.custom.model,
              },
            }
          : { selection: n.source, value: {} }
    return { source, autoNotes: n.autoNotes }
  },

  async ({ effects, input }) => {
    const { source, autoNotes } = input

    if (autoNotes && source.selection === 'none')
      throw new Error(i18n('Choose an LLM before turning on automatic notes'))

    if (source.selection === 'ollama') {
      await storeJson.merge(effects, {
        notes: {
          source: 'ollama',
          autoNotes,
          ollamaModel: source.value.model.trim(),
        },
      })
    } else if (source.selection === 'custom') {
      await storeJson.merge(effects, {
        notes: {
          source: 'custom',
          autoNotes,
          custom: {
            baseUrl: source.value.baseUrl.trim(),
            key: source.value.key?.trim() ?? '',
            model: source.value.model.trim(),
          },
        },
      })
    } else {
      await storeJson.merge(effects, {
        notes: { source: 'none', autoNotes },
      })
    }
  },
)
