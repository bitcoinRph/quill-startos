import { storeJson } from '../fileModels/store.json'
import { i18n } from '../i18n'
import { sdk } from '../sdk'
import { defaultSpeakerName } from '../utils'

const { InputSpec, Value } = sdk

export const inputSpec = InputSpec.of({
  name: Value.text({
    name: i18n('Your name'),
    description: i18n(
      'Labels your own voice in browser recordings and shows as the owner of every note in the Granola-compatible API. The bot also treats a participant with this exact name as you.',
    ),
    required: true,
    default: defaultSpeakerName,
    minLength: 1,
    maxLength: 60,
  }),
  email: Value.text({
    name: i18n('Your email'),
    description: i18n(
      'Shown as the note owner to Granola-compatible tools. Leave empty if you do not need it.',
    ),
    required: false,
    default: null,
    inputmode: 'email',
    maxLength: 254,
    patterns: [
      {
        regex: '^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$',
        description: i18n('Must be an email address'),
      },
    ],
  }),
})

export const configureProfile = sdk.Action.withInput(
  'configure-profile',

  async () => ({
    name: i18n('Configure Profile'),
    description: i18n(
      'Set your name and email. Quill uses them to label your voice and to identify the owner of notes.',
    ),
    warning: null,
    allowedStatuses: 'any',
    group: i18n('Configuration'),
    visibility: 'enabled',
  }),

  inputSpec,

  async () => {
    const p = await storeJson.read((s) => s.profile).once()
    return p ? { name: p.name, email: p.email || null } : null
  },

  async ({ effects, input }) => {
    await storeJson.merge(effects, {
      profile: {
        name: input.name.trim(),
        email: input.email?.trim() ?? '',
      },
    })
  },
)
